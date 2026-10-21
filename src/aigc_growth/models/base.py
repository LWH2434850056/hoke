"""模型基类：统一主键、时间戳、trace_id 约定。"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


def new_id() -> str:
    return uuid.uuid4().hex


class TraceMixin:
    """trace_id 全链路归因：从生成到发布到数据回收一路打标。"""

    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
