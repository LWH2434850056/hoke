"""平台数据模型：作品指标快照（时序）+ 评论明细。

幂等约定：
- video_snapshot 以 (platform, item_id, stat_date) 唯一键 upsert
- comment 以 (platform, comment_id) 唯一键去重
"""
from sqlalchemy import JSON, BigInteger, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aigc_growth.models.base import Base, TimestampMixin, TraceMixin, new_id


class VideoSnapshot(TraceMixin, Base):
    """作品指标快照：衰减式采样 T+1/T+3/T+7/T+30 各落一行。"""

    __tablename__ = "video_snapshots"
    __table_args__ = (
        UniqueConstraint("platform", "item_id", "stat_date", name="uq_snapshot_daily"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(32), index=True)   # douyin/kuaishou/...
    item_id: Mapped[str] = mapped_column(String(128), index=True)
    stat_date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD

    play_count: Mapped[int] = mapped_column(BigInteger, default=0)   # 作者可见数据，仅官方授权口径
    like_count: Mapped[int] = mapped_column(BigInteger, default=0)
    comment_count: Mapped[int] = mapped_column(BigInteger, default=0)
    share_count: Mapped[int] = mapped_column(BigInteger, default=0)
    download_count: Mapped[int] = mapped_column(BigInteger, default=0)
    avg_play_duration: Mapped[float | None] = mapped_column(Float)   # 秒
    video_status: Mapped[str | None] = mapped_column(String(32))
    metrics_raw: Mapped[dict] = mapped_column(JSON, default=dict)    # 接口原始返回存档

    data_source: Mapped[str] = mapped_column(String(32), default="official")  # official|third_party(估算)


class Comment(TraceMixin, Base):
    """评论明细：LLM 意图打标 → 高意向转线索。脱敏后入库。"""

    __tablename__ = "comments"
    __table_args__ = (
        UniqueConstraint("platform", "comment_id", name="uq_comment_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    platform: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str] = mapped_column(String(128), index=True)
    comment_id: Mapped[str] = mapped_column(String(128), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(128))       # 二级回复

    user_nick_masked: Mapped[str | None] = mapped_column(String(128))  # 脱敏后昵称
    content: Mapped[str] = mapped_column(Text)
    like_count: Mapped[int] = mapped_column(Integer, default=0)

    # LLM 意图打标：high_intent / normal / complaint / spam
    intent_label: Mapped[str | None] = mapped_column(String(32), index=True)
    intent_reason: Mapped[str | None] = mapped_column(Text)
    converted_lead: Mapped[bool] = mapped_column(default=False)      # 是否已推 CRM
