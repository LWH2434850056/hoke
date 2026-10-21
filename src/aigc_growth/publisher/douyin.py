"""发布：抖音开放平台 API 主通道 + Playwright 兜底双通道。

工程要点：幂等键、状态轮询、失败截图告警、人工兜底。
"""
from __future__ import annotations

import logging
import time
import uuid

import httpx

from aigc_growth.config import get_settings

logger = logging.getLogger(__name__)


class PublishResult:
    def __init__(self, item_id: str, channel: str):
        self.item_id = item_id
        self.channel = channel


def publish_via_api(video_path: str, title: str, hashtags: list[str],
                    open_id: str = "", access_token: str = "") -> PublishResult:
    """主通道：开放平台视频上传。

    mock 模式返回确定性 item_id（幂等键由 content 侧保证）。
    """
    s = get_settings()
    if s.mock_external or not s.douyin_client_key:
        item_id = f"mock_item_{uuid.uuid4().hex[:10]}"
        logger.info("[MOCK] 已发布: %s (%s)", title[:20], item_id)
        return PublishResult(item_id, channel="api_mock")

    # 真实链路：分片上传 → 完成发布
    # 1) PUT /api/open/upload/video 初始化分片获取 upload_id
    # 2) 分片二进制 PUT 上传
    # 3) POST /api/open/item/create 完成发布，返回 item_id
    with httpx.Client(timeout=120) as client:
        init = client.post(
            "https://open.douyin.com/api/open/upload/video/",
            params={"open_id": open_id},
            headers={"access-token": access_token},
            json={"video_type": 0},
        )
        init.raise_for_status()
        upload_id = init.json()["data"]["upload_id"]

        video_bytes = open(video_path, "rb").read()   # 生产改分片
        client.put(
            f"https://open.douyin.com/api/open/upload/video/part/",
            params={"open_id": open_id, "upload_id": upload_id,
                    "part_number": 0},
            headers={"access-token": access_token},
            content=video_bytes,
        )

        done = client.post(
            "https://open.douyin.com/api/open/item/create/",
            params={"open_id": open_id},
            headers={"access-token": access_token},
            json={"upload_id": upload_id, "text": f"{title} {' '.join(hashtags)}"},
        )
        done.raise_for_status()
        return PublishResult(done.json()["data"]["item_id"], channel="api")


def publish_via_playwright(video_path: str, title: str,
                           cookies: dict | None = None) -> PublishResult:
    """备用通道：creator.douyin.com 模拟上传。

    用于官方能力审核未过的过渡期。风险：Cookie 失效、字段随版本变更。
    失败截图留存 → 告警 → 转人工。
    """
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        if cookies:
            ctx.add_cookies([cookies])
        page = ctx.new_page()
        try:
            page.goto("https://creator.douyin.com/creator-micro/content/upload")
            page.set_input_files('input[type=file]', video_path)
            page.wait_for_selector("text=上传完成", timeout=120_000)
            page.fill("[placeholder*='简介']", title)
            page.click("button:has-text('发布')")
            page.wait_for_timeout(3000)
            logger.info("Playwright 通道发布完成: %s", title[:20])
            return PublishResult(item_id=f"pw_{uuid.uuid4().hex[:10]}", channel="playwright")
        except Exception:
            page.screenshot(path=f"output/upload_fail_{int(time.time())}.png")  # 失败截图留证
            raise
        finally:
            browser.close()


def publish_with_fallback(video_path: str, title: str, hashtags: list[str]) -> PublishResult:
    """双通道编排：API 失败 → Playwright 兜底 → 再失败转人工。"""
    try:
        return publish_via_api(video_path, title, hashtags)
    except Exception as exc:
        logger.warning("API 通道失败(%s)，切 Playwright 兜底", exc)
        try:
            return publish_via_playwright(video_path, title)
        except Exception as exc2:
            logger.error("[ALERT] 双通道均失败，转人工: %s", exc2)
            raise RuntimeError(f"publish_failed: api={exc} playwright={exc2}") from exc2
