"""硅基智能 Provider 实现。

注意：具体 path 与字段名以官方文档为准，此处为结构骨架——
SUBMIT_PATH / QUERY_PATH 两个常量就是替换位。
"""
from __future__ import annotations

import httpx

from aigc_growth.avatar.provider import AvatarTaskResult, AvatarProvider, TaskStatus


class SiliconIntelligenceProvider(AvatarProvider):
    name = "silicon"

    SUBMIT_PATH = "/video/create"      # <- 按官方文档替换
    QUERY_PATH = "/video/status"       # <- 按官方文档替换

    def __init__(self, api_key: str, base_url: str):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=30)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def submit(
        self,
        *,
        script: str,
        avatar_id: str,
        voice_id: str,
        audio_url: str | None = None,
        ratio: str = "9:16",
        subtitle: bool = True,
    ) -> str:
        payload: dict = {
            "avatar_id": avatar_id,
            "voice_id": voice_id,
            "text": script,
            "ratio": ratio,
            "subtitle": subtitle,
        }
        if audio_url:
            payload["audio_url"] = audio_url
            payload.pop("text", None)          # 音频驱动与文本驱动二选一

        r = self.client.post(
            self.base_url + self.SUBMIT_PATH,
            json=payload,
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()["task_id"]

    def query(self, task_id: str) -> AvatarTaskResult:
        r = self.client.get(
            self.base_url + self.QUERY_PATH,
            params={"task_id": task_id},
            headers=self._headers(),
        )
        r.raise_for_status()
        d = r.json()
        return AvatarTaskResult(
            task_id=task_id,
            status=TaskStatus(d.get("status", "pending")),
            video_url=d.get("video_url"),
            error=d.get("error_msg"),
        )
