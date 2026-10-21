"""GEO 可见度跟踪模型：Prompt 库 + 采样批次 + 单次采样原始存档。"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aigc_growth.models.base import Base, TimestampMixin, new_id


class GeoPrompt(TimestampMixin, Base):
    """Prompt 库：品牌词/品类词/长尾问句/竞品对比词四类。"""

    __tablename__ = "geo_prompts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    prompt: Mapped[str] = mapped_column(Text, unique=True)
    category: Mapped[str] = mapped_column(String(32), index=True)  # brand/category/longtail/competitor
    # 核心词高频跑、长尾词低频抽样（成本控制）
    priority: Mapped[str] = mapped_column(String(16), default="normal")  # core | normal
    enabled: Mapped[bool] = mapped_column(default=True)


class GeoSampleRun(TimestampMixin, Base):
    """一轮采样：prompt × 模型 × N 次。指标统计值存这里。"""

    __tablename__ = "geo_sample_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    brand: Mapped[str] = mapped_column(String(128))
    competitors: Mapped[list] = mapped_column(JSON, default=list)

    mention_rate: Mapped[float] = mapped_column(Float)      # 提及率
    citation_rate: Mapped[float] = mapped_column(Float)      # 引用率
    sov: Mapped[float | None] = mapped_column(Float)         # AI Share of Voice
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)  # 位置分布/竞品共现/信源域名 Counter
    cost_total: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="running")

    samples: Mapped[list["GeoSample"]] = relationship(back_populates="run")


class GeoSample(Base):
    """单次采样原始回答存档——数据可溯源的硬要求。"""

    __tablename__ = "geo_samples"
    __table_args__ = (
        UniqueConstraint("run_id", "engine", "sample_seq", name="uq_sample_once"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("geo_sample_runs.id"), index=True)
    prompt_id: Mapped[str | None] = mapped_column(ForeignKey("geo_prompts.id"))
    engine: Mapped[str] = mapped_column(String(64))          # perplexity-sonar / doubao-web / ...
    sample_seq: Mapped[int] = mapped_column(Integer)

    answer: Mapped[str] = mapped_column(Text)                # 原始回答，不删改
    citations: Mapped[list] = mapped_column(JSON, default=list)
    mentioned: Mapped[bool] = mapped_column(default=False)
    position: Mapped[str | None] = mapped_column(String(16))  # head/mid/tail
    competitors_hit: Mapped[list] = mapped_column(JSON, default=list)
    cost: Mapped[float | None] = mapped_column(Float)
    sampled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default="now()")

    run: Mapped[GeoSampleRun] = relationship(back_populates="samples")
