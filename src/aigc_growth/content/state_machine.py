"""全链路状态机：节点常量与流转校验。

每个节点独立为 Celery Task，落库记录状态与产物路径；
任一节点失败按"可重试 / 需人工 / 终止"分级处理，
支持从失败节点断点续跑，不做全链路重跑。
"""
from __future__ import annotations


class TaskState:
    # 主链路
    DISCOVER = "DISCOVER"          # 选题采集
    SCORE = "SCORE"                # 评分排序
    SCRIPT = "SCRIPT"              # 脚本生成(大纲+分镜+口播)
    REVIEW = "REVIEW"              # 合规/人工终审
    RENDER = "RENDER"              # FFmpeg 合成
    UPLOAD = "UPLOAD"              # 上传
    PUBLISHED = "PUBLISHED"        # 已发布
    METRICS = "METRICS"            # 数据回收
    FEEDBACK = "FEEDBACK"          # 反哺选题模型

    # 数字人分支
    AVATAR_SUBMITTED = "AVATAR_SUBMITTED"
    AVATAR_RENDERING = "AVATAR_RENDERING"
    AVATAR_DONE = "AVATAR_DONE"
    AVATAR_FALLBACK = "AVATAR_FALLBACK"   # 降级为 TTS 混剪

    # 异常
    FAILED = "FAILED"
    NEED_HUMAN = "NEED_HUMAN"      # 需人工


# 失败分级：决定重试策略
RETRYABLE_STATES = {TaskState.AVATAR_RENDERING}    # 瞬时错误 → 指数退避重试
HUMAN_STATES = {TaskState.REVIEW, TaskState.NEED_HUMAN}  # 需人工
DLQ_STATES = {TaskState.FAILED}                     # 结构性错误 → 死信队列


def classify_failure(exc: Exception) -> str:
    """错误分类：限流/瞬时 → retry；鉴权 → alert；结构性 → DLQ。"""
    msg = str(exc).lower()
    if any(k in msg for k in ("rate limit", "429", "timeout", "connection")):
        return "retry"
    if any(k in msg for k in ("401", "403", "auth", "token")):
        return "alert"          # 鉴权错误重试无用，转告警
    return "dlq"                # 结构性错误进死信队列
