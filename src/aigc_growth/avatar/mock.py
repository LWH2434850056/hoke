"""Mock 数字人 Provider：无 key 演示全链路，状态机行为与真实一致。"""
from __future__ import annotations

import time
import uuid

from aigc_growth.avatar.provider import AvatarTaskResult, AvatarProvider, TaskStatus


class MockAvatarProvider(AvatarProvider):
    """内存态任务表：submit 后需轮询 2 次才 SUCCESS，模拟异步渲染。"""

    name = "mock"

    def __init__(self, fail_first: int = 0):
        self._tasks: dict[str, dict] = {}
        self._poll_counts: dict[str, int] = {}
        self._fail_first = fail_first   # 前 N 个任务失败，用于测降级

    def submit(self, *, script: str, avatar_id: str, voice_id: str,
               audio_url: str | None = None, ratio: str = "9:16",
               subtitle: bool = True) -> str:
        task_id = f"mock-{uuid.uuid4().hex[:12]}"
        will_fail = len(self._tasks) < self._fail_first
        self._tasks[task_id] = {"failed": will_fail}
        self._poll_counts[task_id] = 0
        return task_id

    def query(self, task_id: str) -> AvatarTaskResult:
        if task_id not in self._tasks:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="unknown task")
        self._poll_counts[task_id] += 1
        t = self._tasks[task_id]
        if t["failed"]:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="mock_render_failed")
        if self._poll_counts[task_id] < 2:   # 第一次轮询还在渲染
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.PROCESSING)
        return AvatarTaskResult(
            task_id=task_id,
            status=TaskStatus.SUCCESS,
            video_url=f"https://mock-oss.local/avatar/{task_id}.mp4",
        )


class InstantMockProvider(MockAvatarProvider):
    """submit 即成功：给单测用，免去轮询等待。"""

    name = "mock-instant"

    def query(self, task_id: str) -> AvatarTaskResult:
        if task_id not in self._tasks:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="unknown task")
        t = self._tasks[task_id]
        if t["failed"]:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="mock_render_failed")
        return AvatarTaskResult(
            task_id=task_id,
            status=TaskStatus.SUCCESS,
            video_url=f"https://mock-oss.local/avatar/{task_id}.mp4",
        )
