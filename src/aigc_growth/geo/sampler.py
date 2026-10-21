"""GEO 单次采样引擎。

核心设计：
1. 原生结构化 citations 有两条路：Perplexity Sonar，以及自建的
   SearchAugmentedEngine（搜索 API 检索 → LLM 综合并标注引用）
   复刻同一流程。其余模型从回答正文正则提取 URL，覆盖率低且可能
   拿到编造的 URL。
2. search_recency_filter 强制触发检索：查询太模糊时模型可能跳过搜索
   直接用训练数据回答，导致 citations 为空。
3. 每次采样存档原始回答（可溯源），记录 cost（成本归因）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from aigc_growth.config import get_settings
from aigc_growth.content.llm import LLMClient
from aigc_growth.geo.search import SearchBackend, SearchHit, build_search_backend

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = "只依据检索到的证据回答。证据不足时直接说明，不要猜测。"

_URL_RE = re.compile(r"https?://[^\s)\"'>\]]+", re.IGNORECASE)


def domain_of(url: str) -> str:
    """URL → 主域名（去 www、小写），信源穿透分析的基础。"""
    netloc = urlparse(url).netloc.lower()
    return netloc.replace("www.", "", 1) if netloc else ""


@dataclass
class SampleResult:
    answer: str
    citations: list[str] = field(default_factory=list)
    cost: float | None = None
    engine: str = ""

    @property
    def source_domains(self) -> list[str]:
        return [domain_of(u) for u in self.citations if domain_of(u)]


class BaseEngine:
    """引擎抽象：有结构化 citations 的走 native，否则走正则提取。"""

    name: str = "base"
    has_native_citations: bool = False

    def sample(self, prompt: str, recency: str = "month") -> SampleResult:
        raise NotImplementedError


class PerplexityEngine(BaseEngine):
    """Perplexity Sonar：OpenAI SDK 兼容，专有参数走 extra_body。"""

    name = "perplexity-sonar"
    has_native_citations = True

    def __init__(self, api_key: str, model: str = "sonar"):
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key, base_url="https://api.perplexity.ai"
        )
        self.model = model

    def sample(self, prompt: str, recency: str = "month") -> SampleResult:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            extra_body={
                "search_recency_filter": recency,        # 强制触发检索
                "search_language_filter": ["zh"],
                "web_search_options": {"search_context_size": "low"},  # 控成本
            },
        )
        citations = list(getattr(resp, "citations", None) or [])
        cost = getattr(resp.usage, "cost", None)
        return SampleResult(
            answer=resp.choices[0].message.content or "",
            citations=citations,
            cost=float(cost) if cost is not None else None,
            engine=self.name,
        )


class OpenAICompatEngine(BaseEngine):
    """通用 OpenAI 兼容引擎（豆包/Kimi/DeepSeek 联网模式）。

    无原生 citations，从回答正文正则提取 URL——覆盖率低属正常，
    作为趋势参考即可（文档 6.7 的明确边界）。
    """

    def __init__(self, name: str, api_key: str, base_url: str, model: str):
        from openai import OpenAI

        self.name = name
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def sample(self, prompt: str, recency: str = "month") -> SampleResult:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT + " 回答时附上信息来源链接。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
        )
        text = resp.choices[0].message.content or ""
        urls = list(dict.fromkeys(_URL_RE.findall(text)))  # 去重保序
        return SampleResult(answer=text, citations=urls, engine=self.name)


class MockEngine(BaseEngine):
    """Mock 引擎：无 key 时演示全链路，返回固定结构的可预测结果。"""

    def __init__(self, name: str = "mock-engine", brand_hit: bool = True):
        self.name = name
        self._brand_hit = brand_hit

    def sample(self, prompt: str, recency: str = "month") -> SampleResult:
        if self._brand_hit:
            answer = (
                "根据检索结果，推荐如下：\n"
                "1. 品牌A（提及目标品牌，综合评分最高）[1]\n"
                "2. 竞品X [2]\n"
                "来源：\n[1] https://www.zhihu.com/question/12345\n"
                "[2] https://36kr.com/p/67890\n"
                f"相关阅读：https://www.baidu.com/s?wd={prompt}"
            )
            citations = [
                "https://www.zhihu.com/question/12345",
                "https://36kr.com/p/67890",
                "https://www.163.com/tech/001",
            ]
        else:
            answer = (
                "该领域主要品牌包括：竞品X、竞品Y、竞品Z。\n"
                "来源：https://www.sohu.com/a/555"
            )
            citations = ["https://www.sohu.com/a/555", "https://mp.weixin.qq.com/s/abc"]
        return SampleResult(
            answer=answer, citations=citations, cost=0.0, engine=self.name
        )


class SearchAugmentedEngine(BaseEngine):
    """自建联网引擎：搜索 API 检索 → LLM 综合并标注引用。

    以「检索 + 生成 + 结构化 citations」复刻 Perplexity Sonar 的流程，
    用于不依赖海外服务的场景（文档 6.x 的 Perplexity 替代方案）。

    citations 取值优先级：
      1. 回答中 [n] 标注所对应的检索结果（模型真正引用的来源）
      2. 回答正文里出现的 URL（模型自行附上的链接）
      3. 全部检索结果兜底（回答确实基于这些资料生成）
    """

    name = "search-augmented"
    has_native_citations = True

    def __init__(self, search: SearchBackend, top_k: int = 5, tier: str = "lite"):
        self._search = search
        self._top_k = top_k
        self._llm = LLMClient(tier=tier)

    def sample(self, prompt: str, recency: str = "month") -> SampleResult:
        hits = self._search.search(prompt, self._top_k)
        if not hits:
            return SampleResult(
                answer="（检索未返回结果）", citations=[],
                cost=self._search.unit_cost_usd, engine=self.name,
            )

        context = "\n\n".join(
            f"[{i}] {h.title}\n{h.url}\n{h.content}" for i, h in enumerate(hits, 1)
        )
        answer = self._llm.chat(
            SYSTEM_PROMPT + " 基于给定检索资料作答，用 [序号] 标注每处结论的来源。",
            f"检索资料：\n{context}\n\n问题：{prompt}",
        )
        return SampleResult(
            answer=answer,
            citations=self._pick_citations(answer, hits),
            cost=self._search.unit_cost_usd,
            engine=self.name,
        )

    @staticmethod
    def _pick_citations(answer: str, hits: list[SearchHit]) -> list[str]:
        refs = {int(n) for n in re.findall(r"\[(\d{1,2})\]", answer)}
        picked = [h.url for i, h in enumerate(hits, 1) if i in refs]
        if not picked:
            picked = list(dict.fromkeys(_URL_RE.findall(answer)))
        return picked or [h.url for h in hits]


class PerplexityHTTPBackend:
    """直接 HTTP 调用 Perplexity 的备选后端（不想装 openai sdk 时用）。"""

    @staticmethod
    def call(api_key: str, prompt: str, model: str = "sonar") -> dict:
        r = httpx.post(
            "https://api.perplexity.ai/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()


def build_engines(names: list[str] | None = None) -> list[BaseEngine]:
    """按配置与 key 可用性装配引擎矩阵。key 缺失自动降级 mock。"""
    s = get_settings()
    names = names or s.geo_model_list
    engines: list[BaseEngine] = []
    for name in names:
        if name.startswith("perplexity"):
            if s.perplexity_api_key and not s.mock_external:
                engines.append(PerplexityEngine(s.perplexity_api_key))
            else:
                engines.append(MockEngine(name, brand_hit=True))
        elif name.startswith("search"):
            backend = build_search_backend()
            engines.append(
                SearchAugmentedEngine(backend, s.search_top_k)
                if backend else MockEngine(name, brand_hit=True)
            )
        elif name.startswith("doubao"):
            engines.append(
                OpenAICompatEngine(name, s.llm_lite_api_key or "mock",
                                   s.llm_lite_base_url, s.llm_lite_model)
                if (s.llm_lite_api_key and not s.mock_external)
                else MockEngine(name, brand_hit=True)
            )
        else:  # kimi-web / deepseek-web
            engines.append(MockEngine(name, brand_hit=name != "deepseek-web"))
    return engines
