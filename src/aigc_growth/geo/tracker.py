"""GEO 跟踪核心：多次采样 + 指标统计。

四个关键工程点（对应文档 6.4）：
1. 多次采样：AI 回答有随机性，单次结果不可信，采样 3-5 次取统计值
2. 变量控制：每次调用独立（无记忆），固定温度
3. 并发控制：Semaphore 限流，避免触发上游限流
4. 原始存档：所有回答不删改落库
"""
from __future__ import annotations

import asyncio
import logging
from collections import Counter
from dataclasses import dataclass, field

from aigc_growth.geo.sampler import BaseEngine, SampleResult, domain_of

logger = logging.getLogger(__name__)


@dataclass
class PromptTrackResult:
    """单条 prompt 的统计结果。结论只看趋势，不看绝对值。"""

    prompt: str
    engine: str
    n_samples: int
    mention_rate: float = 0.0       # 提及率 = 提及次数 / 总采样
    citation_rate: float = 0.0      # 引用率 = 提及且带引用 / 总采样（比提及率更严格）
    positions: Counter = field(default_factory=Counter)      # head/mid/tail
    competitors_hits: Counter = field(default_factory=Counter)  # 竞品共现
    source_domains: Counter = field(default_factory=Counter)    # 信源穿透：AI 到底引用了谁
    cost: float = 0.0
    failed: int = 0                 # 失败采样数（单引擎故障降级用，不计入比率分母）
    raw: list[dict] = field(default_factory=list)               # 原始存档

    def to_dict(self) -> dict:
        return {
            "prompt": self.prompt,
            "engine": self.engine,
            "n_samples": self.n_samples,
            "failed": self.failed,
            "mention_rate": round(self.mention_rate, 4),
            "citation_rate": round(self.citation_rate, 4),
            "positions": dict(self.positions),
            "competitors_hits": dict(self.competitors_hits),
            "source_domains": self.source_domains.most_common(20),
            "cost": round(self.cost, 4),
        }


def analyze(answer: str, brand: str, competitors: list[str]) -> dict:
    """解析单次回答：提及、位置、竞品共现。

    位置判定：品牌首次出现位置按回答长度三等分。
    """
    text = answer.lower()
    b = brand.lower()
    hit = b in text
    pos = None
    if hit:
        idx = text.index(b)
        pos = "head" if idx < len(text) * 0.33 else (
            "mid" if idx < len(text) * 0.66 else "tail"
        )
    return {
        "mentioned": hit,
        "position": pos,
        "competitors": [c for c in competitors if c.lower() in text],
    }


def _one_sample(engine: BaseEngine, prompt: str, brand: str,
                competitors: list[str]) -> dict:
    """单次采样 + 解析（同步执行，由线程池并发调度）。

    单引擎故障（限流/网络/上游异常）降级为一条 failed 记录，
    不向上抛异常——否则一个引擎挂掉会让整轮采样 500。
    """
    try:
        r: SampleResult = engine.sample(prompt)
    except Exception as exc:
        logger.warning("引擎 %s 采样失败，已降级：%s", engine.name, exc)
        return {
            "answer": "", "citations": [], "source_domains": [],
            "cost": 0.0, "failed": True, "error": str(exc),
            "mentioned": False, "position": None, "competitors": [],
        }
    a = analyze(r.answer, brand, competitors)
    return {
        "answer": r.answer,
        "citations": r.citations,
        "source_domains": r.source_domains,
        "cost": r.cost or 0.0,
        "failed": False,
        **a,
    }


def track_prompt_sync(
    engine: BaseEngine,
    prompt: str,
    brand: str,
    competitors: list[str],
    n_samples: int = 3,
) -> PromptTrackResult:
    """同步版：多次采样并统计（供 Celery 任务调用）。"""
    raws = [_one_sample(engine, prompt, brand, competitors) for _ in range(n_samples)]
    return _aggregate(prompt, engine.name, n_samples, raws)


async def track_prompt(
    engine: BaseEngine,
    prompt: str,
    brand: str,
    competitors: list[str],
    n_samples: int = 3,
    concurrency: int = 5,
) -> PromptTrackResult:
    """异步版：Semaphore 并发限流下多次采样。"""
    sem = asyncio.Semaphore(concurrency)

    async def one() -> dict:
        async with sem:
            return await asyncio.to_thread(
                _one_sample, engine, prompt, brand, competitors
            )

    raws = await asyncio.gather(*[one() for _ in range(n_samples)])
    return _aggregate(prompt, engine.name, n_samples, list(raws))


def _aggregate(prompt: str, engine_name: str, n: int, raws: list[dict]) -> PromptTrackResult:
    ok = [r for r in raws if not r.get("failed")]
    denom = len(ok) or n          # 全部失败时退回请求数，避免除零
    mentions = [r for r in ok if r["mentioned"]]
    cited = [r for r in mentions if r["citations"]]
    return PromptTrackResult(
        prompt=prompt,
        engine=engine_name,
        n_samples=n,
        failed=len(raws) - len(ok),
        mention_rate=len(mentions) / denom,
        citation_rate=len(cited) / denom,
        positions=Counter(m["position"] for m in mentions),
        competitors_hits=Counter(c for r in ok for c in r["competitors"]),
        source_domains=Counter(d for r in ok for d in r["source_domains"]),
        cost=sum(r["cost"] for r in raws),
        raw=raws,
    )


async def run_track(
    engines: list[BaseEngine],
    prompts: list[str],
    brand: str,
    competitors: list[str],
    n_samples: int = 3,
    concurrency: int = 5,
) -> list[PromptTrackResult]:
    """全量跟踪：prompt 库 × 引擎矩阵 × N 次采样。"""
    tasks = [
        track_prompt(e, p, brand, competitors, n_samples, concurrency)
        for p in prompts
        for e in engines
    ]
    return list(await asyncio.gather(*tasks))
