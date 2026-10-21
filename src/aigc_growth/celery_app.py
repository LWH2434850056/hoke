"""Celery 应用：双队列资源隔离。

采集(IO 密集) 与 渲染(CPU 密集) 分队列，避免互相阻塞。
"""
from celery import Celery

from aigc_growth.config import get_settings

settings = get_settings()

celery_app = Celery(
    "aigc_growth",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=[
        "aigc_growth.tasks.avatar_task",
        "aigc_growth.tasks.pipeline_task",
        "aigc_growth.tasks.collect_task",
        "aigc_growth.tasks.geo_task",
    ],
)

celery_app.conf.update(
    # 队列定义：io_queue(采集/LLM调用) 与 cpu_queue(FFmpeg渲染)
    task_routes={
        "aigc_growth.tasks.collect_task.*": {"queue": "io_queue"},
        "aigc_growth.tasks.geo_task.*": {"queue": "io_queue"},
        "aigc_growth.tasks.pipeline_task.*": {"queue": "io_queue"},
        "aigc_growth.tasks.avatar_task.*": {"queue": "cpu_queue"},
        "render_with_tts": {"queue": "cpu_queue"},
    },
    task_acks_late=True,          # 任务执行完再确认，worker 崩溃不丢任务
    worker_prefetch_multiplier=1,  # 配合 acks_late 做公平分发
    task_track_started=True,
    result_expires=86400,
    timezone="Asia/Shanghai",
    enable_utc=True,
)
