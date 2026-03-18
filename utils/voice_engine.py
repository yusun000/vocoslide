from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


@dataclass(frozen=True)
class VoiceParams:
    speaker: int
    speed_scale: float = 1.0
    pitch_scale: float = 0.0
    intonation_scale: float = 1.0
    volume_scale: float = 1.0
    pre_phoneme_length: float = 0.1
    post_phoneme_length: float = 0.1
    output_sampling_rate: int = 44100


@dataclass
class Segment:
    text: str
    output_path: str
    params: VoiceParams
    index: int
    subtitle: str = ""


class VoicevoxGenerator:
    """
    既存の generate_audio() を保ちつつ、
    - 並列生成
    - 同一実行内の重複排除
    - ディスクキャッシュ
    - キャッシュ削除
    を提供する。
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 50021,
        timeout_sec: int = 120,
        max_workers: int = 1,
        enable_disk_cache: bool = True,
        enable_in_memory_dedup: bool = True,
        cache_dir: str | Path = "temp/audio_cache",
        clear_cache_before_run: bool = False,
        verbose: bool = True,
    ):
        self.base_url = f"http://{host}:{port}"
        self.timeout_sec = timeout_sec
        self.max_workers = max(1, int(max_workers))
        self.enable_disk_cache = enable_disk_cache
        self.enable_in_memory_dedup = enable_in_memory_dedup
        self.cache_dir = Path(cache_dir)
        self.clear_cache_before_run = clear_cache_before_run
        self.verbose = verbose
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._stats: Dict[str, Any] = {}
        self.session = requests.Session()
        self.session.trust_env = False

    def clear_cache(self) -> None:
        if self.cache_dir.exists():
            for item in self.cache_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    import shutil
                    shutil.rmtree(item)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def reset_stats(self) -> None:
        self._stats = {
            "segments": 0,
            "unique_segments": 0,
            "cache_hit": 0,
            "reused_in_run": 0,
            "synthesized": 0,
            "elapsed": 0.0,
            "max_workers": self.max_workers,
            "disk_cache": self.enable_disk_cache,
            "in_memory_dedup": self.enable_in_memory_dedup,
        }

    def get_stats(self) -> Dict[str, Any]:
        return dict(self._stats)

    def generate_audio(self, text: str, output_path: str, speaker_id: int) -> float:
        """既存互換API。単発生成して長さ（秒）を返す。"""
        params = VoiceParams(speaker=speaker_id)
        return self._generate_one(text=text, output_path=output_path, params=params)

    def generate_segments(self, segments: List[Segment]) -> List[Dict[str, Any]]:
        """
        segments の順番に対応する結果を返す。
        各要素: {duration, file, reused, cache_hit, synthesized}
        """
        self.reset_stats()
        self._stats["segments"] = len(segments)

        if self.clear_cache_before_run:
            self.clear_cache()
            self._log("キャッシュを削除しました。")

        started = time.perf_counter()

        # 1. キー付与
        keyed_segments: List[tuple[str, Segment]] = []
        for seg in segments:
            key = self._make_cache_key(seg.text, seg.params)
            keyed_segments.append((key, seg))

        # 2. 実行時重複排除の単位を決定
        jobs: Dict[str, Segment] = {}
        key_counts: Dict[str, int] = {}
        for key, seg in keyed_segments:
            key_counts[key] = key_counts.get(key, 0) + 1
            if self.enable_in_memory_dedup:
                jobs.setdefault(key, seg)
            else:
                jobs[f"{key}__{seg.index}"] = seg

        self._stats["unique_segments"] = len(jobs)
        self._stats["reused_in_run"] = sum(c - 1 for c in key_counts.values() if c > 1) if self.enable_in_memory_dedup else 0

        # 3. ジョブ実行
        job_results: Dict[str, Dict[str, Any]] = {}
        futures = {}
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            for job_key, seg in jobs.items():
                original_key = self._make_cache_key(seg.text, seg.params)
                cache_path = self._get_cache_path(original_key)
                if self.enable_disk_cache and cache_path.exists():
                    duration = self._measure_duration(cache_path)
                    job_results[job_key] = {
                        "source_path": str(cache_path),
                        "duration": duration,
                        "cache_hit": True,
                        "synthesized": False,
                    }
                    self._stats["cache_hit"] += 1
                    continue
                futures[executor.submit(self._synthesize_to_cache_or_output, seg, original_key)] = job_key

            for future in as_completed(futures):
                job_key = futures[future]
                job_results[job_key] = future.result()
                self._stats["synthesized"] += 1

        # 4. 各セグメントへ割り当て
        results: List[Dict[str, Any]] = []
        for key, seg in keyed_segments:
            lookup_key = key if self.enable_in_memory_dedup else f"{key}__{seg.index}"
            info = dict(job_results[lookup_key])
            src = info["source_path"]
            dst = Path(seg.output_path)
            dst.parent.mkdir(parents=True, exist_ok=True)

            # 既に同一ファイルならコピー不要
            if Path(src).resolve() != dst.resolve():
                import shutil
                shutil.copy2(src, dst)

            results.append(
                {
                    "duration": info["duration"],
                    "file": dst.name,
                    "path": str(dst),
                    "reused": info["cache_hit"] or (self.enable_in_memory_dedup and key_counts.get(key, 0) > 1),
                    "cache_hit": info["cache_hit"],
                    "synthesized": info["synthesized"],
                }
            )

        self._stats["elapsed"] = time.perf_counter() - started
        self._log(
            "完了: segments={segments}, unique={unique}, cache_hit={cache_hit}, reused_in_run={reused}, synthesized={synthesized}, workers={workers}, elapsed={elapsed:.2f}s".format(
                segments=self._stats["segments"],
                unique=self._stats["unique_segments"],
                cache_hit=self._stats["cache_hit"],
                reused=self._stats["reused_in_run"],
                synthesized=self._stats["synthesized"],
                workers=self._stats["max_workers"],
                elapsed=self._stats["elapsed"],
            )
        )
        return results

    def _synthesize_to_cache_or_output(self, seg: Segment, original_key: str) -> Dict[str, Any]:
        query = self._create_audio_query(seg.text, seg.params)
        wav_bytes = self._synthesis(query, seg.params)

        if self.enable_disk_cache:
            out_path = self._get_cache_path(original_key)
        else:
            out_path = Path(seg.output_path)

        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        tmp_path.write_bytes(wav_bytes)
        tmp_path.replace(out_path)

        duration = self._measure_duration(out_path)
        return {
            "source_path": str(out_path),
            "duration": duration,
            "cache_hit": False,
            "synthesized": True,
        }

    def _generate_one(self, text: str, output_path: str, params: VoiceParams) -> float:
        query = self._create_audio_query(text, params)
        wav_bytes = self._synthesis(query, params)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(wav_bytes)
        return self._measure_duration(out_path)

    def _create_audio_query(self, text: str, params: VoiceParams) -> Dict[str, Any]:
        query_res = self.session.post(
            f"{self.base_url}/audio_query",
            params={"text": text, "speaker": params.speaker},
            timeout=self.timeout_sec,
        )
        query_res.raise_for_status()
        query = query_res.json()
        query["speedScale"] = params.speed_scale
        query["pitchScale"] = params.pitch_scale
        query["intonationScale"] = params.intonation_scale
        query["volumeScale"] = params.volume_scale
        query["prePhonemeLength"] = params.pre_phoneme_length
        query["postPhonemeLength"] = params.post_phoneme_length
        query["outputSamplingRate"] = params.output_sampling_rate
        return query

    def _synthesis(self, query: Dict[str, Any], params: VoiceParams) -> bytes:
        synth_res = self.session.post(
            f"{self.base_url}/synthesis",
            params={"speaker": params.speaker},
            json=query,
            timeout=self.timeout_sec,
        )
        synth_res.raise_for_status()
        return synth_res.content

    def _get_cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.wav"

    def _make_cache_key(self, text: str, params: VoiceParams) -> str:
        normalized = self._normalize_text(text)
        payload = {
            "text": normalized,
            "speaker": params.speaker,
            "speed_scale": params.speed_scale,
            "pitch_scale": params.pitch_scale,
            "intonation_scale": params.intonation_scale,
            "volume_scale": params.volume_scale,
            "pre_phoneme_length": params.pre_phoneme_length,
            "post_phoneme_length": params.post_phoneme_length,
            "output_sampling_rate": params.output_sampling_rate,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = text.replace("\u3000", " ")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _measure_duration(wav_path: str | Path) -> float:
        with wave.open(str(wav_path), "rb") as wf:
            return wf.getnframes() / wf.getframerate()

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[VoicevoxGenerator] {message}")


def parse_check_notes(file_path):
    """
    check_notes.txt 等のテキストファイルを解析する
    --- PAGE_001 --- という形式のセクションを検出し、
    さらに内容を「//」でパーツ分割して辞書で返す
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"原稿ファイルが見つかりません: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    pages = re.split(r'--- PAGE_(\d+) ---', content)
    page_data = {}
    for i in range(1, len(pages), 2):
        page_num = int(pages[i])
        text = pages[i + 1].strip()
        if text:
            parts = [p.strip() for p in re.split(r'／／|//', text) if p.strip()]
            page_data[page_num] = parts
    return page_data
