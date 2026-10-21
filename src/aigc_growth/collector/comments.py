"""评论处理：LLM 意图打标 + 脱敏入库 + 高意向转线索。

获客价值点：不只是统计数量，而是把高意向评论转成线索接 CRM——
这才是获客闭环（文档 Q9 答点）。
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel

from aigc_growth.content.llm import LLMClient
from aigc_growth.models.metrics import Comment

logger = logging.getLogger(__name__)


class _IntentSchema(BaseModel):
    """评论意图打标结构化输出。"""

    intent: str
    reason: str

INTENT_SYSTEM = """\
你是评论区运营助手。对评论做意图分类，输出 JSON：
{"intent": "high_intent|normal|complaint|spam", "reason": "一句话依据"}
- high_intent: 高意向咨询（问价格/方案/合作/流程）
- normal: 一般互动
- complaint: 负面投诉
- spam: 广告灌水
"""

_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_WECHAT_RE = re.compile(r"(微信|vx|VX|WX|wx)[号:：\s]*([a-zA-Z0-9_-]{5,20})")


def mask_pii(text: str) -> str:
    """存储脱敏：手机号、微信号正则脱敏（合规红线）。"""
    text = _PHONE_RE.sub("1**********", text)
    text = _WECHAT_RE.sub(r"\1***", text)
    return text


def mask_nick(nick: str | None) -> str:
    """昵称脱敏：保留首尾字符。"""
    if not nick:
        return "匿名"
    if len(nick) <= 2:
        return nick[0] + "*"
    return nick[0] + "*" * (len(nick) - 2) + nick[-1]


def classify_intent(text: str) -> dict:
    """LLM 打标。规则先行：明显广告/咨询直接判，其余走 LLM。"""
    # 规则快筛（省 token）：命中即返回，不调模型
    if re.search(r"(加.{0,4}(微信|vx|VX)|代理|招商|优惠)", text):
        return {"intent": "spam", "reason": "规则命中广告引流"}
    if re.search(r"(价格|多少钱|费用|报价|怎么收费|合作|方案)", text):
        return {"intent": "high_intent", "reason": "规则命中采购意向词"}

    client = LLMClient(tier="lite")
    result = client.structured(
        INTENT_SYSTEM, text, _IntentSchema,
        mock_result={"intent": "normal", "reason": "mock 默认一般互动"},
    )
    return {"intent": result.intent, "reason": result.reason}


def upsert_comment(db, platform: str, item_id: str, raw: dict) -> Comment:
    """单条评论脱敏 + 打标 + 幂等入库。已存在的评论跳过（增量拉取）。"""
    existing = db.query(Comment).filter_by(
        platform=platform, comment_id=raw["comment_id"]
    ).one_or_none()
    if existing:
        return existing

    intent = classify_intent(raw.get("text", ""))
    c = Comment(
        platform=platform,
        item_id=item_id,
        comment_id=raw["comment_id"],
        parent_id=raw.get("parent_id"),
        user_nick_masked=mask_nick(raw.get("user_nick")),
        content=mask_pii(raw.get("text", "")),
        like_count=raw.get("digg_count", 0),
        intent_label=intent["intent"],
        intent_reason=intent["reason"],
        converted_lead=intent["intent"] == "high_intent",   # 高意向 → 直接转线索
    )
    db.add(c)
    return c


def process_comments(db, platform: str, item_id: str, raw_list: list[dict]) -> dict:
    """批量处理一页评论，返回线索统计。"""
    new_leads = 0
    for raw in raw_list:
        c = upsert_comment(db, platform, item_id, raw)
        if c.intent_label == "high_intent" and not c.converted_lead:
            c.converted_lead = True
            new_leads += 1
            # 生产：此处推送企微/CRM webhook
            logger.info("[线索] 评论 %s → CRM", c.comment_id)
    db.commit()
    return {"processed": len(raw_list), "new_leads": new_leads}
