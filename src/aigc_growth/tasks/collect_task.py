"""数据采集 Celery 任务（io_queue）：作品指标快照 + 评论同步。

采集 IO 密集 → 独立队列，与渲染 CPU 队列隔离。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aigc_growth.celery_app import celery_app
from aigc_growth.collector.comments import process_comments
from aigc_growth.collector.douyin import DouyinDataClient
from aigc_growth.collector.upsert import upsert_snapshot

logger = logging.getLogger(__name__)


@celery_app.task(name="aigc_growth.tasks.collect_task.snapshot", bind=True, max_retries=3)
def collect_snapshot(self, open_id: str, item_id: str, trace_id: str | None = None) -> dict:
    """单作品指标采集 → 幂等入库。失败分级：限流退避重试/鉴权告警/结构错误入DLQ。"""
    from aigc_growth.db import SessionLocal

    client = DouyinDataClient(access_token=_get_token(open_id))
    stats = client.fetch_video_data(open_id, [item_id])
    if not stats:
        return {"item_id": item_id, "skipped": "privacy_or_missing"}

    with SessionLocal() as db:
        snap = upsert_snapshot(db, stats[0], trace_id=trace_id)
        return {
            "item_id": item_id,
            "stat_date": snap.stat_date,
            "play_count": snap.play_count,
        }


@celery_app.task(name="aigc_growth.tasks.collect_task.sync_comments", bind=True, max_retries=3)
def sync_comments(self, open_id: str, item_id: str) -> dict:
    """评论增量拉取（分页 cursor）→ 打标 → 脱敏入库 → 高意向转线索。"""
    from aigc_growth.db import SessionLocal

    client = DouyinDataClient(access_token=_get_token(open_id))
    totals = {"processed": 0, "new_leads": 0}
    cursor = 0
    for _ in range(10):                    # 单任务最多翻 10 页，防失控
        data = client.fetch_comments(open_id, item_id, cursor=cursor)
        with SessionLocal() as db:
            r = process_comments(db, "douyin", item_id, data.get("list", []))
            totals["processed"] += r["processed"]
            totals["new_leads"] += r["new_leads"]
        if not data.get("has_more"):
            break
        cursor = data.get("cursor", cursor + 1)
    logger.info("评论同步完成 %s: %s", item_id, totals)
    return totals


def _get_token(open_id: str) -> str:
    """取 token：mock 模式返回假 token；生产从 TokenPool 取。"""
    from aigc_growth.config import get_settings

    if get_settings().mock_external:
        return "mock-token"
    from aigc_growth.collector.douyin import TokenPool

    return TokenPool().get(open_id)
