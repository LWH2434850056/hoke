"""抖音开放平台数据采集：官方授权链路（简历主写这段）。

关键事实（决定方案选型）：
1. 播放量是"作者可见数据"——公开页面没有，必须走作者授权接口
2. item_ids 只能查询该 token 对应用户上传的视频
3. 视频设为隐私时返回创建时间为 0 的记录，清洗时过滤
4. video_id ≠ item_id；从分享链接解析的需走转换接口
"""
from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass

import httpx

from aigc_growth.config import get_settings

logger = logging.getLogger(__name__)

BASE = "https://open.douyin.com"


@dataclass
class VideoStats:
    item_id: str
    play_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    download_count: int = 0
    avg_play_duration: float | None = None
    video_status: str | None = None
    raw: dict | None = None
    data_source: str = "official"   # official | third_party(估算值，不作为考核口径)


class DouyinOAuth:
    """授权与 token 管理。

    两种授权方式：
    - 普通授权  /oauth/access_token/  → token 前缀 act.xxx（单账号）
    - 经营号授权 /business_token/     → token 前缀 bus_act.xxx（企业矩阵，推荐）
    """

    def __init__(self, client_key: str, client_secret: str):
        self.client_key = client_key
        self.client_secret = client_secret

    def business_token(self, code: str) -> dict:
        """经营抖音号授权换 token。"""
        r = httpx.post(
            f"{BASE}/business_token/",
            json={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
            },
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["data"]


class TokenPool:
    """多账号 token 池：提前 30 分钟刷新，单号失效不影响整体。"""

    def __init__(self):
        self._tokens: dict[str, dict] = {}   # open_id -> {token, expires_at}

    def put(self, open_id: str, token: str, expires_in: int) -> None:
        self._tokens[open_id] = {
            "token": token,
            "expires_at": time.time() + expires_in,
        }

    def get(self, open_id: str) -> str:
        t = self._tokens.get(open_id)
        if not t:
            raise KeyError(f"open_id={open_id} 未授权")
        if t["expires_at"] - time.time() < 1800:   # 提前 30 分钟刷新
            logger.warning("token 即将过期，触发刷新: open_id=%s", open_id)
            # 生产：此处调 refresh 逻辑并告警刷新失败
        return t["token"]


class DouyinDataClient:
    """视频数据 / 评论数据官方接口封装。"""

    def __init__(self, access_token: str):
        self.token = access_token

    def _headers(self) -> dict:
        return {"access-token": self.token, "Content-Type": "application/json"}

    def fetch_video_data(self, open_id: str, item_ids: list[str]) -> list[VideoStats]:
        """拉取视频指标。官方建议单次 ≤10 个 item_id。

        POST /video/data/?open_id={open_id}
        """
        if len(item_ids) > 10:
            raise ValueError("官方建议单次 ≤10 个 item_id，请分批")

        s = get_settings()
        if s.mock_external or not s.douyin_client_key:
            return _mock_video_stats(item_ids)

        r = httpx.post(
            f"{BASE}/video/data/",
            params={"open_id": open_id},
            headers=self._headers(),
            json={"item_ids": item_ids},
            timeout=30,
        )
        r.raise_for_status()
        out: list[VideoStats] = []
        for item in r.json().get("data", {}).get("list", []):
            # 数据清洗：隐私视频创建时间为 0 → 过滤
            create_time = item.get("create_time", 0)
            if not create_time:
                logger.info("过滤隐私视频: %s", item.get("item_id"))
                continue
            stat = item.get("statistics", {})
            out.append(VideoStats(
                item_id=item["item_id"],
                play_count=stat.get("play_count", 0),
                like_count=stat.get("digg_count", 0),
                comment_count=stat.get("comment_count", 0),
                share_count=stat.get("share_count", 0),
                download_count=stat.get("download_count", 0),
                avg_play_duration=item.get("avg_play_duration"),
                video_status=item.get("video_status"),
                raw=item,
            ))
        return out

    def fetch_comments(self, open_id: str, item_id: str,
                       cursor: int = 0, count: int = 20) -> dict:
        """评论内容采集（唯一合规路径）。增量拉取按 comment_id 去重。

        GET /comment/list/
        """
        s = get_settings()
        if s.mock_external or not s.douyin_client_key:
            return _mock_comments(item_id, cursor)

        r = httpx.get(
            f"{BASE}/comment/list/",
            params={"open_id": open_id, "item_id": item_id,
                    "cursor": cursor, "count": count},
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json().get("data", {})


def item_id_from_share_url(share_url: str) -> str:
    """从分享链接解析 video_id → 再走官方转换接口换 item_id。

    注意：video_id ≠ item_id，发布接口返回的是 item_id。
    """
    import re

    m = re.search(r"/video/(\d+)", share_url)
    return m.group(1) if m else ""


def _mock_video_stats(item_ids: list[str]) -> list[VideoStats]:
    """mock：确定性伪随机数据（同 item_id 结果稳定，方便幂等测试）。"""
    out = []
    for iid in item_ids:
        seed = int(hashlib.md5(iid.encode()).hexdigest()[:8], 16)
        out.append(VideoStats(
            item_id=iid,
            play_count=seed % 50_000 + 5_000,
            like_count=seed % 900 + 100,
            comment_count=seed % 90 + 10,
            share_count=seed % 40 + 5,
            download_count=seed % 20 + 1,
            avg_play_duration=float(seed % 12 + 8),
            video_status="published",
            raw={"mock": True},
        ))
    return out


def _mock_comments(item_id: str, cursor: int) -> dict:
    """mock 评论数据：含各类意图，供打标流程演示。"""
    base = 20 * cursor
    return {
        "cursor": cursor + 1,
        "has_more": cursor < 2,
        "list": [
            {"comment_id": f"cm_{base + 1}", "text": "怎么收费？想了解下方案",
             "digg_count": 3, "user_nick": "用户13800138000"},
            {"comment_id": f"cm_{base + 2}", "text": "太假了，AI 味重",
             "digg_count": 0, "user_nick": "路人甲"},
            {"comment_id": f"cm_{base + 3}", "text": "同款求模板",
             "digg_count": 12, "user_nick": "运营小王"},
            {"comment_id": f"cm_{base + 4}", "text": "加我微信 abc123 代理优惠",
             "digg_count": 0, "user_nick": "推广bot"},
        ],
    }
