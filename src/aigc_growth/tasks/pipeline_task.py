"""全链路 pipeline 任务编排：内容生成 → 渲染 → 终审 → 发布。

状态机：DISCOVER → SCRIPT → RENDER/AVATAR → REVIEW → UPLOAD → PUBLISHED
每个节点落库，支持断点续跑。
"""
from __future__ import annotations

import logging

from aigc_growth.celery_app import celery_app
from aigc_growth.content.graph import run_pipeline
from aigc_growth.content.state_machine import TaskState

logger = logging.getLogger(__name__)


@celery_app.task(name="aigc_growth.tasks.pipeline_task.generate_content")
def generate_content(content_id: str, topic: str) -> dict:
    """内容生成节点：LangGraph 工作流 → 产物落库 → 状态流转。"""
    from aigc_growth.config import get_settings
    from aigc_growth.db import SessionLocal
    from aigc_growth.models.content import ContentTask

    s = get_settings()
    state = run_pipeline(topic)

    with SessionLocal() as db:
        task = db.get(ContentTask, content_id)
        if task is None:
            task = ContentTask(id=content_id)
            db.add(task)
        task.outline = state["outline"].model_dump()
        task.storyboard = state["storyboard"].model_dump()["shots"]
        task.voiceover = state["voiceover"]
        task.titles = state["titles"].model_dump()
        task.judge_score = state["judge_score"]
        task.prompt_version = "v1-geo"
        task.model_version = s.llm_lite_model
        task.transit(TaskState.REVIEW, f"judge={state['judge_score']}")
        db.commit()

    return {"content_id": content_id, "judge_score": state["judge_score"]}


@celery_app.task(name="aigc_growth.tasks.pipeline_task.render_and_publish")
def render_and_publish(content_id: str, avatar_id: str = "", voice_id: str = "female_warm",
                       use_avatar: bool = False) -> dict:
    """渲染节点：数字人通道 或 TTS 混剪通道（自动降级）。"""
    import uuid

    from aigc_growth.db import SessionLocal
    from aigc_growth.models.content import ContentTask
    from aigc_growth.publisher.douyin import publish_with_fallback
    from aigc_growth.tasks.avatar_task import avatar_render
    from aigc_growth.video.pipeline import render_with_tts

    with SessionLocal() as db:
        task = db.get(ContentTask, content_id)
        if task is None or not task.voiceover:
            raise ValueError(f"content={content_id} 无口播产物，先跑 generate_content")
        voiceover = task.voiceover
        title = (task.titles or {}).get("titles", [voiceover[:30]])[0]
        hashtags = (task.titles or {}).get("hashtags", [])
        trace_id = task.trace_id or uuid.uuid4().hex
        task.trace_id = trace_id
        db.commit()

    # 渲染：数字人 or TTS（avatar_render 内部已带降级）
    if use_avatar and avatar_id:
        result = avatar_render.apply(args=[content_id, voiceover, avatar_id, voice_id, trace_id],
                                     queue="cpu_queue").get()
        video_url = result["video_url"]
    else:
        video_url = render_with_tts(content_id, voiceover, voice_id)

    # 终审闸门（生产：REVIEW 状态人工确认后才发；demo 自动过）
    with SessionLocal() as db:
        task = db.get(ContentTask, content_id)
        task.transit(TaskState.UPLOAD, f"video={video_url}")
        db.commit()

    pub = publish_with_fallback(video_url, title, hashtags)

    with SessionLocal() as db:
        task = db.get(ContentTask, content_id)
        task.transit(TaskState.PUBLISHED, f"item_id={pub.item_id} channel={pub.channel}")
        db.commit()

    return {"content_id": content_id, "item_id": pub.item_id,
            "channel": pub.channel, "trace_id": trace_id}
