"""GEO 联网检索后端：为 SearchAugmentedEngine 提供结构化检索结果。

Perplexity 在 GEO 里的核心贡献是「联网检索 + 结构化 citations」两件事。
本模块用可直连的搜索 API 复刻这一能力，让 GEO 跟踪不再强依赖
Perplexity（海外信用卡 + 网络可达性）。

三个后端输出统一为 SearchHit 列表，上层引擎与报表无需感知差异。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

import httpx

from aigc_growth.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class SearchHit:
    title: str
    url: str
    content: str = ""


class _Throttle:
    """全局节流：同一通道内串行化请求并保持最小间隔。

    GEO 一次全量采样会并发打出十几个检索请求（prompt 数 × 采样次数），
    远超上游 QPS 配额，不节流必然撞 429。持有锁期间睡满间隔，
    相当于给每个调用方预约下一个发送时隙。
    """

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_at:
                time.sleep(self._next_at - now)
            self._next_at = time.monotonic() + self._min_interval


# 体验包/免费档的 QPS 通常很低，1 秒间隔较稳；付费档可在配置里调小
_THROTTLE = _Throttle(1.0)
_MAX_RETRY = 3


def _post(url: str, headers: dict, payload: dict, timeout: int = 30) -> dict:
    """带节流与 429/5xx 指数退避重试的 POST，返回 JSON。"""
    last: httpx.Response | None = None
    for attempt in range(_MAX_RETRY):
        _THROTTLE.wait()
        r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 429 or r.status_code >= 500:
            last = r
            backoff = min(2 ** attempt, 8)
            logger.warning("搜索接口 %s 返回 %s，%s 秒后重试（第 %d 次）",
                           url, r.status_code, backoff, attempt + 1)
            time.sleep(backoff)
            continue
        r.raise_for_status()
        return r.json()
    assert last is not None
    last.raise_for_status()
    raise RuntimeError("unreachable")


class SearchBackend:
    """检索后端抽象：实现 search() 即接入 GEO。"""

    name: str = "base"
    unit_cost_usd: float = 0.0        # 单次检索成本，用于 GEO 报告的成本归因

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        raise NotImplementedError


class ZhipuSearch(SearchBackend):
    """智谱 Web Search API：国内直连，search_std 0.01 元/次。"""

    name = "zhipu-web-search"
    URL = "https://open.bigmodel.cn/api/paas/v4/web_search"

    def __init__(self, api_key: str, engine: str = "search_std"):
        self._api_key = api_key
        self._engine = engine
        # search_std 0.01 元 / search_pro 0.03 元，按 7.2 汇率折美元
        self.unit_cost_usd = 0.0042 if engine == "search_pro" else 0.0014

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        body = _post(
            self.URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={
                "search_query": query[:70],      # 官方上限 70 字符
                "search_engine": self._engine,
                "count": max(1, min(top_k, 50)),
                "content_size": "high",          # 摘要给足，便于 LLM 综合
                "search_recency_filter": "oneYear",
            },
        )
        return [
            SearchHit(title=it.get("title") or "",
                      url=it.get("link") or "",
                      content=it.get("content") or "")
            for it in (body.get("search_result") or [])
            if it.get("link")
        ]


class TavilySearch(SearchBackend):
    """Tavily：为 LLM 设计的搜索 API，免费 1000 credits/月，但需海外网络。"""

    name = "tavily"
    URL = "https://api.tavily.com/search"
    unit_cost_usd = 0.008        # 付费档 $8/1000 次；免费额度内实际为 0

    def __init__(self, api_key: str):
        self._api_key = api_key

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        body = _post(
            self.URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={
                "query": query,
                "max_results": top_k,
                "search_depth": "basic",
                "topic": "general",
            },
        )
        return [
            SearchHit(title=it.get("title") or "",
                      url=it.get("url") or "",
                      content=it.get("content") or "")
            for it in (body.get("results") or [])
            if it.get("url")
        ]


class BochaSearch(SearchBackend):
    """博查 AI 搜索：国内直连，响应结构兼容 Bing Search API。"""

    name = "bocha"
    URL = "https://api.bochaai.com/v1/web-search"
    unit_cost_usd = 0.0014

    def __init__(self, api_key: str):
        self._api_key = api_key

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        body = _post(
            self.URL,
            headers={"Authorization": f"Bearer {self._api_key}"},
            payload={"query": query, "count": top_k, "summary": True,
                     "freshness": "oneYear"},
        )
        pages = (
            # 官方返回：webPages 直接在顶层（兼容 Bing Search API 结构）
            (body.get("webPages") or {}).get("value")
            # 兼容部分网关/代理在 data 下再包一层的情况
            or ((body.get("data") or {}).get("webPages") or {}).get("value")
            or []
        )
        return [
            SearchHit(title=it.get("name") or "",
                      url=it.get("url") or "",
                      content=it.get("summary") or it.get("snippet") or "")
            for it in pages
            if it.get("url")
        ]


# provider -> 对应 settings 字段名
_KEY_FIELD = {
    "zhipu": "zhipu_api_key",
    "tavily": "tavily_api_key",
    "bocha": "bocha_api_key",
}


def _make(provider: str, key: str) -> SearchBackend:
    if provider == "zhipu":
        return ZhipuSearch(key, get_settings().zhipu_search_engine)
    if provider == "tavily":
        return TavilySearch(key)
    return BochaSearch(key)


def build_search_backend() -> SearchBackend | None:
    """按 search_provider 选通道；留空则按已配 key 自动挑选。

    返回 None 表示无可用搜索通道，调用方应降级为 mock 引擎。
    """
    s = get_settings()
    if s.mock_external:
        return None

    if s.search_provider:
        if s.search_provider not in _KEY_FIELD:
            logger.warning("未知 search_provider=%s，改为按 key 自动挑选", s.search_provider)
        else:
            key = getattr(s, _KEY_FIELD[s.search_provider], "")
            if not key:
                logger.warning("search_provider=%s 但对应 key 为空，GEO 降级为 mock",
                               s.search_provider)
                return None
            return _make(s.search_provider, key)

    for provider, key_field in _KEY_FIELD.items():
        key = getattr(s, key_field, "")
        if key:
            logger.info("GEO 搜索通道自动选定：%s", provider)
            return _make(provider, key)
    return None
