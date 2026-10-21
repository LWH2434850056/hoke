"""快照幂等入库：upsert 到 video_snapshot。

幂等约定：(platform, item_id, stat_date) 唯一键，
重复采集走 upsert，不产生脏数据。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from aigc_growth.collector.douyin import VideoStats
from aigc_growth.models.metrics import VideoSnapshot


def upsert_snapshot(
    db: Session,
    stats: VideoStats,
    stat_date: str | None = None,
    trace_id: str | None = None,
) -> VideoSnapshot:
    stat_date = stat_date or datetime.now().strftime("%Y-%m-%d")

    values = {
        "platform": "douyin",
        "item_id": stats.item_id,
        "stat_date": stat_date,
        "play_count": stats.play_count,
        "like_count": stats.like_count,
        "comment_count": stats.comment_count,
        "share_count": stats.share_count,
        "download_count": stats.download_count,
        "avg_play_duration": stats.avg_play_duration,
        "video_status": stats.video_status,
        "metrics_raw": stats.raw or {},
        "data_source": stats.data_source,
        "trace_id": trace_id,
    }

    stmt = pg_insert(VideoSnapshot).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_snapshot_daily",
        set_={k: v for k, v in values.items()
              if k not in ("platform", "item_id", "stat_date")},
    )
    db.execute(stmt)
    db.commit()
    return db.query(VideoSnapshot).filter_by(
        platform="douyin", item_id=stats.item_id, stat_date=stat_date
    ).one()
