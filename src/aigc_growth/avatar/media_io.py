"""数字人 Provider 共用能力：配音合成、素材公网化、素材下载。

各厂商对素材形态要求不一（本地文件 / 公网 URL），差异统一收在这里，
Provider 只负责协议对接。
"""
from __future__ import annotations

import logging
from pathlib import Path

import httpx

from aigc_growth.config import get_settings
from aigc_growth.video import tts

logger = logging.getLogger(__name__)

OUTPUT_ROOT = Path(__file__).resolve().parents[3] / "output"
AVATAR_ROOT = OUTPUT_ROOT / "avatar"


def work_dir(task_id: str) -> Path:
    d = AVATAR_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def synthesize_voice(script: str, out_dir: Path, voice_key: str) -> Path:
    """口播文案 → 配音音频（与 TTS 通道共用同一套音色预设）。"""
    return tts.synthesize(script, out_dir, voice_key).audio_path


def is_url(value: str) -> bool:
    return value.startswith(("http://", "https://"))


def public_url(value: str) -> str:
    """给云端接口准备公网可达的素材 URL。

    已是 URL 直接放行；本地文件需要 MEDIA_PUBLIC_BASE 把 output/ 映射到公网，
    否则直接抛错——静默传本地路径只会换来一个看不懂的远端报错。
    """
    if is_url(value):
        return value
    s = get_settings()
    path = Path(value)
    if not s.media_public_base:
        raise RuntimeError(
            f"云端数字人接口要求素材公网可达，但拿到的是本地路径：{value}。"
            "请把 output/ 暴露到公网（隧道或对象存储）后配置 MEDIA_PUBLIC_BASE，"
            "或直接传入公网 URL。"
        )
    if not path.exists():
        raise FileNotFoundError(f"素材不存在: {value}")
    try:
        rel = path.resolve().relative_to(OUTPUT_ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            f"素材 {value} 不在 output/ 目录下，无法映射为公网 URL"
        ) from exc
    return f"{s.media_public_base.rstrip('/')}/{rel}"


def download(url: str, out_dir: Path, name: str = "download") -> Path:
    """下载远端素材到本地工作目录（保留原扩展名）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        suffix = Path(r.url.path).suffix or ".bin"
        out = out_dir / f"{name}{suffix}"
        with out.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    return out


def source_image(avatar_id: str, out_dir: Path) -> Path:
    """数字人「形象」：本地图直接用，URL 则下载到工作目录。"""
    if not is_url(avatar_id):
        p = Path(avatar_id)
        if not p.exists():
            raise FileNotFoundError(
                f"形象图不存在: {avatar_id}（avatar_id 需为图片路径或公网 URL）"
            )
        return p
    return download(avatar_id, out_dir, name="source_image")
