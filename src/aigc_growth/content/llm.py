"""LLM 客户端：分层路由 + Mock 降级 + 结构化输出。

模型分层路由（成本三件套之一）：
- 强推理模型（strong）：选题确认、脚本大纲 —— 重决策
- 高性价比模型（lite）：批量口播、标题、评论打标 —— 批量生产
"""
from __future__ import annotations

import json
import logging
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from aigc_growth.config import get_settings

logger = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMClient:
    """统一的 LLM 调用入口，带结构化输出与修复重试。"""

    def __init__(self, tier: str = "lite"):
        s = get_settings()
        self.mock = s.mock_external
        if tier == "strong":
            self.api_key, self.base_url, self.model = (
                s.llm_strong_api_key, s.llm_strong_base_url, s.llm_strong_model,
            )
        else:
            self.api_key, self.base_url, self.model = (
                s.llm_lite_api_key, s.llm_lite_base_url, s.llm_lite_model,
            )
        self._client = None

    def _get_client(self):
        if self._client is None and not self.mock:
            from openai import OpenAI

            self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        return self._client

    def chat(self, system: str, user: str) -> str:
        """纯文本对话。mock 模式返回占位文本。"""
        if self.mock:
            return f"[MOCK LLM 回复] {user[:50]}..."
        resp = self._get_client().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content or ""

    def structured(
        self,
        system: str,
        user: str,
        schema: type[T],
        max_repair: int = 2,
        mock_result: dict | None = None,
    ) -> T:
        """结构化输出 + Schema 强校验 + 失败自修复。

        解析失败不抛异常：把报错回喂给模型让它自我修正（文档 Q2 答点）。
        """
        if self.mock:
            data = mock_result if mock_result is not None else _default_mock(schema)
            return schema.model_validate(data)

        client = self._get_client()
        messages = [
            {"role": "system", "content": system + "\n输出必须是合法 JSON，不要带 markdown 代码块。"},
            {"role": "user", "content": user},
        ]
        last_err: Exception | None = None
        for attempt in range(max_repair + 1):
            resp = client.chat.completions.create(model=self.model, messages=messages)
            text = resp.choices[0].message.content or ""
            try:
                data = _extract_json(text)
                return schema.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as exc:
                last_err = exc
                logger.warning("结构化输出校验失败(第%d次): %s", attempt + 1, exc)
                messages.append({"role": "assistant", "content": text})
                messages.append({
                    "role": "user",
                    "content": f"你的输出解析失败：{exc}。请严格按 Schema 重新输出合法 JSON。",
                })
        raise ValueError(f"结构化输出修复 {max_repair} 次仍失败: {last_err}")


def _extract_json(text: str) -> dict:
    """从 LLM 回复中提取 JSON（容忍 markdown 代码块包裹）。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


def _default_mock(schema: type[BaseModel]) -> dict:
    """按 Schema 生成最小合法 mock 数据。"""
    if schema.__name__ == "Outline":
        return {
            "hook": "还在为获客发愁？这条视频讲透",
            "pain_points": ["人工写稿慢", "选题靠猜"],
            "solution": "AI 全链路自动化获客流水线",
            "cta": "评论区扣1，送获客方案",
        }
    if schema.__name__ == "Storyboard":
        return {
            "shots": [
                {"seq": 1, "scene": "人物出镜讲痛点", "voiceover_line": "每天写稿3小时，产出还是不稳定？", "duration_sec": 4},
                {"seq": 2, "scene": "屏幕演示系统界面", "voiceover_line": "AI 一键生成脚本、配音、字幕、成片", "duration_sec": 6},
                {"seq": 3, "scene": "数据看板特写", "voiceover_line": "数据回流自动优化选题，越用越准", "duration_sec": 5},
            ]
        }
    if schema.__name__ == "TitlePack":
        return {
            "titles": ["单条视频成本降到3块钱，我是怎么做到的"],
            "hashtags": ["#AI获客", "#数字人", "#短视频运营"],
            "cover_text": "成本降90%",
        }
    if schema.__name__ == "JudgeVerdict":
        return {
            "score": 7.5,
            "dimensions": {"hook吸引力": 8, "信息密度": 7, "转化引导": 8, "合规性": 9},
            "issues": [],
            "pass_threshold": True,
        }
    raise ValueError(f"无 mock 模板: {schema.__name__}")
