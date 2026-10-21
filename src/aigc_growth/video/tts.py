"""TTS 配音：edge-tts 真实实现（免费可用）+ 抽象接口。

音色作为可 A/B 测试的变量纳管（与数字人形象同理）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

# 多音色管理：形象/音色与品牌账号绑定，支持 A/B
VOICE_PRESETS: dict[str, str] = {
    "female_warm": "zh-CN-XiaoxiaoNeural",     # 女声-温暖
    "male_pro": "zh-CN-YunxiNeural",           # 男声-专业
    "female_lively": "zh-CN-XiaoyiNeural",     # 女声-活泼
}


@dataclass
class TTSResult:
    audio_path: Path
    voice: str


async def _synthesize(text: str, out_path: Path, voice: str) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(out_path))


def synthesize(text: str, out_dir: Path, voice_key: str = "female_warm") -> TTSResult:
    """文本 → mp3 配音。同步接口，供 Celery 任务调用。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "voiceover.mp3"
    voice = VOICE_PRESETS.get(voice_key, VOICE_PRESETS["female_warm"])
    asyncio.run(_synthesize(text, out_path, voice))
    return TTSResult(audio_path=out_path, voice=voice)
