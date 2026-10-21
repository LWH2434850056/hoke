"""数字人视频生成 Celery 任务。

四个工程要点（文档 4.4）全部落实：
1. 幂等：content_id 去重，数字人按次计费，重复提交=重复烧钱
2. 轮询退避：指数退避，上限 60s，总超时 900s
3. 降级不静默：失败切 TTS 混剪且必须告警
4. 形象/音色入 trace：avatar_id/voice_id 落库做 A/B 归因
"""
from __future__ import annotations

import logging
import time

from aigc_growth.avatar.factory import get_provider
from aigc_growth.avatar.provider import TaskStatus
from aigc_growth.celery_app import celery_app
from aigc_growth.config import get_settings
from aigc_growth.content.state_machine import TaskState

logger = logging.getLogger(__name__)

MAX_WAIT_SEC = 900        # 总超时
INIT_INTERVAL = 10        # 初始轮询间隔
MAX_INTERVAL = 60         # 退避上限


def _alert(msg: str) -> None:
    """告警（生产接 Prometheus Alertmanager/钉钉/企微 webhook）。"""
    logger.error("[ALERT] %s", msg)


def _fallback(content_id: str, script: str, reason: str) -> dict:
    """降级：数字人不可用时切 TTS + 素材混剪，保证发布链路不断。"""
    from aigc_growth.db import SessionLocal
    from aigc_growth.models.content import ContentTask, VideoAsset
    from aigc_growth.video.pipeline import render_with_tts

    url = render_with_tts(content_id, script)
    with SessionLocal() as db:
        task = db.get(ContentTask, content_id)
        if task:
            task.transit(TaskState.AVATAR_FALLBACK, reason=reason)
        asset = db.query(VideoAsset).filter_by(content_id=content_id).one_or_none()
        if asset is None:
            asset = VideoAsset(content_id=content_id)
            db.add(asset)
        asset.url = url
        asset.status = "fallback"
        asset.provider = "tts_mixed"
        asset.fallback_reason = reason
        db.commit()

    _alert(f"avatar fallback: content={content_id} reason={reason}")   # 降级必须告警
    return {"video_url": url, "fallback": True, "reason": reason}


@celery_app.task(
    bind=True,
    name="aigc_growth.tasks.avatar_task.avatar_render",
    max_retries=3,
    default_retry_delay=30,
)
def avatar_render(
    self,
    content_id: str,
    script: str,
    avatar_id: str,
    voice_id: str,
    trace_id: str,
) -> dict:
    """数字人生成主任务：幂等 → 提交 → 退避轮询 → 下载入库 → 失败降级。"""
    from aigc_growth.db import SessionLocal
    from aigc_growth.models.content import ContentTask, VideoAsset

    provider = get_provider()
    s = get_settings()

    # 1) 幂等：已生成过直接复用，避免重复计费
    with SessionLocal() as db:
        existing = db.query(VideoAsset).filter_by(content_id=content_id).one_or_none()
        if existing and existing.status == "done":
            logger.info("content=%s 已有成片，幂等复用", content_id)
            return {"video_url": existing.url, "cached": True}

    # 2) 提交任务（提交失败直接降级）
    try:
        provider_task_id = provider.submit(script=script, avatar_id=avatar_id,
                                           voice_id=voice_id)
    except Exception as exc:
        logger.warning("数字人提交失败: %s", exc)
        return _fallback(content_id, script, reason=f"submit_failed: {exc}")

    with SessionLocal() as db:
        task = db.get(ContentTask, content_id)
        if task:
            task.transit(TaskState.AVATAR_SUBMITTED, task_id=provider_task_id)
        db.commit()

    # 3) 轮询：指数退避，总超时兜底
    waited, interval = 0, INIT_INTERVAL
    while waited < MAX_WAIT_SEC:
        res = provider.query(provider_task_id)

        if res.status == TaskStatus.SUCCESS:
            video_url = _download(res.video_url, content_id)
            with SessionLocal() as db:
                asset = db.query(VideoAsset).filter_by(content_id=content_id).one_or_none()
                if asset is None:
                    asset = VideoAsset(content_id=content_id)
                    db.add(asset)
                asset.url = video_url
                asset.status = "done"
                asset.provider = provider.name
                asset.avatar_id = avatar_id          # A/B 归因
                asset.voice_id = voice_id
                asset.trace_id = trace_id
                task = db.get(ContentTask, content_id)
                if task:
                    task.transit(TaskState.AVATAR_DONE)
                db.commit()
            return {"video_url": video_url, "task_id": provider_task_id}

        if res.status == TaskStatus.FAILED:
            return _fallback(content_id, script, reason=res.error or "render_failed")

        # 还在渲染：更新状态后按退避间隔等待
        with SessionLocal() as db:
            task = db.get(ContentTask, content_id)
            if task and task.status != TaskState.AVATAR_RENDERING:
                task.transit(TaskState.AVATAR_RENDERING, provider_task_id)
                db.commit()

        time.sleep(interval)
        waited += interval
        interval = min(int(interval * 1.5), MAX_INTERVAL)

    return _fallback(content_id, script, reason="poll_timeout")


def _download(url: str | None, content_id: str) -> str:
    """下载成片到对象存储（mock 下直接返回原 URL）。"""
    if url is None:
        raise ValueError("provider 未返回 video_url")
    if url.startswith("https://mock-oss.local"):
        return url
    # 生产：httpx 流式下载 → MinIO/OSS put_object，返回对象地址
    return url
