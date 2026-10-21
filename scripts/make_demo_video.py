"""演示脚本：一段口播 → 真实 mp4 成片（edge-tts + Whisper + FFmpeg）。

用法：
    python scripts/make_demo_video.py
前置：本机已安装 ffmpeg（PATH 中可找到）
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aigc_growth.video.pipeline import render_with_tts  # noqa: E402
from aigc_growth.video.render import check_ffmpeg  # noqa: E402

DEMO_VOICEOVER = (
    "获客难、写稿慢、爆款无法复现？这三个问题，一条流水线全部解决。"
    "我们把选题、文案、配音、字幕、合成做成全自动：AI 按热点自动选题，"
    "大模型按爆款模板写稿，数字人出镜配音，成片直接定时发布。"
    "数据自动回流，哪类选题完播率高，下一轮就多产哪类。"
    "单条成本从三小时压到十分钟。评论区扣 1，送你完整方案。"
)


def main() -> None:
    if not check_ffmpeg():
        print("错误：未找到 ffmpeg，请先安装并加入 PATH")
        sys.exit(1)

    print("[演示] 开始生成视频：口播 → TTS → 字幕对齐 → FFmpeg 合成")
    final = render_with_tts("demo-001", DEMO_VOICEOVER)
    print(f"[演示] 完成！成片: {final}")
    print(f"[演示] 同目录含 cover.jpg / subs.srt / voiceover.mp3 中间产物")


if __name__ == "__main__":
    main()
