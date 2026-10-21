"""结构化输出 Schema：保证 LLM 输出可被下游代码稳定解析。

全部环节强制 JSON Schema 校验；解析失败不抛异常，
把报错回喂给模型自我修复（重试上限内）。
"""
from pydantic import BaseModel, Field


class Outline(BaseModel):
    """脚本大纲。"""

    hook: str = Field(description="开篇钩子，前3秒抓住注意力")
    pain_points: list[str] = Field(min_length=1, max_length=3)
    solution: str
    cta: str = Field(description="行动号召")


class StoryboardShot(BaseModel):
    """分镜单元。"""

    seq: int
    scene: str = Field(description="画面描述，用于素材匹配")
    voiceover_line: str = Field(description="该镜头口播台词")
    duration_sec: int = Field(ge=2, le=30)


class Storyboard(BaseModel):
    shots: list[StoryboardShot] = Field(min_length=3, max_length=10)

    @property
    def full_voiceover(self) -> str:
        return "\n".join(s.voiceover_line for s in self.shots)

    @property
    def total_duration(self) -> int:
        return sum(s.duration_sec for s in self.shots)


class TitlePack(BaseModel):
    """标题/话题/封面文案三件套。"""

    titles: list[str] = Field(min_length=1, max_length=5)
    hashtags: list[str] = Field(max_length=8)
    cover_text: str = Field(description="封面大字，≤12字")


class JudgeVerdict(BaseModel):
    """LLM-as-judge 评分结果。"""

    score: float = Field(ge=0, le=10)
    dimensions: dict[str, float] = Field(
        description="hook吸引力/信息密度/转化引导/合规性 各0-10"
    )
    issues: list[str] = Field(default_factory=list)
    pass_threshold: bool
