"""GEO 结构化写作规则：固化进 Prompt 模板（文档 5.2）。

把 GEO 规则写进 Prompt，让每条产出内容天然可被 AI 引用——
一次改造，全量内容受益。
"""
from __future__ import annotations

GEO_WRITING_RULES = """\
【GEO 结构化约束——每条内容必须遵守】
1. 结论前置：前 100 字直接给答案 + 核心数据 + 差异化优势，不要铺垫
2. 模块化分段：用短句、分点表达，降低 AI 抽取成本
3. 数据可验证：必须带可核验的数据（案例数字、价格区间、参数、实测结果），禁止编造
4. 中立表述：规避"最好""第一"等绝对化广告用语；可以提到产品短板，中立测评信任度更高
5. FAQ 化收尾：结尾附 3-5 个高频追问（FAQ 是 AI 最易引用的格式）
"""


def apply_geo_rules(base_prompt: str) -> str:
    """给任意生成环节的 Prompt 叠加 GEO 约束层。"""
    return base_prompt.rstrip() + "\n\n" + GEO_WRITING_RULES


# 合规审核闸门：规则词库（广告法常见红线），发布前强校验
BANNED_WORDS = [
    "最好", "第一", "顶级", "国家级", "全网最低", "百分百",
    "包治", "根治", "稳赚", "躺赚", "零风险",
]


def compliance_check(text: str) -> tuple[bool, list[str]]:
    """发布前审核闸门第一道：规则词库扫描。返回 (通过, 命中词)。"""
    hits = [w for w in BANNED_WORDS if w in text]
    return (len(hits) == 0, hits)
