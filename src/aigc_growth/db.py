"""数据库会话管理：引擎懒加载（无 psycopg 环境下也可导入使用 mock 路径）。"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from aigc_growth.config import get_settings

_engine = None
_session_factory: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url, pool_pre_ping=True, future=True
        )
    return _engine


def SessionLocal() -> Session:
    """取一个数据库会话。用法：`with SessionLocal() as db:`"""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(), autoflush=False, expire_on_commit=False
        )
    return _session_factory()


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖注入用。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """开发环境直接建表；生产用 alembic 迁移。"""
    from aigc_growth.models import base  # noqa: F401  确保模型注册

    base.Base.metadata.create_all(get_engine())
