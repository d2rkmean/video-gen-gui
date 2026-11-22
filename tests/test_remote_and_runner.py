"""Тесты SSH-утилит, удалённого CliRunner и сборки всех 7 экранов Flet в local/ssh режимах."""

from __future__ import annotations

import io
import json
import tarfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from video_gen_gui.models import GuiSettings, VideoGenConfig
from video_gen_gui.remote import (
    check_remote_preflight,
    download_remote_file,
    list_remote_dir,
    read_remote_text,
    relativize_remote_path,
    sh_remote_path,
    sync_remote_fonts,
    write_remote_text,
)
from video_gen_gui.remote import (
    test_ssh_connection as probe_ssh_connection,
)
from video_gen_gui.runner import CliRunner, probe_environment
from video_gen_gui.state import AppState
from video_gen_gui.views.dashboard import DashboardView
from video_gen_gui.views.database import DatabaseView
from video_gen_gui.views.gallery import GalleryView
from video_gen_gui.views.pipeline import PipelineView
from video_gen_gui.views.render import RenderView
from video_gen_gui.views.sources import SourcesView
from video_gen_gui.views.subtitles import SubtitlesView

RUSSO_TTF = Path(__file__).resolve().parents[2] / "video-gen" / "fonts" / "RussoOne.ttf"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _FakeStream:
    def __init__(self, chunks: list[bytes]) -> None:
        self._lines = list(chunks)
        self._buf = b"".join(chunks)
        self._offset = 0

    async def readline(self) -> bytes:
        if not self._lines:
            return b""
        return self._lines.pop(0)

    async def read(self, n: int = -1) -> bytes:
        if self._offset >= len(self._buf):
            return b""
        if n < 0:
            out = self._buf[self._offset :]
            self._offset = len(self._buf)
            return out
        out = self._buf[self._offset : self._offset + n]
        self._offset += len(out)
        return out


class _FakeProcess:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout_bytes: bytes = b"",
        stderr_bytes: bytes = b"",
        stdout_lines: list[bytes] | None = None,
        stderr_lines: list[bytes] | None = None,
        pid: int = 1001,
    ) -> None:
        self.returncode: int | None = None
        self._final_rc = returncode
        self.pid = pid
        self.recorded_stdin: bytes | None = None
        self.killed = False
        self._stdout_bytes = stdout_bytes
        self._stderr_bytes = stderr_bytes
        self.stdout = _FakeStream(
            stdout_lines if stdout_lines is not None else ([stdout_bytes] if stdout_bytes else [])
        )
        self.stderr = _FakeStream(
            stderr_lines if stderr_lines is not None else ([stderr_bytes] if stderr_bytes else [])
        )

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        self.recorded_stdin = input
        self.returncode = self._final_rc
        return self._stdout_bytes, self._stderr_bytes

    async def wait(self) -> int:
        self.returncode = self._final_rc
        return self._final_rc

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _make_ssh_settings(
    *,
    ssh_host: str = "gpu-server",
    ssh_user: str = "ubuntu",
    ssh_port: int = 2222,
    remote_vg_dir: str = "~/projects/video-gen",
    remote_cfg_path: str = "",
) -> GuiSettings:
    return GuiSettings(
        execution_mode="ssh",
        ssh_host=ssh_host,
        ssh_user=ssh_user,
        ssh_port=ssh_port,
        remote_video_gen_dir=remote_vg_dir,
        remote_config_path=remote_cfg_path,
    )


def test_gui_settings_ssh_helpers_and_path_utils() -> None:
    local_cfg = GuiSettings(execution_mode="local", ssh_host="10.0.0.5")
    assert local_cfg.is_remote is False

    s_no_user = GuiSettings(execution_mode="ssh", ssh_host="10.0.0.5", ssh_user="")
    assert s_no_user.is_remote is True
    assert s_no_user.ssh_destination() == "10.0.0.5"

    s_with_user = GuiSettings(execution_mode="ssh", ssh_host="10.0.0.5", ssh_user="ubuntu")
    assert s_with_user.ssh_destination() == "ubuntu@10.0.0.5"

    s_at_in_host = GuiSettings(execution_mode="ssh", ssh_host="root@10.0.0.5", ssh_user="ubuntu")
    assert s_at_in_host.ssh_destination() == "root@10.0.0.5"

    s_empty_host = GuiSettings(execution_mode="ssh", ssh_host="   ")
    assert s_empty_host.ssh_destination() == ""

    assert GuiSettings(remote_video_gen_dir="  ").resolved_remote_video_gen_dir() == "~/projects/video-gen"
    assert (
        GuiSettings(remote_video_gen_dir=" /srv/video-gen ").resolved_remote_video_gen_dir()
        == "/srv/video-gen"
    )

    assert (
        GuiSettings(
            remote_video_gen_dir="~/projects/video-gen/",
            remote_config_path="",
        ).resolved_remote_config_path()
        == "~/projects/video-gen/config.json"
    )
    assert (
        GuiSettings(
            remote_video_gen_dir="/srv/vg",
            remote_config_path="prod/cfg.json",
        ).resolved_remote_config_path()
        == "/srv/vg/prod/cfg.json"
    )
    assert (
        GuiSettings(
            remote_video_gen_dir="/srv/vg",
            remote_config_path="/etc/vg/config.json",
        ).resolved_remote_config_path()
        == "/etc/vg/config.json"
    )
    assert (
        GuiSettings(
            remote_video_gen_dir="/srv/vg",
            remote_config_path="~/my.json",
        ).resolved_remote_config_path()
        == "~/my.json"
    )

    assert sh_remote_path("") == "."
    assert sh_remote_path("~") == '"$HOME"'
    assert sh_remote_path("~/") == '"$HOME"'
    assert sh_remote_path("~/projects/video gen") == '"$HOME"/\'projects/video gen\''
    assert sh_remote_path("/opt/клипы 1/cfg.json") == "'/opt/клипы 1/cfg.json'"

    assert (
        relativize_remote_path("/home/u/video-gen/input/ep01.mp4", "/home/u/video-gen")
        == "input/ep01.mp4"
    )
    assert relativize_remote_path("/home/u/video-gen", "/home/u/video-gen") == "."
    assert relativize_remote_path("/mnt/nas/ep01.mp4", "/home/u/video-gen") == "/mnt/nas/ep01.mp4"
    assert relativize_remote_path("input/ep01.mp4", "/home/u/video-gen") == "input/ep01.mp4"


@pytest.mark.anyio
async def test_ssh_remote_operations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = _make_ssh_settings()

    # 1. test_ssh_connection (успех + ошибка)
    ok_proc = _FakeProcess(returncode=0, stdout_bytes=b"gpu-node\nNVIDIA RTX 4090\n")
    with patch("asyncio.create_subprocess_exec", return_value=ok_proc):
        ok, msg = await probe_ssh_connection(settings)
    assert ok is True
    assert "gpu-node" in msg
    assert "NVIDIA RTX 4090" in msg

    fail_proc = _FakeProcess(returncode=255, stderr_bytes=b"Permission denied")
    with patch("asyncio.create_subprocess_exec", return_value=fail_proc):
        ok_f, msg_f = await probe_ssh_connection(settings)
    assert ok_f is False
    assert "Permission denied" in msg_f

    # 2. read_remote_text
    read_proc = _FakeProcess(returncode=0, stdout_bytes="привет, конфиг!\n".encode())
    with patch("asyncio.create_subprocess_exec", return_value=read_proc):
        text = await read_remote_text(settings, "~/projects/video-gen/config.json")
    assert text == "привет, конфиг!\n"

    missing_proc = _FakeProcess(returncode=44)
    with patch("asyncio.create_subprocess_exec", return_value=missing_proc), pytest.raises(
        FileNotFoundError
    ):
        await read_remote_text(settings, "~/missing.json")

    # 3. write_remote_text
    write_proc = _FakeProcess(returncode=0)
    with patch("asyncio.create_subprocess_exec", return_value=write_proc):
        await write_remote_text(settings, "~/projects/video-gen/config.json", '{"a": "тест"}\n')
    assert write_proc.recorded_stdin == '{"a": "тест"}\n'.encode()

    # 4. check_remote_preflight
    cfg = VideoGenConfig()
    cfg.assets.banner = "banners/promo.mp4"
    preflight_proc = _FakeProcess(
        returncode=0,
        stdout_bytes=json.dumps(["Файл баннера не найден: /srv/vg/banners/promo.mp4"]).encode(),
    )
    with patch("asyncio.create_subprocess_exec", return_value=preflight_proc):
        warnings = await check_remote_preflight(settings, cfg)
    assert len(warnings) == 1
    assert "баннера" in warnings[0]
    assert preflight_proc.recorded_stdin is not None
    sent_payload = json.loads(preflight_proc.recorded_stdin.decode("utf-8"))
    assert sent_payload["banner"] == "banners/promo.mp4"

    # 5. list_remote_dir
    listing = {
        "path": "/home/ubuntu/videos",
        "entries": [
            {"name": "sub", "path": "/home/ubuntu/videos/sub", "is_dir": True, "size": 0},
            {"name": "clip.mp4", "path": "/home/ubuntu/videos/clip.mp4", "is_dir": False, "size": 123},
        ],
    }
    list_proc = _FakeProcess(returncode=0, stdout_bytes=json.dumps(listing).encode("utf-8"))
    with patch("asyncio.create_subprocess_exec", return_value=list_proc):
        resolved_dir, entries = await list_remote_dir(settings, "~/videos", allowed_exts=["mp4"])
    assert resolved_dir == "/home/ubuntu/videos"
    assert len(entries) == 2
    assert entries[1]["name"] == "clip.mp4"


@pytest.mark.anyio
async def test_sync_remote_fonts_extracts_real_ttf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = _make_ssh_settings()

    ttf_bytes = RUSSO_TTF.read_bytes()
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:") as tf:
        info = tarfile.TarInfo(name="RussoOne.ttf")
        info.size = len(ttf_bytes)
        tf.addfile(info, io.BytesIO(ttf_bytes))

        bad_info = tarfile.TarInfo(name="broken.ttf")
        bad_info.size = 14
        tf.addfile(bad_info, io.BytesIO(b"404: Not Found"))

    proc = _FakeProcess(returncode=0, stdout_bytes=tar_buf.getvalue())
    with patch("asyncio.create_subprocess_exec", return_value=proc):
        out_dir = await sync_remote_fonts(settings, "fonts/")

    expected_dir = settings.remote_cache_dir() / "fonts"
    assert out_dir == expected_dir
    extracted = expected_dir / "RussoOne.ttf"
    assert extracted.is_file()
    assert extracted.read_bytes() == ttf_bytes
    assert not (expected_dir / "broken.ttf").exists()


@pytest.mark.anyio
async def test_download_remote_file_cyrillic_and_spaces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = _make_ssh_settings()

    payload = b"\x00\x00\x00\x18ftypmp42" + b"BINARY_VIDEO_DATA" * 100
    proc = _FakeProcess(
        returncode=0,
        stdout_lines=[payload[:64], payload[64:]],
        stderr_bytes=b"",
    )
    local_dest = tmp_path / "готовые клипы" / "клип номер 1.mp4"

    with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
        result = await download_remote_file(
            settings, "/srv/video gen/output/клип номер 1.mp4", local_dest
        )

    assert result == local_dest
    assert local_dest.is_file()
    assert local_dest.read_bytes() == payload
    assert not local_dest.with_name(f".{local_dest.name}.part").exists()
    called_cmd = mock_exec.call_args.args
    assert "sh" in called_cmd


@pytest.mark.anyio
async def test_probe_environment_ssh(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = _make_ssh_settings(ssh_host="render-box", ssh_user="ml")

    probe_json = json.dumps(
        {
            "vg": True,
            "py": True,
            "venv": True,
            "cli": True,
            "pin": "3.12",
            "uv_path": "/home/ml/.local/bin/uv",
            "uv_ver": "uv 0.6.1",
            "ff_path": "/usr/bin/ffmpeg",
            "nvenc": True,
        }
    ).encode("utf-8")

    ok_proc = _FakeProcess(returncode=0, stdout_bytes=probe_json)
    with patch("asyncio.create_subprocess_exec", return_value=ok_proc):
        st = await probe_environment(tmp_path, settings=settings)

    assert st.is_remote is True
    assert st.ssh_reachable is True
    assert st.target_label == "ml@render-box"
    assert st.video_gen_exists is True
    assert st.pyproject_exists is True
    assert st.venv_ready is True
    assert st.cli_binary_ready is True
    assert st.nvenc_available is True
    assert st.uv_version == "uv 0.6.1"

    fail_proc = _FakeProcess(returncode=255)
    with patch("asyncio.create_subprocess_exec", return_value=fail_proc):
        st_fail = await probe_environment(tmp_path, settings=settings)
    assert st_fail.is_remote is True
    assert st_fail.ssh_reachable is False


@pytest.mark.anyio
async def test_cli_runner_install_and_pipeline_ssh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    settings = _make_ssh_settings()
    runner = CliRunner()

    # 1. install_dependencies в SSH-режиме
    install_lines: list[tuple[str, str]] = []
    install_proc = _FakeProcess(
        returncode=0,
        stdout_lines=[b"Resolved 42 packages\n", b"Installed 42 packages\n"],
        stderr_lines=[b"__VG_REMOTE_PID__=7777\n", b"Syncing environment\n"],
    )
    with patch("asyncio.create_subprocess_exec", return_value=install_proc) as mock_install_exec:
        rc = await runner.install_dependencies(
            tmp_path,
            lambda lvl, msg: install_lines.append((lvl, msg)),
            upgrade=True,
            settings=settings,
        )
    assert rc == 0
    assert any("uv sync --upgrade" in msg for _, msg in install_lines)
    assert any("Resolved 42 packages" in msg for _, msg in install_lines)
    assert not any("__VG_REMOTE_PID__" in msg for _, msg in install_lines)
    assert "uv sync --upgrade" in mock_install_exec.call_args.args[-1]

    # 2. run_pipeline в SSH-режиме: write_remote_text + __VG_REMOTE_PID__=4242 + JSON progress
    write_cfg_proc = _FakeProcess(returncode=0)
    ev1 = {"event": "pipeline_started", "total": 1, "jobs": ["ep01.mp4"]}
    ev2 = {"event": "pipeline_finished", "ok": 1, "failed": 0, "broken_videos": 0}
    run_proc = _FakeProcess(
        returncode=0,
        stdout_lines=[
            (json.dumps(ev1) + "\n").encode("utf-8"),
            b"FFmpeg banner render step\n",
            (json.dumps(ev2) + "\n").encode("utf-8"),
        ],
        stderr_lines=[
            b"__VG_REMOTE_PID__=4242\n",
            b"Minor warning from whisper\n",
        ],
    )

    captured_events: list[dict[str, Any]] = []
    captured_lines: list[tuple[str, str]] = []
    observed_pids: list[int | None] = []

    orig_consume = runner._consume_remote_pid_marker

    def _spy_consume(text: str) -> bool:
        consumed = orig_consume(text)
        if consumed:
            observed_pids.append(runner._remote_pid)
        return consumed

    monkeypatch.setattr(runner, "_consume_remote_pid_marker", _spy_consume)

    cfg_json = VideoGenConfig().to_json_text()
    with patch(
        "asyncio.create_subprocess_exec",
        side_effect=[write_cfg_proc, run_proc],
    ):
        rc_run = await runner.run_pipeline(
            tmp_path,
            tmp_path / "config.json",
            on_event=captured_events.append,
            on_line=lambda lvl, msg: captured_lines.append((lvl, msg)),
            extra_env={"OPENAI_API_KEY": "sk-remote"},
            settings=settings,
            config_json_text=cfg_json,
        )

    assert rc_run == 0
    assert write_cfg_proc.recorded_stdin == cfg_json.encode("utf-8")
    assert observed_pids == [4242]
    assert captured_events == [ev1, ev2]
    assert not any("__VG_REMOTE_PID__" in msg for _, msg in captured_lines)
    assert any("FFmpeg banner render step" in msg for _, msg in captured_lines)
    assert any(lvl == "WARNING" and "Minor warning" in msg for lvl, msg in captured_lines)


class _Buildable(Protocol):
    def build(self) -> ft.Control: ...


@pytest.mark.parametrize("execution_mode", ["local", "ssh"])
def test_all_seven_views_build_in_local_and_ssh_modes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    execution_mode: str,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    fake_page = MagicMock(spec=ft.Page)
    state = AppState()
    state.settings.execution_mode = "ssh" if execution_mode == "ssh" else "local"
    state.settings.ssh_host = "gpu-server"
    state.settings.ssh_user = "ubuntu"

    picker = ft.FilePicker()
    clip = ft.Clipboard()
    noop: Callable[[], None] = lambda: None  # noqa: E731

    views: list[_Buildable] = [
        DashboardView(
            fake_page,
            state,
            picker,
            clip,
            noop,
            noop,
            lambda _: None,
            noop,
            noop,
        ),
        SourcesView(fake_page, state, picker),
        PipelineView(fake_page, state, picker),
        RenderView(fake_page, state, picker),
        SubtitlesView(fake_page, state, picker),
        DatabaseView(fake_page, state),
        GalleryView(fake_page, state, clip),
    ]
    for view in views:
        ctrl = view.build()
        assert isinstance(ctrl, ft.Control)
