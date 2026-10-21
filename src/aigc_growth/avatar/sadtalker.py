"""本地 SadTalker Provider：图片 + 音频 → 对口型说话视频。

零成本、无额度上限、素材不出本机，代价是需要 GPU 与本地部署。
本模块只做「编排」——用官方推理脚本 inference.py，不重写模型。

部署（一次性）：
    git clone https://github.com/OpenTalker/SadTalker
    cd SadTalker && pip install -r requirements.txt   # 建议独立环境
    然后配置 SADTALKER_DIR 指向该目录。
"""
from __future__ import annotations

import logging
import subprocess
import sys
import uuid
from pathlib import Path

from aigc_growth.avatar import media_io
from aigc_growth.avatar.provider import AvatarTaskResult, AvatarProvider, TaskStatus
from aigc_growth.video import render

logger = logging.getLogger(__name__)


class SadTalkerProvider(AvatarProvider):
    name = "sadtalker"

    def __init__(self, repo_dir: str, python_exe: str = "",
                 extra_args: str = "", voice_key: str = "female_warm"):
        self._repo = Path(repo_dir) if repo_dir else None
        self._python = python_exe or sys.executable
        self._extra_args = [a for a in extra_args.split() if a]
        self._voice_key = voice_key
        self._jobs: dict[str, dict] = {}

    def submit(self, *, script: str, avatar_id: str, voice_id: str,
               audio_url: str | None = None, ratio: str = "9:16",
               subtitle: bool = True) -> str:
        if self._repo is None or not (self._repo / "inference.py").exists():
            raise RuntimeError(
                "SadTalker 未部署：请克隆 SadTalker 仓库并配置 SADTALKER_DIR"
                "（需含 inference.py）"
            )

        task_id = f"st-{uuid.uuid4().hex[:12]}"
        wd = media_io.work_dir(task_id)

        image = media_io.source_image(avatar_id, wd)
        if audio_url:
            audio = (media_io.download(audio_url, wd, name="voiceover")
                     if media_io.is_url(audio_url) else Path(audio_url))
        else:
            audio = media_io.synthesize_voice(script, wd, voice_id or self._voice_key)

        # SadTalker 对 16k 单声道 wav 最稳，mp3 先转码
        wav = render.to_wav(audio, wd / "voiceover.wav")
        result_dir = wd / "result"
        result_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            self._python, "inference.py",
            "--driven_audio", str(wav),
            "--source_image", str(image),
            "--result_dir", str(result_dir),
            *self._extra_args,
        ]
        log_path = wd / "sadtalker.log"
        log_file = log_path.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            cmd, cwd=str(self._repo), stdout=log_file,
            stderr=subprocess.STDOUT, text=True,
        )
        logger.info("SadTalker 已启动 task=%s pid=%s", task_id, proc.pid)

        self._jobs[task_id] = {
            "proc": proc, "log": log_path, "result_dir": result_dir, "wd": wd,
        }
        return task_id

    def query(self, task_id: str) -> AvatarTaskResult:
        job = self._jobs.get(task_id)
        if job is None:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.FAILED,
                                    error="unknown task")

        code = job["proc"].poll()
        if code is None:
            return AvatarTaskResult(task_id=task_id, status=TaskStatus.PROCESSING)

        if code != 0:
            tail = _tail(job["log"])
            return AvatarTaskResult(
                task_id=task_id, status=TaskStatus.FAILED,
                error=f"sadtalker exit={code}: {tail}",
            )

        produced = sorted(job["result_dir"].glob("*.mp4"))
        if not produced:
            return AvatarTaskResult(
                task_id=task_id, status=TaskStatus.FAILED,
                error=f"sadtalker 无产物: {_tail(job['log'])}",
            )

        final = render.concat_videos(produced, job["wd"] / "final.mp4")
        return AvatarTaskResult(task_id=task_id, status=TaskStatus.SUCCESS,
                                video_url=str(final))


def _tail(log_path: Path, n: int = 5) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return " | ".join(lines[-n:])
    except OSError:
        return ""
