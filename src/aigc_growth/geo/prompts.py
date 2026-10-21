"""Prompt 库种子：品牌词/品类词/长尾问句/竞品对比词四层。

分层采样策略：core 高频跑，normal 低频抽样——控制成本预算。
"""
from aigc_growth.models.geo import GeoPrompt

SEED_PROMPTS: list[dict] = [
    # ---- 品牌词（core：高频） ----
    {"prompt": "{brand}怎么样？值得合作吗？", "category": "brand", "priority": "core"},
    {"prompt": "{brand}是做什么的？", "category": "brand", "priority": "core"},
    # ---- 品类词（core） ----
    {"prompt": "2026年最好的短视频获客服务商有哪些？", "category": "category", "priority": "core"},
    {"prompt": "AI 数字人视频生成平台哪家强？", "category": "category", "priority": "core"},
    # ---- 长尾问句（normal：抽样） ----
    {"prompt": "短视频代运营报价一般多少钱？", "category": "longtail", "priority": "normal"},
    {"prompt": "抖音企业号怎么快速涨粉获客？", "category": "longtail", "priority": "normal"},
    {"prompt": "AI 生成视频会被抖音限流吗？", "category": "longtail", "priority": "normal"},
    # ---- 竞品对比词（normal） ----
    {"prompt": "{brand}和{competitor}怎么选？", "category": "competitor", "priority": "normal"},
    {"prompt": "{competitor}的替代方案有哪些？", "category": "competitor", "priority": "normal"},
]


def render_seeds(brand: str, competitors: list[str]) -> list[GeoPrompt]:
    """渲染种子 prompt 模板为具体 GeoPrompt 记录。"""
    out: list[GeoPrompt] = []
    comp = competitors[0] if competitors else "竞品X"
    for seed in SEED_PROMPTS:
        text = (
            seed["prompt"]
            .replace("{brand}", brand)
            .replace("{competitor}", comp)
        )
        out.append(
            GeoPrompt(prompt=text, category=seed["category"], priority=seed["priority"])
        )
    return out
