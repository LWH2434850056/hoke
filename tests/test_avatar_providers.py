"""数字人 Provider 单测：本地 SadTalker 编排、阿里云万相协议、素材公网化。

全部用假响应/假脚本，不碰真实外部服务、不消耗任何额度。
"""
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aigc_growth.avatar.provider import TaskStatus


class TestMediaIO:
    def test_url_passthrough(self):
        from aigc_growth.avatar.media_io import public_url

        assert public_url("https://a.com/x.png") == "https://a.com/x.png"

    def test_local_without_base_raises(self, monkeypatch):
        from aigc_growth.avatar import media_io

        monkeypatch.setattr(media_io, "get_settings",
                            lambda: SimpleNamespace(media_public_base=""))
        with pytest.raises(RuntimeError, match="公网可达"):
            media_io.public_url("local.png")

    def test_local_mapped_by_base(self, monkeypatch, tmp_path):
        from aigc_growth.avatar import media_io

        monkeypatch.setattr(media_io, "OUTPUT_ROOT", tmp_path)
        monkeypatch.setattr(
            media_io, "get_settings",
            lambda: SimpleNamespace(media_public_base="https://cdn.example.com/m"))
        f = tmp_path / "videos" / "c1" / "voiceover.mp3"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"x")

        assert media_io.public_url(str(f)) == \
            "https://cdn.example.com/m/videos/c1/voiceover.mp3"


class TestAliyunWanProtocol:
    @staticmethod
    def _provider():
        from aigc_growth.avatar.aliyun import AliyunWanProvider

        return AliyunWanProvider("sk-test", "https://dashscope.aliyuncs.com")

    @staticmethod
    def _resp(payload, status_code: int = 200):
        class _R:
            def __init__(self):
                self.status_code = status_code

            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return _R()

    def test_create_remote_parses_task_id(self, monkeypatch):
        from aigc_growth.avatar import aliyun as mod

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **k: self._resp(
                {"output": {"task_id": "abc-123", "task_status": "PENDING"}}))
        assert self._provider()._create_remote("https://i.png", "https://a.mp3") == "abc-123"

    def test_create_remote_raises_without_task_id(self, monkeypatch):
        from aigc_growth.avatar import aliyun as mod

        monkeypatch.setattr(mod.httpx, "post",
                            lambda *a, **k: self._resp({"message": "quota exceeded"}))
        with pytest.raises(RuntimeError, match="配额|quota"):
            self._provider()._create_remote("https://i.png", "https://a.mp3")

    def test_fetch_success(self, monkeypatch):
        from aigc_growth.avatar import aliyun as mod

        monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: self._resp({
            "output": {"task_status": "SUCCEEDED",
                       "results": {"video_url": "https://x/1.mp4"}},
            "usage": {"duration": 10.2},
        }))
        status, url, err = self._provider()._fetch("tid")
        assert (status, url, err) == (TaskStatus.SUCCESS, "https://x/1.mp4", None)

    def test_fetch_failed_carries_message(self, monkeypatch):
        from aigc_growth.avatar import aliyun as mod

        monkeypatch.setattr(mod.httpx, "get", lambda *a, **k: self._resp({
            "output": {"task_status": "FAILED", "code": "InvalidParameter",
                       "message": "audio too long"},
        }))
        status, url, err = self._provider()._fetch("tid")
        assert status == TaskStatus.FAILED
        assert "InvalidParameter" in err and "audio too long" in err

    def test_running_maps_to_processing(self, monkeypatch):
        from aigc_growth.avatar import aliyun as mod

        monkeypatch.setattr(mod.httpx, "get",
                            lambda *a, **k: self._resp({"output": {"task_status": "RUNNING"}}))
        assert self._provider()._fetch("tid")[0] == TaskStatus.PROCESSING

    def test_unknown_task_failed(self):
        res = self._provider().query("nope")
        assert res.status == TaskStatus.FAILED


class TestSadTalkerProvider:
    def test_submit_without_repo_raises(self):
        from aigc_growth.avatar.sadtalker import SadTalkerProvider

        p = SadTalkerProvider("")
        with pytest.raises(RuntimeError, match="未部署"):
            p.submit(script="口播", avatar_id="x.png", voice_id="v1")

    @staticmethod
    def _fake_repo(tmp_path: Path) -> Path:
        repo = tmp_path / "sadtalker"
        repo.mkdir()
        (repo / "inference.py").write_text(
            "import argparse, pathlib\n"
            "ap = argparse.ArgumentParser()\n"
            "ap.add_argument('--driven_audio'); ap.add_argument('--source_image')\n"
            "ap.add_argument('--result_dir'); ap.add_argument('--still', action='store_true')\n"
            "ap.add_argument('--preprocess'); ap.add_argument('--enhancer')\n"
            "a, _ = ap.parse_known_args()\n"
            "d = pathlib.Path(a.result_dir); d.mkdir(parents=True, exist_ok=True)\n"
            "(d / 'shot.mp4').write_bytes(b'mp4')\n",
            encoding="utf-8",
        )
        return repo

    @staticmethod
    def _prepare(monkeypatch, tmp_path):
        """把外部依赖（TTS/ffmpeg/输出根）换成不联网的假实现。"""
        from aigc_growth.avatar import media_io
        from aigc_growth.video import render

        monkeypatch.setattr(media_io, "AVATAR_ROOT", tmp_path / "avatar")
        monkeypatch.setattr(
            media_io, "synthesize_voice",
            lambda script, out_dir, voice_key: _touch(out_dir / "voiceover.mp3"))
        monkeypatch.setattr(media_io, "source_image",
                            lambda avatar_id, out_dir: _touch(out_dir / "src.png"))
        monkeypatch.setattr(render, "to_wav",
                            lambda src, out_path, sample_rate=16000: _touch(out_path))
        monkeypatch.setattr(render, "concat_videos",
                            lambda parts, out_path: _copy_first(parts, out_path))

    def test_full_local_flow(self, monkeypatch, tmp_path):
        from aigc_growth.avatar.sadtalker import SadTalkerProvider

        self._prepare(monkeypatch, tmp_path)
        repo = self._fake_repo(tmp_path)
        p = SadTalkerProvider(str(repo), python_exe=sys.executable, extra_args="--still")

        tid = p.submit(script="一段口播", avatar_id="src.png", voice_id="v1")
        assert tid.startswith("st-")

        res = _poll_until_done(p, tid)
        assert res.status == TaskStatus.SUCCESS
        assert res.video_url.endswith("final.mp4")
        assert Path(res.video_url).exists()

    def test_failed_exit_reports_log_tail(self, monkeypatch, tmp_path):
        from aigc_growth.avatar import media_io
        from aigc_growth.avatar.sadtalker import SadTalkerProvider
        from aigc_growth.video import render

        monkeypatch.setattr(media_io, "AVATAR_ROOT", tmp_path / "avatar")
        monkeypatch.setattr(media_io, "synthesize_voice",
                            lambda *a, **k: _touch(tmp_path / "v.mp3"))
        monkeypatch.setattr(media_io, "source_image",
                            lambda *a, **k: _touch(tmp_path / "s.png"))
        monkeypatch.setattr(render, "to_wav", lambda src, out, **k: _touch(out))

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "inference.py").write_text(
            "raise SystemExit(3)\n", encoding="utf-8")

        p = SadTalkerProvider(str(repo), python_exe=sys.executable)
        tid = p.submit(script="x", avatar_id="s.png", voice_id="v1")
        res = _poll_until_done(p, tid)
        assert res.status == TaskStatus.FAILED
        assert "exit=3" in res.error


class TestXfyunProvider:
    @staticmethod
    def _provider():
        from aigc_growth.avatar.xfyun import XfyunAvatarProvider

        return XfyunAvatarProvider("appid-1", "key-abc", "secret-xyz")

    @staticmethod
    def _resp(payload):
        class _R:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return _R()

    def test_auth_url_signature(self, monkeypatch):
        """签名用例：固定 date，独立重算 signature，验证与讯飞规范一致。"""
        import base64
        import hashlib
        import hmac
        from urllib.parse import quote

        from aigc_growth.avatar import xfyun as mod

        fixed_date = "Wed, 10 Jul 2024 07:35:43 GMT"
        monkeypatch.setattr(mod, "formatdate", lambda **k: fixed_date)

        url = self._provider()._auth_url("/v1/private/video/generate")

        sig_origin = ("host: vms.cn-huadong-1.xf-yun.com\n"
                      f"date: {fixed_date}\n"
                      "POST /v1/private/video/generate HTTP/1.1")
        sig = base64.b64encode(
            hmac.new(b"secret-xyz", sig_origin.encode(), hashlib.sha256)
            .digest()).decode()
        auth_origin = ('api_key="key-abc", algorithm="hmac-sha256", '
                       f'headers="host date request-line", signature="{sig}"')
        expected_auth = base64.b64encode(auth_origin.encode()).decode()

        assert url.startswith("https://vms.cn-huadong-1.xf-yun.com/v1/private/video/generate?")
        assert f"authorization={quote(expected_auth)}" in url
        assert f"date={quote(fixed_date)}" in url
        assert "host=vms.cn-huadong-1.xf-yun.com" in url

    def test_submit_success(self, monkeypatch):
        from aigc_growth.avatar import xfyun as mod

        captured = {}

        def fake_post(url, json=None, **kw):
            captured["body"] = json
            return self._resp({"header": {"code": 0, "message": "success",
                                          "task_id": "vid_001"}})

        monkeypatch.setattr(mod.httpx, "post", fake_post)
        tid = self._provider().submit(script="介绍大模型微调", avatar_id="x",
                                      voice_id="v1")
        assert tid == "vid_001"
        body = captured["body"]
        assert body["header"]["app_id"] == "appid-1"
        assert body["parameter"]["avatar"]["prompt"] == "介绍大模型微调"
        assert 50 <= body["parameter"]["avatar"]["word_count"] <= 300

    def test_submit_business_error_raises(self, monkeypatch):
        from aigc_growth.avatar import xfyun as mod

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **k: self._resp(
                {"header": {"code": 400, "message": "invalid request", "task_id": ""}}))
        with pytest.raises(RuntimeError, match="invalid request"):
            self._provider().submit(script="x", avatar_id="x", voice_id="v1")

    def test_query_processing(self, monkeypatch):
        from aigc_growth.avatar import xfyun as mod

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **k: self._resp(
                {"header": {"code": 0, "task_id": "vid_001", "task_status": "2"}}))
        res = self._provider().query("vid_001")
        assert res.status == TaskStatus.PROCESSING

    def test_query_success_downloads_video(self, monkeypatch, tmp_path):
        from aigc_growth.avatar import media_io
        from aigc_growth.avatar import xfyun as mod

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **k: self._resp({
                "header": {"code": 0, "task_id": "vid_001", "task_status": "4"},
                "payload": {"video": "https://cdn.xfyun.cn/out.mp4",
                            "text": "扩写后的文案"},
            }))
        monkeypatch.setattr(media_io, "download",
                            lambda url, out_dir, name="d": _touch(tmp_path / "final.mp4"))

        res = self._provider().query("vid_001")
        assert res.status == TaskStatus.SUCCESS
        assert res.video_url.endswith("final.mp4")

    def test_query_done_without_video_is_failed(self, monkeypatch):
        from aigc_growth.avatar import xfyun as mod

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **k: self._resp(
                {"header": {"code": 0, "task_id": "vid_001", "task_status": "4"},
                 "payload": {}}))
        res = self._provider().query("vid_001")
        assert res.status == TaskStatus.FAILED

    def test_query_business_error(self, monkeypatch):
        from aigc_growth.avatar import xfyun as mod

        monkeypatch.setattr(
            mod.httpx, "post",
            lambda *a, **k: self._resp(
                {"header": {"code": 500, "message": "get task status failed"}}))
        res = self._provider().query("vid_001")
        assert res.status == TaskStatus.FAILED
        assert "500" in res.error


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path

def _copy_first(parts, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(Path(parts[0]).read_bytes())
    return out_path


def _poll_until_done(provider, task_id, tries: int = 100):
    import time

    for _ in range(tries):
        res = provider.query(task_id)
        if res.status != TaskStatus.PROCESSING:
            return res
        time.sleep(0.05)
    raise AssertionError("任务未在预期时间内结束")
