"""内容任务模型：状态机载体 + 生成产物 + 视频资产。"""
from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aigc_growth.models.base import Base, TimestampMixin, TraceMixin, new_id


class ContentTask(TraceMixin, TimestampMixin, Base):
    """一条内容的主任务，状态机流转记录在此。

    状态见 content.state_machine.TaskState。
    """

    __tablename__ = "content_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    topic_id: Mapped[str | None] = mapped_column(String(32), index=True)
    title: Mapped[str | None] = mapped_column(Text)

    # 生成产物（各节点落库，断点续跑时复用）
    outline: Mapped[dict | None] = mapped_column(JSON)
    storyboard: Mapped[list | None] = mapped_column(JSON)
    voiceover: Mapped[str | None] = mapped_column(Text)
    titles: Mapped[list | None] = mapped_column(JSON)        # 标题/话题/封面文案

    # 模型与 Prompt 版本归因
    model_version: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    judge_score: Mapped[float | None] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(32), default="DISCOVER", index=True)
    status_history: Mapped[list] = mapped_column(JSON, default=list)  # [(状态, 时间, 备注)]
    error_info: Mapped[str | None] = mapped_column(Text)

    assets: Mapped[list["VideoAsset"]] = relationship(back_populates="task")

    def transit(self, new_status: str, note: str = "") -> None:
        """状态流转：记录历史，供断点续跑与归因。"""
        from datetime import datetime, timezone

        self.status = new_status
        hist = list(self.status_history or [])
        hist.append(
            {"status": new_status, "at": datetime.now(timezone.utc).isoformat(), "note": note}
        )
        self.status_history = hist


class VideoAsset(TraceMixin, Base):
    """视频产物资产。avatar_id/voice_id 入库支持形象 A/B 归因。"""

    __tablename__ = "video_assets"
    __table_args__ = (
        UniqueConstraint("content_id", name="uq_video_asset_content"),  # 幂等：一内容一资产
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    content_id: Mapped[str] = mapped_column(
        ForeignKey("content_tasks.id"), index=True, unique=True
    )
    url: Mapped[str | None] = mapped_column(Text)            # OSS/本地路径
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending|done|fallback
    avatar_id: Mapped[str | None] = mapped_column(String(64))   # 数字人形象 → A/B 归因
    voice_id: Mapped[str | None] = mapped_column(String(64))    # 音色 → A/B 归因
    provider: Mapped[str | None] = mapped_column(String(32))    # silicon/mock/tts_mixed
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    fallback_reason: Mapped[str | None] = mapped_column(Text)   # 降级原因，必须留痕

    task: Mapped[ContentTask] = relationship(back_populates="assets")
