"""End-to-end тесты всех SSH-операций video-gen-gui против настоящего локального OpenSSH sshd."""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from video_gen_gui.fonts import discover_fonts, render_subtitle_preview_png
from video_gen_gui.models import GuiSettings, SubtitlesSection, VideoGenConfig
from video_gen_gui.remote import (
    build_ssh_cmd,
    check_remote_preflight,
    check_remote_tcp_endpoint,
    clear_remote_cache,
    download_remote_file,
    list_remote_clips,
    list_remote_dir,
    read_remote_text,
    sync_remote_fonts,
    write_remote_text,
)
from video_gen_gui.remote import (
    test_ssh_connection as probe_ssh_connection,
)
from video_gen_gui.runner import CliRunner, probe_environment

SSHD_BIN = shutil.which("sshd")
SSH_KEYGEN_BIN = shutil.which("ssh-keygen")
REAL_VG_DIR = Path(__file__).resolve().parents[2] / "video-gen"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_tcp(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"sshd не открыл порт 127.0.0.1:{port} за {timeout}с")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(scope="module")
def local_sshd(tmp_path_factory: pytest.TempPathFactory) -> Generator[dict[str, Any], None, None]:
    if not SSHD_BIN or not SSH_KEYGEN_BIN:
        pytest.skip("OpenSSH sshd / ssh-keygen недоступны")

    base = tmp_path_factory.mktemp("local_sshd")
    host_key = base / "host_ed25519"
    client_key = base / "client_ed25519"
    wrong_key = base / "wrong_ed25519"

    for key_path in (host_key, client_key, wrong_key):
        subprocess.run(
            [SSH_KEYGEN_BIN, "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
            check=True,
        )

    auth_keys = base / "authorized_keys"
    auth_keys.write_text(client_key.with_suffix(".pub").read_text(encoding="utf-8"), encoding="utf-8")
    auth_keys.chmod(0o600)

    port = _free_port()
    pid_file = base / "sshd.pid"
    cfg_file = base / "sshd_config"
    cfg_file.write_text(
        "\n".join(
            [
                f"Port {port}",
                "ListenAddress 127.0.0.1",
                f"HostKey {host_key}",
                f"PidFile {pid_file}",
                f"AuthorizedKeysFile {auth_keys}",
                "PubkeyAuthentication yes",
                "PasswordAuthentication no",
                "KbdInteractiveAuthentication no",
                "ChallengeResponseAuthentication no",
                "UsePAM no",
                "StrictModes no",
                "AllowAgentForwarding no",
                "AllowTcpForwarding no",
                "PrintMotd no",
                "Subsystem sftp internal-sftp",
                "",
            ]
        ),
        encoding="utf-8",
    )

    proc = subprocess.Popen(
        [SSHD_BIN, "-D", "-e", "-f", str(cfg_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_tcp(port, timeout=6.0)
        yield {
            "host": "127.0.0.1",
            "port": port,
            "user": getpass.getuser(),
            "key": client_key,
            "wrong_key": wrong_key,
            "base": base,
        }
    finally:
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=3.0)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def _ssh_settings(
    sshd: dict[str, Any],
    *,
    remote_vg_dir: str,
    key: Path | None = None,
    port: int | None = None,
) -> GuiSettings:
    return GuiSettings(
        execution_mode="ssh",
        ssh_host=sshd["host"],
        ssh_port=port if port is not None else sshd["port"],
        ssh_user=sshd["user"],
        ssh_key_path=str(key or sshd["key"]),
        remote_video_gen_dir=remote_vg_dir,
    )


def test_build_ssh_cmd_rejects_option_injection() -> None:
    bad = GuiSettings(execution_mode="ssh", ssh_host="-oProxyCommand=id")
    with pytest.raises(ValueError, match="Некорректный SSH-адрес"):
        build_ssh_cmd(bad)

    bad_space = GuiSettings(execution_mode="ssh", ssh_host="127.0.0.1 evil")
    with pytest.raises(ValueError, match="Некорректный SSH-адрес"):
        build_ssh_cmd(bad_space)


@pytest.mark.anyio
async def test_real_ssh_connection_and_probe_video_gen(
    local_sshd: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir(mode=0o700)

    settings = _ssh_settings(local_sshd, remote_vg_dir=str(REAL_VG_DIR))
    ok, msg = await probe_ssh_connection(settings)
    assert ok is True, msg
    assert "Подключено:" in msg

    wrong_key_settings = _ssh_settings(
        local_sshd, remote_vg_dir=str(REAL_VG_DIR), key=local_sshd["wrong_key"]
    )
    ok_bad_key, msg_bad_key = await probe_ssh_connection(wrong_key_settings)
    assert ok_bad_key is False
    assert "Ошибка SSH" in msg_bad_key

    closed_port_settings = _ssh_settings(
        local_sshd, remote_vg_dir=str(REAL_VG_DIR), port=_free_port()
    )
    ok_closed, _ = await probe_ssh_connection(closed_port_settings)
    assert ok_closed is False

    env_status = await probe_environment(tmp_path, settings=settings)
    assert env_status.is_remote is True
    assert env_status.ssh_reachable is True
    assert env_status.video_gen_exists is True
    assert env_status.pyproject_exists is True
    assert env_status.venv_ready is True
    assert env_status.cli_binary_ready is True
    assert env_status.uv_path is not None
    assert env_status.ffmpeg_path is not None


@pytest.mark.anyio
async def test_real_ssh_files_dirs_preflight_clips_and_download(
    local_sshd: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir(mode=0o700)

    remote_root = tmp_path / "remote sandbox" / "проект video-gen"
    (remote_root / "input").mkdir(parents=True)
    (remote_root / "bottom").mkdir(parents=True)
    (remote_root / "output" / "папка 1").mkdir(parents=True)
    (remote_root / "cache").mkdir(parents=True)
    (remote_root / "pyproject.toml").write_text('[project]\nname = "vg"\n', encoding="utf-8")
    (remote_root / "cache" / "stale.json").write_text("{}", encoding="utf-8")

    clip_path = remote_root / "output" / "папка 1" / "клип тест 01.mp4"
    clip_bytes = b"\x00\x00\x00\x18ftypmp42" + os.urandom(8192)
    clip_path.write_bytes(clip_bytes)
    clip_path.with_suffix(".json").write_text(
        json.dumps({"viral_title": "Заголовок", "score": 9, "code": "12345-1"}, ensure_ascii=False),
        encoding="utf-8",
    )

    settings = _ssh_settings(local_sshd, remote_vg_dir=str(remote_root))

    cfg_target = str(remote_root / "sub dir" / "конфиг.json")
    payload = '{"hello": "привет мир"}\n'
    await write_remote_text(settings, cfg_target, payload)
    assert await read_remote_text(settings, cfg_target) == payload

    with pytest.raises(FileNotFoundError):
        await read_remote_text(settings, str(remote_root / "missing.json"))

    resolved_dir, entries = await list_remote_dir(
        settings, str(remote_root / "output" / "папка 1"), allowed_exts=["mp4"]
    )
    assert resolved_dir == str((remote_root / "output" / "папка 1").resolve())
    assert [e["name"] for e in entries] == ["клип тест 01.mp4"]

    with pytest.raises(FileNotFoundError):
        await list_remote_dir(settings, str(remote_root / "does_not_exist"))

    cfg = VideoGenConfig()
    cfg.assets.banner = "banners/missing.mp4"
    cfg.assets.music_dir = "missing_music/"
    warnings = await check_remote_preflight(settings, cfg)
    assert len(warnings) == 2
    assert all("CLI отклонит конфиг" in w for w in warnings)

    out_dir_str, clips = await list_remote_clips(settings, "output/")
    assert out_dir_str == str((remote_root / "output").resolve())
    assert len(clips) == 1
    assert clips[0]["meta"]["viral_title"] == "Заголовок"

    local_dl = tmp_path / "downloads" / "скачанный клип.mp4"
    downloaded = await download_remote_file(settings, clips[0]["abs_path"], local_dl)
    assert downloaded.read_bytes() == clip_bytes

    ok_cache, _ = await clear_remote_cache(settings)
    assert ok_cache is True
    assert not (remote_root / "cache" / "stale.json").exists()
    assert (remote_root / "cache").is_dir()

    non_vg_settings = _ssh_settings(local_sshd, remote_vg_dir=str(tmp_path / "downloads"))
    ok_refuse, msg_refuse = await clear_remote_cache(non_vg_settings)
    assert ok_refuse is False
    assert "не найдена" in msg_refuse


@pytest.mark.anyio
async def test_real_ssh_font_sync_and_tcp_check(
    local_sshd: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir(mode=0o700)

    settings = _ssh_settings(local_sshd, remote_vg_dir=str(REAL_VG_DIR))
    synced_dir = await sync_remote_fonts(settings, "fonts/")
    families, font_map = discover_fonts(synced_dir)
    assert len(families) >= 1
    assert len(font_map) >= 1

    png = render_subtitle_preview_png(SubtitlesSection(font=families[0]), font_map)
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        open_port = int(listener.getsockname()[1])
        ok_open, _ = await check_remote_tcp_endpoint(settings, "127.0.0.1", open_port, timeout=2.0)
        assert ok_open is True

    ok_closed, _ = await check_remote_tcp_endpoint(settings, "127.0.0.1", _free_port(), timeout=2.0)
    assert ok_closed is False


@pytest.mark.anyio
async def test_real_ssh_runner_pipeline_streaming_and_clean_stop(
    local_sshd: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".ssh").mkdir(mode=0o700)

    fake_vg = tmp_path / "fake_remote_vg"
    bin_dir = fake_vg / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    child_pid_file = fake_vg / "child.pid"

    stub_cli = bin_dir / "video-gen"
    stub_cli.write_text(
        f"""#!/usr/bin/env python3
import json, subprocess, sys, time
print(json.dumps({{"event": "pipeline_started", "total": 1, "jobs": ["demo.mp4"]}}), flush=True)
child = subprocess.Popen(["sleep", "60"])
with open({str(child_pid_file)!r}, "w", encoding="utf-8") as fh:
    fh.write(str(child.pid))
try:
    for pct in (25.0, 50.0):
        print(json.dumps({{"event": "render_progress", "clip": "demo.mp4", "pct": pct}}), flush=True)
        time.sleep(0.2)
    time.sleep(60)
finally:
    child.terminate()
""",
        encoding="utf-8",
    )
    stub_cli.chmod(0o755)

    settings = _ssh_settings(local_sshd, remote_vg_dir=str(fake_vg))
    runner = CliRunner()
    events: list[dict[str, Any]] = []
    lines: list[tuple[str, str]] = []

    run_task = asyncio.create_task(
        runner.run_pipeline(
            tmp_path,
            tmp_path / "config.json",
            on_event=events.append,
            on_line=lambda lvl, txt: lines.append((lvl, txt)),
            extra_env={"OPENAI_API_KEY": "sk-test"},
            settings=settings,
            config_json_text=VideoGenConfig().to_json_text(),
        )
    )

    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and not child_pid_file.is_file():
        await asyncio.sleep(0.05)

    assert child_pid_file.is_file(), f"stub CLI не успел стартовать; lines={lines}"
    child_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
    assert any(ev.get("event") == "pipeline_started" for ev in events)

    await runner.stop()
    rc = await asyncio.wait_for(run_task, timeout=10.0)
    assert rc != 0
    assert runner.is_running is False

    await asyncio.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
