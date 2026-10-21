"""阿里云百炼 · 万相数字人（wan2.2-s2v）Provider。

图片 + 音频 → 口型同步视频，音频驱动，与「用自己的文案精确播报」的需求吻合。
计费：480P 0.5 元/秒、720P 0.9 元/秒；新用户 100 秒免费额度。

两个必须处理的硬约束（官方文档）：
1. 单次音频 < 20 秒 —— 超长口播自动分段、逐段提交、最后拼接
2. 图片与音频必须是公网 HTTP(S) URL —— 见 settings.media_public_base
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx

from aigc_growth.avatar import media_io
from aigc_growth.avatar.provider import AvatarTaskResult, AvatarProvider, TaskStatus
from aigc_growth.video import render

logger = logging.getLogger(__name__)

SUBMIT_PATH = "/api/v1/services/aigc/image2video/video-synthesis"
QUERY_PATH = "/api/v1/tasks/{task_id}"
MODEL = "wan2.2-s2v"
MAX_AUDIO_SEC = 19.0          # 官方上限 20 秒，留 1 秒余量

_STATUS_MAP = {
    "PENDING": TaskStatus.PENDING,
    "RUNNING": TaskStatus.PROCESSING,
    "SUCCEEDED": TaskStatus.SUCCESS,
    "FAILED": TaskStatus.FAILED,
    "CANCELED": TaskStatus.FAILED,
    "UNKNOWN": TaskStatus.FAILED,
}


class AliyunWanProvider(AvatarProvider):
    name = "aliyun-wan"

    def __init__(self, api_key: str, base_url: str, resolution: str = "480P",
                 voice_key: str = "female_warm"):
        self._api_key = api_key
        self._base = base_url.rstrip("/")
        self._resolution = resolution
        self._voice_key = voice_key
        self._jobs: dict[str, dict] = {}

    # ---------- 协议 ----------

    def submit(self, *, script: str, avatar_id: str, voice_id: str,
               audio_url: str | None = None, ratio: str = "9:16",
               subtitle: bool = True) -> str:
        task_id = f"wan-{uuid.uuid4().hex[:12]}"
        wd = media_io.work_dir(task_id)

        image_url = media_io.public_url(avatar_id)

        # 音频：外部给了就下载下来核对时长，没给就用同一套 TTS 现场合成
        if audio_url:
            audio_path = (media_io.download(audio_url, wd, name="voiceover")
                          if media_io.is_url(audio_url) else Path(audio_url))
        else:
            audio_path = media_io.synthesize_voice(
                script, wd, voice_id or self._voice_key)

        duration = render.probe_duration(audio_path)
        if duration > MAX_AUDIO_SEC:
            parts = render.split_audio(audio_path, wd / "parts", MAX_AUDIO_SEC)
            logger.info("配音 %.1fs 超过单次上限，切为 %d 段分段提交",
                        duration, len(parts))
        else:
            parts = [audio_path]

        remote_ids = [
            self._create_remote(image_url, media_io.public_url(str(p)))
            for p in parts
        ]
        self._jobs[task_id] = {"remote_ids": remote_ids, "work_dir": wd}
        return task_id

    def query(self, task_id: str) -> AvatarTaskResult:
        job = self._jobs.get(task_id)
        if job is None:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="unknown task")

        states = [self._fetch(rid) for rid in job["remote_ids"]]
        for status, _, err in states:
            if status == TaskStatus.FAILED:
                return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                        error=err)
        if any(status != TaskStatus.SUCCESS for status, _, _ in states):
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.PROCESSING)

        urls = [u for _, u, _ in states if u]
        final = self._merge(urls, job["work_dir"] / "final.mp4")
        return AvatarTaskResult(task_id=task_id, status=TaskStatus.SUCCESS,
                                video_url=str(final))

    # ---------- 远端调用 ----------

    def _create_remote(self, image_url: str, audio_url: str) -> str:
        r = httpx.post(
            self._base + SUBMIT_PATH,
            headers={"Authorization": f"Bearer {self._api_key}",
                     "Content-Type": "application/json",
                     "X-DashScope-Async": "enable"},
            json={
                "model": MODEL,
                "input": {"image_url": image_url, "audio_url": audio_url},
                "parameters": {"resolution": self._resolution},
            },
            timeout=60,
        )
        r.raise_for_status()
        body = r.json()
        out = body.get("output") or {}
        if not out.get("task_id"):
            raise RuntimeError(f"万相提交失败: {body.get('message') or body}")
        return out["task_id"]

    def _fetch(self, remote_id: str) -> tuple[TaskStatus, str | None, str | None]:
        """返回 (状态, video_url, error)。"""
        r = httpx.get(
            self._base + QUERY_PATH.format(task_id=remote_id),
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=30,
        )
        r.raise_for_status()
        out = (r.json().get("output") or {})
        status = _STATUS_MAP.get(out.get("task_status", "UNKNOWN"),
                                 TaskStatus.PROCESSING)
        video_url = (out.get("results") or {}).get("video_url")
        err = None
        if status == TaskStatus.FAILED:
            err = f"{out.get('code') or 'failed'}: {out.get('message') or ''}".strip()
        return status, video_url, err

    def _merge(self, urls: list[str], out_path: Path) -> Path:
        """下载各段成片并按序拼接为完整长视频（长口播场景）。"""
        wd = out_path.parent
        if len(urls) == 1:
            return render.concat_videos(
                [media_io.download(urls[0], wd, name="final_raw")], out_path)
        parts = [
            media_io.download(u, wd / "segments", name=f"seg{i:03d}")
            for i, u in enumerate(urls)
        ]
        return render.concat_videos(parts, out_path)
