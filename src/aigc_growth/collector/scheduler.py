"""衰减式采样调度：平衡数据准确性与接口配额。

发布后 1h → 6h → 24h(T+1) → 72h(T+3) → 7d(T+7) → 30d
之后进入低频归档（每周一次，持续 90 天）。

理由：抖音内容有长尾推荐，单看 T+1 会低估作品；
但全程高频又浪费配额。衰减式采样兼顾准确性与成本。
"""
from __future__ import annotations

from datetime import datetime, timedelta

# (offset_hours, 标签)
SNAPSHOT_SCHEDULE: list[tuple[int, str]] = [
    (1, "1h"),
    (6, "6h"),
    (24, "T+1"),
    (72, "T+3"),
    (168, "T+7"),
    (720, "T+30"),
]
ARCHIVE_INTERVAL_DAYS = 7
ARCHIVE_DURATION_DAYS = 90


def due_snapshots(published_at: datetime, now: datetime | None = None) -> list[str]:
    """给定发布时间，计算当前应执行哪些快照点（幂等：重复调用结果一致）。"""
    now = now or datetime.now()
    elapsed = (now - published_at).total_seconds() / 3600
    due = []
    for offset, label in SNAPSHOT_SCHEDULE:
        if elapsed >= offset:
            due.append(label)
    # 90 天内每周归档
    days = elapsed / 24
    if days > 30 and days <= ARCHIVE_DURATION_DAYS:
        weeks = int(days // ARCHIVE_INTERVAL_DAYS)
        if weeks > 0:
            due.append(f"archive_w{weeks}")
    return due


def next_snapshot_at(published_at: datetime) -> datetime | None:
    """下一次该采样的时间点（Celery beat 定时任务用）。"""
    elapsed = (datetime.now() - published_at).total_seconds() / 3600
    for offset, _ in SNAPSHOT_SCHEDULE:
        if elapsed < offset:
            return published_at + timedelta(hours=offset)
    if elapsed / 24 <= ARCHIVE_DURATION_DAYS:
        return published_at + timedelta(
            days=(int(elapsed / 24 // ARCHIVE_INTERVAL_DAYS) + 1) * ARCHIVE_INTERVAL_DAYS
        )
    return None   # 进入归档结束：不再采样
