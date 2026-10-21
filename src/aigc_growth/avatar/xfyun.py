"""讯飞开放平台 · 数字人视频大模型 Provider。

免费包（个人认证）：300 秒 / 有效期 7 天 / 并发 1 路。

与万相/SadTalker 的形态差异（重要）：
讯飞是「主题 → 服务端自动扩写文案 + 配音 + 渲染」的一站式生成，
不接受外部文案与形象图——script 参数实际作为「创作主题」传入。
因此 avatar_id / voice_id / audio_url 均被忽略。

鉴权：hmac-sha256 签名（APIKey/APISecret/APPID 三件套），
签名原文 = host + date + request-line，拼到 URL query。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from email.utils import formatdate
from urllib.parse import quote, urlparse

import httpx

from aigc_growth.avatar import media_io
from aigc_growth.avatar.provider import AvatarTaskResult, AvatarProvider, TaskStatus

logger = logging.getLogger(__name__)


class XfyunAvatarProvider(AvatarProvider):
    name = "xfyun"

    GENERATE_PATH = "/v1/private/video/generate"
    QUERY_PATH = "/v1/private/video/query"

    def __init__(self, app_id: str, api_key: str, api_secret: str,
                 base_url: str = "https://vms.cn-huadong-1.xf-yun.com",
                 word_count: int = 0):
        self._app_id = app_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._base = base_url.rstrip("/")
        self._word_count = word_count    # 0 = 按文案长度自动

    # ---------- 鉴权 ----------

    def _auth_url(self, path: str) -> str:
        """按讯飞签名规范把 authorization/date/host 拼到 URL query。"""
        host = urlparse(self._base).netloc
        date = formatdate(timeval=time.time(), localtime=False, usegmt=True)
        signature_origin = f"host: {host}\ndate: {date}\nPOST {path} HTTP/1.1"
        signature = base64.b64encode(
            hmac.new(self._api_secret.encode(), signature_origin.encode(),
                     hashlib.sha256).digest()
        ).decode()
        authorization_origin = (
            f'api_key="{self._api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(authorization_origin.encode()).decode()
        return (
            f"{self._base}{path}"
            f"?authorization={quote(authorization)}"
            f"&date={quote(date)}&host={quote(host)}"
        )

    # ---------- 协议 ----------

    def submit(self, *, script: str, avatar_id: str, voice_id: str,
               audio_url: str | None = None, ratio: str = "9:16",
               subtitle: bool = True) -> str:
        # 讯飞是主题驱动：script 作为创作主题，文案由服务端扩写
        avatar: dict = {"prompt": script[:500]}
        word_count = self._word_count or min(300, max(50, len(script)))
        avatar["word_count"] = word_count

        r = httpx.post(
            self._auth_url(self.GENERATE_PATH),
            json={"header": {"app_id": self._app_id},
                  "parameter": {"avatar": avatar}},
            timeout=30,
        )
        r.raise_for_status()
        header = (r.json().get("header") or {})
        if header.get("code") != 0:
            raise RuntimeError(
                f"讯飞任务创建失败: {header.get('code')}: {header.get('message')}"
            )
        return header["task_id"]

    def query(self, task_id: str) -> AvatarTaskResult:
        r = httpx.post(
            self._auth_url(self.QUERY_PATH),
            json={"header": {"app_id": self._app_id, "task_id": task_id}},
            timeout=30,
        )
        r.raise_for_status()
        body = r.json()
        header = body.get("header") or {}

        if header.get("code") != 0:
            return AvatarTaskResult(
                task_id=task_id, status=TaskStatus.FAILED,
                error=f"{header.get('code')}: {header.get('message')}",
            )

        # task_status: 1 已创建 / 2 处理中 / 3,4 完成
        if header.get("task_status", "1") in ("1", "2"):
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.PROCESSING)

        payload = body.get("payload") or {}
        video = payload.get("video")
        if not video:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="任务完成但无视频地址")

        # 下载到本地 output/，避免远端链接过期后前端无法回放
        local = media_io.download(video, media_io.work_dir(task_id), name="final")
        return AvatarTaskResult(task_id=task_id, status=TaskStatus.SUCCESS,
                                video_url=str(local))
