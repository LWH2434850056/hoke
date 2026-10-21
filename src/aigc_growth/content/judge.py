"""LLM-as-judge：多版本生成结果的自动评分与择优。

闭环：生成 3 版 → 多维评分 → 择优；低于阈值自动重生成（上限 N 次）。
"""
from __future__ import annotations

import logging

from aigc_growth.content.llm import LLMClient
from aigc_growth.content.schemas import JudgeVerdict

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 7.0

JUDGE_SYSTEM = """\
你是资深短视频内容评审。对给定的口播文案从四个维度打分(0-10)：
- hook吸引力：前3秒能否留住观众
- 信息密度：是否有可验证的数据与干货
- 转化引导：CTA 是否清晰自然
- 合规性：是否有广告法违规、绝对化用语
输出 JSON：{"score": 综合分, "dimensions": {...}, "issues": [问题列表], "pass_threshold": score>=阈值}
"""


def judge_voiceover(
    voiceover: str, threshold: float = DEFAULT_THRESHOLD
) -> JudgeVerdict:
    """单版本评分。"""
    client = LLMClient(tier="lite")
    return client.structured(
        JUDGE_SYSTEM,
        f"阈值 {threshold}。评审以下口播文案：\n\n{voiceover}",
        JudgeVerdict,
        mock_result={
            "score": 7.8,
            "dimensions": {"hook吸引力": 8.0, "信息密度": 7.5, "转化引导": 7.0, "合规性": 9.0},
            "issues": [],
            "pass_threshold": True,
        },
    )


def generate_with_judge(
    generate_fn,
    n_versions: int = 3,
    threshold: float = DEFAULT_THRESHOLD,
    max_regenerate: int = 2,
    **kwargs,
):
    """生成→评分→择优→低于阈值重生成的完整闭环。

    generate_fn: (version_idx) -> 文案 的可调用对象
    返回 (最佳文案, 评审结果)
    """
    candidates: list[tuple[str, JudgeVerdict]] = []

    for i in range(n_versions):
        vo = generate_fn(i, **kwargs)
        verdict = judge_voiceover(vo, threshold)
        candidates.append((vo, verdict))
        if verdict.score >= threshold:
            logger.info("版本%d 直接达标: %.1f", i + 1, verdict.score)
            return vo, verdict

    # 无版本达标：取最高分版本，若低于阈值且允许重生成则再来一轮
    candidates.sort(key=lambda x: x[1].score, reverse=True)
    best_vo, best_verdict = candidates[0]

    for attempt in range(max_regenerate):
        if best_verdict.score >= threshold:
            break
        logger.warning("最高分 %.1f 低于阈值 %.1f，重生成第 %d 次",
                       best_verdict.score, threshold, attempt + 1)
        vo = generate_fn(100 + attempt, **kwargs, hint="；".join(best_verdict.issues) or "提高hook吸引力与数据密度")
        verdict = judge_voiceover(vo, threshold)
        if verdict.score > best_verdict.score:
            best_vo, best_verdict = vo, verdict

    return best_vo, best_verdict
