"""FFmpeg 视频合成：9:16 竖版 + 字幕烧录 + BGM 混音 + 封面抽帧。

滤镜链批量合成，CPU 密集 → 独立 cpu_queue。
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


def _ffmpeg_bin() -> str:
    """优先 PATH 中的 ffmpeg，否则用 imageio-ffmpeg 自带静态二进制。"""
    path = shutil.which("ffmpeg")
    if path:
        return path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError as exc:
        raise RuntimeError(
            "未找到 ffmpeg：请安装系统 ffmpeg 或 `pip install imageio-ffmpeg`"
        ) from exc


def _run(cmd: list[str], cwd: str | None = None) -> None:
    cmd[0] = _ffmpeg_bin()          # 替换为实际可执行路径
    logger.debug("FFmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg 失败: {proc.stderr[-800:]}")


def check_ffmpeg() -> bool:
    try:
        return bool(_ffmpeg_bin())
    except RuntimeError:
        return False


def probe_duration(media: Path) -> float:
    """探测音视频时长（秒）。无 ffprobe 时用 ffmpeg -i 解析 stderr。"""
    proc = subprocess.run(
        [_ffmpeg_bin(), "-i", str(media)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    import re

    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    if not m:
        raise RuntimeError(f"无法解析时长: {media}")
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + mnt * 60 + s


def make_background(bg_src: Path | None, out_path: Path, w: int = 1080, h: int = 1920,
                    duration: float = 15.0) -> Path:
    """生成 9:16 背景视频：有素材则裁剪缩放，无素材生成纯色渐变底。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if bg_src and bg_src.exists():
        _run([
            "ffmpeg", "-y", "-i", str(bg_src),
            "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
            "-t", str(duration), "-an",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(out_path),
        ])
    else:
        # 无素材兜底：深蓝渐变纯色底（demo 可用）
        _run([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"gradients=s={w}x{h}:c0=#1a2a6c:c1=#2E4374:d={duration}",
            "-c:v", "libx264", "-preset", "fast", "-pix_fmt", "yuv420p",
            str(out_path),
        ])
    return out_path


def compose(
    *,
    background: Path,
    audio: Path,
    srt: Path | None,
    out_path: Path,
    bgm: Path | None = None,
    bgm_volume: float = 0.15,
) -> Path:
    """合成成片：背景 + 配音 + 字幕烧录 + BGM 低音量混音。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["ffmpeg", "-y", "-i", str(background), "-i", str(audio)]
    if bgm and bgm.exists():
        cmd += ["-i", str(bgm)]

    # 字幕烧录：Windows 盘符含冒号，libavfilter 转义易碎。
    # 稳妥做法：把 ffmpeg 工作目录切到字幕目录，直接用文件名。
    cwd = None
    vf = ""
    if srt and srt.exists():
        cwd = str(srt.parent)
        vf = f"subtitles={srt.name}:force_style='FontSize=14,MarginV=160'"

    if bgm and bgm.exists():
        amix = f"[1:a]volume=1.0[a1];[2:a]volume={bgm_volume}[a2];[a1][a2]amix=inputs=2:duration=first[aout]"
        cmd += ["-filter_complex", amix, "-map", "0:v", "-map", "[aout]"]
    else:
        cmd += ["-map", "0:v", "-map", "1:a"]

    if vf:
        cmd += ["-vf", vf]
    cmd += ["-shortest", "-c:v", "libx264", "-preset", "fast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_path)]
    _run(cmd, cwd=cwd)
    return out_path


def extract_cover(video: Path, out_path: Path, at_sec: float = 1.0) -> Path:
    """封面抽帧。"""
    _run([
        "ffmpeg", "-y", "-ss", str(at_sec), "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(out_path),
    ])
    return out_path


def to_wav(src: Path, out_path: Path, sample_rate: int = 16000) -> Path:
    """转 16k 单声道 wav：本地数字人模型（SadTalker）对 wav 支持最稳。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run([
        "ffmpeg", "-y", "-i", str(src), "-ac", "1", "-ar", str(sample_rate),
        "-c:a", "pcm_s16le", str(out_path),
    ])
    return out_path


def split_audio(src: Path, out_dir: Path, max_sec: float = 18.0) -> list[Path]:
    """按时长切分音频，供有单次时长上限的云端接口分段调用。

    用 FFmpeg segment 分段器（不重编码，切得快）。返回值按序排列。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = src.suffix or ".mp3"
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-f", "segment", "-segment_time", str(max_sec),
        "-c", "copy", str(out_dir / f"part%03d{suffix}"),
    ])
    parts = sorted(out_dir.glob(f"part*{suffix}"))
    if not parts:
        raise RuntimeError(f"音频切分失败: {src}")
    return parts


def concat_videos(parts: list[Path], out_path: Path) -> Path:
    """按序拼接视频段（同源同参，优先流拷贝，失败再重编码）。"""
    if not parts:
        raise ValueError("没有可拼接的视频段")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if len(parts) == 1:
        shutil.copyfile(parts[0], out_path)
        return out_path

    list_file = out_path.parent / "concat.txt"
    list_file.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
    )
    base = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    try:
        _run(base + ["-c", "copy", str(out_path)])
    except RuntimeError:
        logger.warning("流拷贝拼接失败，改用重编码")
        _run(base + ["-c:v", "libx264", "-preset", "fast",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", str(out_path)])
    return out_path
