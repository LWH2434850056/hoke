"""GEO 报告：信源缺口分析（最有价值的一步）+ Markdown 输出。

信源穿透的意义：AI 不引用你，往往不是因为你内容差，
而是它引用的那几个域名里没有你。
"""
from __future__ import annotations

from collections import Counter

from aigc_growth.geo.tracker import PromptTrackResult

# 默认自有信源域名：我们已布局内容矩阵的阵地
DEFAULT_OWN_DOMAINS = {
    "example-brand.com",        # 官网
    "zhihu.com",                # 问答社区层
    "mp.weixin.qq.com",         # 公众号
    "xiaohongshu.com",          # UGC 层
}


def source_gap(
    domains: Counter,
    own_domains: set[str] | None = None,
) -> dict:
    """对比"AI 实际引用的域名"与"我们已布局的域名"，输出缺口。

    gap 里的域名就是下一步内容投放的目标清单——
    去这些站点发内容，比闷头写自有内容有效得多。
    """
    own = own_domains or DEFAULT_OWN_DOMAINS
    covered = {d for d in domains if d in own}
    gap = [d for d in domains if d not in own]
    return {
        "cited_domains": domains.most_common(20),   # AI 信任的信源
        "covered": sorted(covered),                  # 我们已占位的
        "gap": gap,                                  # 待攻克的
    }


def aggregate_overall(results: list[PromptTrackResult], brand: str) -> dict:
    """跨 prompt 汇总：整体可见度指标。"""
    total = sum(r.n_samples for r in results)
    mentions = sum(round(r.mention_rate * r.n_samples) for r in results)
    cited = sum(round(r.citation_rate * r.n_samples) for r in results)
    domain_counter: Counter = Counter()
    competitor_counter: Counter = Counter()
    for r in results:
        domain_counter.update(r.source_domains)
        competitor_counter.update(r.competitors_hits)

    # AI Share of Voice：品牌提及 / (品牌提及 + 竞品共现)
    competitor_total = sum(competitor_counter.values())
    sov = mentions / (mentions + competitor_total) if (mentions + competitor_total) else 0.0

    return {
        "brand": brand,
        "total_samples": total,
        "mention_rate": mentions / total if total else 0.0,
        "citation_rate": cited / total if total else 0.0,
        "sov": round(sov, 4),
        "top_competitors": competitor_counter.most_common(10),
        "gap": source_gap(domain_counter),
        "cost": round(sum(r.cost for r in results), 4),
    }


def render_markdown(results: list[PromptTrackResult], brand: str) -> str:
    """输出 Markdown 报告到 output/。"""
    overall = aggregate_overall(results, brand)
    gap = overall["gap"]

    lines = [
        f"# GEO 可见度跟踪报告 · {brand}",
        "",
        f"> 采样：{overall['total_samples']} 次 | 成本：${overall['cost']}",
        "> 注意：AI 回答有随机性，只看趋势不看绝对值。",
        "",
        "## 核心指标",
        "",
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 提及率 Mention Rate | {overall['mention_rate']:.1%} |",
        f"| 引用率 Citation Rate | {overall['citation_rate']:.1%} |",
        f"| AI Share of Voice | {overall['sov']:.1%} |",
        "",
        "## 竞品共现 Top 10",
        "",
        "| 竞品 | 共现次数 |",
        "|------|---------|",
    ]
    lines += [f"| {c} | {n} |" for c, n in overall["top_competitors"]] or ["| （无） | 0 |"]

    lines += [
        "",
        "## 信源缺口分析",
        "",
        f"- **已占位**（我们布局了、AI 也引用了）：{', '.join(gap['covered']) or '无'}",
        "",
        f"- **待攻克**（AI 高频引用但我们还没布局）：",
        "",
        "| 域名 | 被引用次数 | 行动建议 |",
        "|------|-----------|---------|",
    ]
    for d, n in gap["cited_domains"]:
        if d in gap["gap"]:
            lines.append(f"| {d} | {n} | 投放内容 / 建立信源 |")

    lines += ["", "## 逐 Prompt 明细", ""]
    for r in results:
        lines += [
            f"### [{r.engine}] {r.prompt}",
            f"- 提及率 {r.mention_rate:.0%} | 引用率 {r.citation_rate:.0%}"
            f" | 位置 {dict(r.positions)} | 成本 ${r.cost:.4f}",
            "",
        ]

    return "\n".join(lines)
