"""视频生产 Celery 任务（cpu_queue）：FFmpeg 属 CPU 密集，与 IO 队列隔离。"""
from __future__ import annotations

import logging

from aigc_growth.celery_app import celery_app
from aigc_growth.video.pipeline import render_with_tts

logger = logging.getLogger(__name__)


@celery_app.task(name="aigc_growth.tasks.video_jobs.render_task", bind=True, max_retries=2)
def render_task(self, content_id: str, voiceover: str, voice_key: str = "female_warm") -> dict:
    try:
        url = render_with_tts(content_id, voiceover, voice_key)
        return {"video_url": url}
    except RuntimeError as exc:   # FFmpeg 结构性失败
        logger.error("渲染失败(结构性): %s", exc)
        raise
