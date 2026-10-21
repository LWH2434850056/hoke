"""视频生产编排（纯逻辑，不依赖 Celery）：TTS 混剪全流程。"""
from __future__ import annotations

import logging
from pathlib import Path

from aigc_growth.video import render, subtitle, tts

logger = logging.getLogger(__name__)

OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "output" / "videos"


def render_with_tts(content_id: str, voiceover: str,
                    voice_key: str = "female_warm") -> str:
    """TTS + 素材混剪：数字人的降级通道，也是默认生产通道。

    口播 → TTS → Whisper 字幕对齐 → FFmpeg 合成 → 封面抽帧。
    """
    work_dir = OUTPUT_ROOT / content_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # 1) 配音
    tts_result = tts.synthesize(voiceover, work_dir, voice_key)

    # 2) 字幕：原始文案直出（零错字），按配音时长做字数加权时间轴
    audio_dur = render.probe_duration(tts_result.audio_path)
    srt = subtitle.srt_from_text(voiceover, audio_dur, work_dir / "subs.srt")

    # 3) 合成（demo 无背景素材，用渐变底；生产从素材库向量检索匹配）
    #    背景时长跟随配音实际时长，避免 -shortest 把长文案截断
    logger.info("配音时长 %.1fs，背景同步", audio_dur)
    background = render.make_background(None, work_dir / "bg.mp4", duration=audio_dur)
    final = render.compose(
        background=background,
        audio=tts_result.audio_path,
        srt=srt,
        out_path=work_dir / "final.mp4",
    )
    render.extract_cover(final, work_dir / "cover.jpg")
    logger.info("成片完成: %s", final)
    return str(final)
