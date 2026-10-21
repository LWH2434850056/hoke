"""FastAPI 服务接口层。

路由分组：内容任务 / GEO 跟踪 / 数据看板（只读指标）。
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aigc_growth.config import get_settings
from aigc_growth.db import get_db
from aigc_growth.models.content import ContentTask
from aigc_growth.models.geo import GeoSampleRun
from aigc_growth.models.metrics import Comment, VideoSnapshot

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from aigc_growth.db import init_db

    try:
        init_db()   # 开发环境直接建表
    except Exception as exc:  # DB 未就绪/驱动缺失不阻塞服务启动
        import logging

        logging.getLogger(__name__).warning("init_db 跳过: %s", exc)
    yield


app = FastAPI(
    title="AI 数字获客系统",
    description="选题→文案→视频→发布→数据回流→GEO 跟踪 全链路",
    version="0.1.0",
    lifespan=lifespan,
)


# ---------- 前端页面 ----------
from pathlib import Path  # noqa: E402

from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_WEB_DIR = Path(__file__).resolve().parents[1] / "web"
_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "output"
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 成片/封面静态托管：/media/videos/<content_id>/final.mp4
app.mount("/media", StaticFiles(directory=_OUTPUT_DIR), name="media")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(_WEB_DIR / "index.html")


# ---------- 内容任务 ----------

class GenerateReq(BaseModel):
    topic: str
    feedback: str = ""          # 重新生成时的改进意见


class RenderReq(BaseModel):
    voice_id: str = "female_warm"
    use_avatar: bool = False
    avatar_id: str = ""


def _save_generation(db: Session, content_id: str, topic: str, state) -> None:
    from aigc_growth.content.state_machine import TaskState

    task = db.get(ContentTask, content_id)
    if task is None:
        task = ContentTask(id=content_id)
        db.add(task)
    task.title = topic[:50]
    task.outline = state["outline"].model_dump()
    task.storyboard = state["storyboard"].model_dump()["shots"]
    task.voiceover = state["voiceover"]
    task.titles = state["titles"].model_dump()
    task.judge_score = state["judge_score"]
    task.prompt_version = "v1-geo"
    task.model_version = settings.llm_lite_model
    task.transit(TaskState.REVIEW, f"judge={state['judge_score']}")
    db.commit()


def _content_payload(task: ContentTask) -> dict:
    asset = task.assets[0] if task.assets else None
    asset_payload = None
    if asset and asset.url:
        local = str(asset.url)
        # 本地 output/ 下的文件 → /media 可预览 URL
        if str(_OUTPUT_DIR) in local:
            rel = local.replace(str(_OUTPUT_DIR), "").replace("\\", "/").lstrip("/")
            preview = f"/media/{rel}"
            cover = preview.rsplit("/", 1)[0] + "/cover.jpg"
        else:
            preview = cover = None
        asset_payload = {
            "url": asset.url, "preview_url": preview, "cover_url": cover,
            "status": asset.status, "provider": asset.provider,
            "voice_id": asset.voice_id, "avatar_id": asset.avatar_id,
            "fallback_reason": asset.fallback_reason,
        }
    return {
        "id": task.id, "status": task.status, "title": task.title,
        "judge_score": task.judge_score, "trace_id": task.trace_id,
        "history": task.status_history,
        "outline": task.outline, "storyboard": task.storyboard,
        "titles": task.titles, "voiceover": task.voiceover,
        "model_version": task.model_version,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "asset": asset_payload,
    }


@app.post("/api/content/generate", tags=["content"])
def generate(req: GenerateReq, db: Session = Depends(get_db)):
    """第 1 步：生成文案（不渲染不发布）。feedback 非空时为带反馈重新生成。"""
    import uuid

    from aigc_growth.content.graph import run_pipeline

    content_id = uuid.uuid4().hex
    state = run_pipeline(req.topic, feedback=req.feedback or None)
    _save_generation(db, content_id, req.topic, state)
    return _content_payload(db.get(ContentTask, content_id))


@app.post("/api/content/{content_id}/regenerate", tags=["content"])
def regenerate(content_id: str, req: GenerateReq, db: Session = Depends(get_db)):
    """文案不满意 → 带反馈重新生成（复用同一任务，状态回退）。"""
    task = db.get(ContentTask, content_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    from aigc_growth.content.graph import run_pipeline

    state = run_pipeline(task.title or req.topic, feedback=req.feedback or None,
                         prev_voiceover=task.voiceover)
    _save_generation(db, content_id, task.title or req.topic, state)
    return _content_payload(db.get(ContentTask, content_id))


@app.post("/api/content/{content_id}/render", tags=["content"])
def render(content_id: str, req: RenderReq, db: Session = Depends(get_db)):
    """第 2 步：渲染视频（可换音色/数字人重渲）。返回可预览的 /media URL。"""
    task = db.get(ContentTask, content_id)
    if task is None or not task.voiceover:
        raise HTTPException(400, "无口播文案，先生成")
    import uuid

    from aigc_growth.content.state_machine import TaskState
    from aigc_growth.models.content import VideoAsset
    from aigc_growth.video.pipeline import render_with_tts

    task.trace_id = task.trace_id or uuid.uuid4().hex

    if req.use_avatar and req.avatar_id:
        from aigc_growth.tasks.avatar_task import avatar_render

        r = avatar_render.apply(
            args=[content_id, task.voiceover, req.avatar_id, req.voice_id, task.trace_id]
        ).get()
        local_path = r["video_url"]
    else:
        local_path = render_with_tts(content_id, task.voiceover, req.voice_id)
        asset = db.query(VideoAsset).filter_by(content_id=content_id).one_or_none()
        if asset is None:
            asset = VideoAsset(content_id=content_id)
            db.add(asset)
        asset.url = local_path
        asset.status = "done"
        asset.provider = "tts_mixed"
        asset.voice_id = req.voice_id
        asset.trace_id = task.trace_id

    task.transit(TaskState.REVIEW, "rendered")
    db.commit()
    return _content_payload(task)


@app.post("/api/content/{content_id}/publish", tags=["content"])
def publish(content_id: str, db: Session = Depends(get_db)):
    """第 3 步：确认发布（双通道）。mock 下返回假 item_id。"""
    task = db.get(ContentTask, content_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    asset = task.assets[0] if task.assets else None
    if asset is None or not asset.url:
        raise HTTPException(400, "无视频资产，先渲染")

    from aigc_growth.content.state_machine import TaskState
    from aigc_growth.publisher.douyin import publish_with_fallback

    title = (task.titles or {}).get("titles", [task.title or ""])[0]
    hashtags = (task.titles or {}).get("hashtags", [])
    task.transit(TaskState.UPLOAD)
    db.commit()
    pub = publish_with_fallback(asset.url, title, hashtags)
    task.transit(TaskState.PUBLISHED, f"item_id={pub.item_id} channel={pub.channel}")
    db.commit()
    return {**_content_payload(task), "publish": {"item_id": pub.item_id, "channel": pub.channel}}


@app.get("/api/content", tags=["content"])
def list_content(db: Session = Depends(get_db), limit: int = 30):
    """内容列表（视频库页用）。"""
    rows = db.scalars(
        select(ContentTask).order_by(ContentTask.created_at.desc()).limit(limit)
    ).all()
    return [_content_payload(t) for t in rows]


@app.get("/api/content/{content_id}", tags=["content"])
def get_content(content_id: str, db: Session = Depends(get_db)):
    task = db.get(ContentTask, content_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    return _content_payload(task)


# ---------- GEO 跟踪 ----------

@app.post("/api/geo/run", tags=["geo"])
def run_geo(brand: str = "示例品牌A", competitors: str = "竞品X,竞品Y"):
    """触发一轮 GEO 可见度采样。"""
    from aigc_growth.tasks.geo_task import run_sampling

    comp = [c.strip() for c in competitors.split(",") if c.strip()]
    r = run_sampling.apply(args=[brand, comp]).get()
    return r


@app.get("/api/geo/latest", tags=["geo"])
def geo_latest(db: Session = Depends(get_db)):
    """最近一轮采样结果。"""
    run = db.scalar(
        select(GeoSampleRun).order_by(GeoSampleRun.created_at.desc()).limit(1)
    )
    if run is None:
        raise HTTPException(404, "尚无采样记录，先 POST /api/geo/run")
    return {
        "run_id": run.id, "brand": run.brand,
        "mention_rate": run.mention_rate, "citation_rate": run.citation_rate,
        "metrics": run.metrics, "cost": run.cost_total,
        "status": run.status,
    }


# ---------- 数据看板（只读） ----------

@app.get("/api/dashboard/overview", tags=["dashboard"])
def dashboard(db: Session = Depends(get_db)):
    """核心指标总览。播放量口径说明：仅官方授权数据。"""
    total_play = db.scalar(select(func.sum(VideoSnapshot.play_count))) or 0
    total_like = db.scalar(select(func.sum(VideoSnapshot.like_count))) or 0
    total_snapshots = db.scalar(select(func.count(VideoSnapshot.id))) or 0
    total_leads = db.scalar(
        select(func.count(Comment.id)).where(Comment.intent_label == "high_intent")
    ) or 0
    total_contents = db.scalar(select(func.count(ContentTask.id))) or 0
    published = db.scalar(
        select(func.count(ContentTask.id)).where(ContentTask.status == "PUBLISHED")
    ) or 0
    return {
        "contents_total": total_contents,
        "contents_published": published,
        "play_total": total_play,          # 官方授权口径
        "like_total": total_like,
        "snapshots": total_snapshots,
        "high_intent_leads": total_leads,  # 高意向评论线索
    }


@app.get("/api/dashboard/comments", tags=["dashboard"])
def dashboard_comments(db: Session = Depends(get_db), limit: int = 50):
    """评论意图明细。昵称已脱敏、联系方式已打码。"""
    rows = db.scalars(
        select(Comment).order_by(Comment.like_count.desc()).limit(limit)
    ).all()
    return [{
        "comment_id": c.comment_id, "item_id": c.item_id,
        "content": c.content, "nick": c.user_nick_masked,
        "intent": c.intent_label, "reason": c.intent_reason,
        "converted_lead": c.converted_lead,
    } for c in rows]


@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "mock": settings.mock_external}
