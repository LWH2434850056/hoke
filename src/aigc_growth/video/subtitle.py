"""Whisper 字幕对齐：配音音频 → 带时间轴的 SRT 字幕。

faster-whisper tiny 模型本地可跑，CPU 即可。
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_MODEL_CACHE: dict = {}


def _get_model(size: str = "tiny"):
    if size not in _MODEL_CACHE:
        from faster_whisper import WhisperModel

        _MODEL_CACHE[size] = WhisperModel(size, device="cpu", compute_type="int8")
    return _MODEL_CACHE[size]


def _fmt_ts(seconds: float) -> str:
    ms = int(seconds * 1000)
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def srt_from_text(voiceover: str, audio_duration: float, srt_path: Path,
                  max_chars: int = 22) -> Path:
    """原始口播文案 → SRT。零错字（不走 ASR），按字数比例分配时间轴。

    TTS 语速均匀，按标点切句 + 字数加权分配时长，对齐精度足够。
    """
    import re

    # 按标点切句，再合并到不超过 max_chars 的显示块
    sentences = [s for s in re.split(r"(?<=[。！？!?；;\n])", voiceover) if s.strip()]
    chunks: list[str] = []
    buf = ""
    for s in sentences:
        s = s.strip()
        if not s:
            continue
        if buf and len(buf) + len(s) > max_chars:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)
    if not chunks:
        chunks = [voiceover.strip()[:max_chars] or "..."]

    # 字数加权分配总时长
    total_chars = sum(len(c) for c in chunks) or 1
    cursor = 0.0
    lines: list[str] = []
    for i, chunk in enumerate(chunks, 1):
        dur = audio_duration * len(chunk) / total_chars
        start, end = cursor, cursor + dur
        cursor = end
        lines += [str(i), f"{_fmt_ts(start)} --> {_fmt_ts(end)}", chunk, ""]

    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("字幕生成(原文案直出): %s (%d 块)", srt_path, len(chunks))
    return srt_path
