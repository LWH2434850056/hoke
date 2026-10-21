"""数字人能力抽象层。

先定义抽象层再填厂商实现——换厂商只改一个实现类，业务代码不动。
（各家字段不统一，直接写死会把自己锁死。）
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class TaskStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class AvatarTaskResult:
    task_id: str
    status: TaskStatus
    video_url: str | None = None
    error: str | None = None


class AvatarProvider:
    """数字人 Provider 协议：submit 返回 task_id，query 轮询状态。"""

    name: str = "base"

    def submit(
        self,
        *,
        script: str,
        avatar_id: str,
        voice_id: str,
        audio_url: str | None = None,   # 也可走音频驱动
        ratio: str = "9:16",
        subtitle: bool = True,
    ) -> str:
        raise NotImplementedError

    def query(self, task_id: str) -> AvatarTaskResult:
        raise NotImplementedError
