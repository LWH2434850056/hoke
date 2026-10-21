"""LangGraph 内容生成工作流：有状态 DAG + 条件边回退。

状态图（对应文档 2.2 的生成段）：
    OUTLINE → STORYBOARD → VOICEOVER → JUDGE → TITLES
                  ↑             │
                  └── score<阈值 ┘（回退到分镜重写，而非重跑全流程）

为什么用 LangGraph 而不是线性脚本：脚本不合格要回退到
STORYBOARD 节点重生成，有状态图 + 条件边天然支持回退与循环。
"""
from __future__ import annotations

import logging
from typing import Annotated, TypedDict

from aigc_growth.content.geo_rules import apply_geo_rules, compliance_check
from aigc_growth.content.judge import DEFAULT_THRESHOLD, judge_voiceover
from aigc_growth.content.llm import LLMClient
from aigc_growth.content.schemas import Outline, Storyboard, TitlePack

logger = logging.getLogger(__name__)


class ContentState(TypedDict):
    """工作流状态：每节点读写，LangGraph 负责传递与持久化。"""

    topic: str
    outline: Outline | None
    storyboard: Storyboard | None
    voiceover: str | None
    titles: TitlePack | None
    judge_score: float
    judge_issues: list[str]
    feedback: str | None               # 人工反馈（重新生成时注入）
    regenerate_count: int            # 回退重写次数（防死循环）
    threshold: float
    error: str | None


# ---------- 节点实现 ----------

def node_outline(state: ContentState) -> dict:
    """选题确认 → 脚本大纲（强推理模型通道）。有用户反馈时定向修复。"""
    client = LLMClient(tier="strong")
    user = f"选题：{state['topic']}"
    if state.get("feedback"):
        user += (
            f"\n上一版大纲/文案的用户反馈（必须采纳并改进）：{state['feedback']}\n"
            f"上一版口播（参考其问题）：{state.get('voiceover') or '无'}"
        )
    outline = client.structured(
        apply_geo_rules(
            "你是短视频策划。根据选题输出脚本大纲：hook、痛点(1-3个)、解决方案、CTA。"
        ),
        user,
        Outline,
    )
    return {"outline": outline}


def node_storyboard(state: ContentState) -> dict:
    """大纲 → 分镜拆解。有 judge 反馈时把问题带回去定向修复。"""
    client = LLMClient(tier="lite")
    o = state["outline"]
    feedback = ""
    if state.get("judge_issues"):
        feedback = f"\n上一版评审问题（必须修复）：{'；'.join(state['judge_issues'])}"
    storyboard = client.structured(
        "你是分镜师。把大纲拆成3-6个镜头，每个镜头含画面描述、台词、时长(2-30秒)。",
        f"大纲：{o.model_dump_json()}{feedback}",
        Storyboard,
    )
    return {"storyboard": storyboard}


def node_voiceover(state: ContentState) -> dict:
    """分镜 → 口播文案。"""
    sb = state["storyboard"]
    return {"voiceover": sb.full_voiceover}


def node_judge(state: ContentState) -> dict:
    """LLM-as-judge 评分 + 合规规则词库闸门。"""
    vo = state["voiceover"]

    # 第一道闸门：规则词库（广告法红线），命中直接打低分触发回退
    passed, hits = compliance_check(vo)
    if not passed:
        return {
            "judge_score": 0.0,
            "judge_issues": [f"广告法违禁词: {', '.join(hits)}"],
        }

    verdict = judge_voiceover(vo, state.get("threshold", DEFAULT_THRESHOLD))
    return {
        "judge_score": verdict.score,
        "judge_issues": verdict.issues,
    }


def node_titles(state: ContentState) -> dict:
    """口播 → 标题/话题/封面文案三件套。"""
    client = LLMClient(tier="lite")
    pack = client.structured(
        "根据口播文案生成：1-5个标题候选、话题标签(≤8个)、封面大字(≤12字)。",
        f"口播：\n{state['voiceover']}",
        TitlePack,
    )
    return {"titles": pack}


# ---------- 条件边 ----------

def should_regenerate(state: ContentState) -> str:
    """judge 之后：达标 → TITLES；不达标且未超重试 → 回 STORYBOARD；超限 → 带着最高分继续。"""
    if state["judge_score"] >= state.get("threshold", DEFAULT_THRESHOLD):
        return "titles"
    if state.get("regenerate_count", 0) < 2:
        return "regenerate"
    logger.warning("重试2次仍未达标(score=%.1f)，继续走发布流程由人工终审兜底",
                   state["judge_score"])
    return "titles"


def increment_regenerate(state: ContentState) -> dict:
    return {"regenerate_count": state.get("regenerate_count", 0) + 1}


# ---------- 图装配 ----------

def build_graph():
    """构建并编译工作流图。延迟导入 langgraph，未安装时给出降级线性实现。"""
    try:
        from langgraph.graph import END, StateGraph
    except ImportError:
        return _build_linear_fallback()

    g = StateGraph(ContentState)
    g.add_node("outline", node_outline)
    g.add_node("storyboard", node_storyboard)
    g.add_node("voiceover", node_voiceover)
    g.add_node("judge", node_judge)
    g.add_node("regenerate", increment_regenerate)
    g.add_node("titles", node_titles)

    g.set_entry_point("outline")
    g.add_edge("outline", "storyboard")
    g.add_edge("storyboard", "voiceover")
    g.add_edge("voiceover", "judge")
    g.add_conditional_edges("judge", should_regenerate, {
        "titles": "titles",
        "regenerate": "regenerate",
    })
    g.add_edge("regenerate", "storyboard")   # 回退：只重跑分镜之后的节点
    g.add_edge("titles", END)

    return g.compile()


def _build_linear_fallback():
    """langgraph 未安装时的线性降级：节点复用，仅不支持回退。"""
    class _LinearGraph:
        def invoke(self, state: ContentState) -> ContentState:
            out: ContentState = {**state, "regenerate_count": 0, "threshold": DEFAULT_THRESHOLD}
            out.update(node_outline(out))
            out.update(node_storyboard(out))
            out.update(node_voiceover(out))
            out.update(node_judge(out))
            out.update(node_titles(out))
            return out

    return _LinearGraph()


def run_pipeline(topic: str, threshold: float = DEFAULT_THRESHOLD,
                 feedback: str | None = None,
                 prev_voiceover: str | None = None) -> ContentState:
    """一行跑完整条内容生成链路。feedback/prev_voiceover 用于人工重新生成。"""
    graph = build_graph()
    init: ContentState = {
        "topic": topic,
        "outline": None,
        "storyboard": None,
        "voiceover": prev_voiceover,
        "titles": None,
        "judge_score": 0.0,
        "judge_issues": [],
        "feedback": feedback,
        "regenerate_count": 0,
        "threshold": threshold,
        "error": None,
    }
    return graph.invoke(init)
