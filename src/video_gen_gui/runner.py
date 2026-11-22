"""Асинхронный менеджер окружения, установки зависимостей (uv sync) и запуска CLI video-gen (локально и по SSH)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from video_gen_gui.models import GuiSettings
from video_gen_gui.remote import (
    build_ssh_cmd,
    run_ssh_command,
    sh_remote_path,
    write_remote_text,
)

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REMOTE_PID_PREFIX = "__VG_REMOTE_PID__="


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


@dataclass(slots=True)
class EnvironmentStatus:
    video_gen_exists: bool
    pyproject_exists: bool
    venv_ready: bool
    cli_binary_ready: bool
    uv_path: str | None
    uv_version: str
    ffmpeg_path: str | None
    nvenc_available: bool
    python_version_pin: str
    is_remote: bool = False
    ssh_reachable: bool = True
    target_label: str = "local"


def build_clean_env(
    video_gen_dir: Path,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Формирует изолированное окружение для дочернего uv/video-gen процесса."""
    env = dict(os.environ)
    for var in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
        env.pop(var, None)

    env["PYTHONUNBUFFERED"] = "1"
    env["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"

    # Подключаем CUDA библиотеки (cuBLAS / cuDNN) из .venv проекта video-gen, как в run_farm.sh
    venv_lib_root = video_gen_dir / ".venv" / "lib"
    nvidia_lib_dirs: list[str] = []
    if venv_lib_root.is_dir():
        for py_dir in sorted(venv_lib_root.glob("python*")):
            nvidia_root = py_dir / "site-packages" / "nvidia"
            if nvidia_root.is_dir():
                for sub_lib in sorted(nvidia_root.glob("*/lib")):
                    if sub_lib.is_dir():
                        nvidia_lib_dirs.append(str(sub_lib))
    if nvidia_lib_dirs:
        existing_ld = env.get("LD_LIBRARY_PATH", "")
        joined = ":".join(nvidia_lib_dirs)
        env["LD_LIBRARY_PATH"] = f"{joined}:{existing_ld}" if existing_ld else joined

    if extra_env:
        for k, v in extra_env.items():
            if v:
                env[k] = v
    return env


def _unreachable_remote_status(target_label: str) -> EnvironmentStatus:
    return EnvironmentStatus(
        video_gen_exists=False,
        pyproject_exists=False,
        venv_ready=False,
        cli_binary_ready=False,
        uv_path=None,
        uv_version="",
        ffmpeg_path=None,
        nvenc_available=False,
        python_version_pin=">=3.12",
        is_remote=True,
        ssh_reachable=False,
        target_label=target_label,
    )


async def _probe_remote_environment(settings: GuiSettings) -> EnvironmentStatus:
    target_label = settings.ssh_destination() or "ssh"
    vg_q = sh_remote_path(settings.resolved_remote_video_gen_dir())
    script = (
        'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
        f"vg={vg_q}; "
        'vg_exists=0; py_exists=0; venv_ready=0; cli_ready=0; py_pin=">=3.12"; '
        'if [ -d "$vg" ]; then vg_exists=1; fi; '
        'if [ -f "$vg/pyproject.toml" ]; then py_exists=1; fi; '
        'if [ -f "$vg/.venv/bin/python" ]; then venv_ready=1; fi; '
        'if [ -f "$vg/.venv/bin/video-gen" ]; then cli_ready=1; fi; '
        'if [ -f "$vg/.python-version" ]; then py_pin=$(tr -d "\\r\\n" < "$vg/.python-version"); fi; '
        'uv_path=$(command -v uv 2>/dev/null || true); '
        'uv_ver=""; '
        'if [ -n "$uv_path" ]; then uv_ver=$("$uv_path" --version 2>/dev/null | head -n 1 || true); fi; '
        'ff_path=$(command -v ffmpeg 2>/dev/null || true); '
        'nvenc=0; '
        'if [ -n "$ff_path" ]; then '
        '  if "$ff_path" -hide_banner -encoders 2>/dev/null | grep -q "h264_nvenc"; then nvenc=1; fi; '
        'fi; '
        'python3 -c '
        '"import json, sys; print(json.dumps({'
        "'vg': sys.argv[1] == '1', 'py': sys.argv[2] == '1', 'venv': sys.argv[3] == '1', "
        "'cli': sys.argv[4] == '1', 'pin': sys.argv[5], 'uv_path': sys.argv[6] or None, "
        "'uv_ver': sys.argv[7], 'ff_path': sys.argv[8] or None, 'nvenc': sys.argv[9] == '1'"
        '}))" '
        '"$vg_exists" "$py_exists" "$venv_ready" "$cli_ready" "$py_pin" '
        '"$uv_path" "$uv_ver" "$ff_path" "$nvenc"'
    )
    try:
        rc, out, _err = await run_ssh_command(settings, script, timeout=15.0)
    except (ValueError, TimeoutError, OSError):
        return _unreachable_remote_status(target_label)

    if rc != 0:
        return _unreachable_remote_status(target_label)

    try:
        data = json.loads(out.strip().splitlines()[-1])
    except Exception:
        return _unreachable_remote_status(target_label)

    return EnvironmentStatus(
        video_gen_exists=bool(data.get("vg")),
        pyproject_exists=bool(data.get("py")),
        venv_ready=bool(data.get("venv")),
        cli_binary_ready=bool(data.get("cli")),
        uv_path=data.get("uv_path"),
        uv_version=str(data.get("uv_ver") or ""),
        ffmpeg_path=data.get("ff_path"),
        nvenc_available=bool(data.get("nvenc")),
        python_version_pin=str(data.get("pin") or ">=3.12"),
        is_remote=True,
        ssh_reachable=True,
        target_label=target_label,
    )


async def probe_environment(
    video_gen_dir: Path,
    settings: GuiSettings | None = None,
) -> EnvironmentStatus:
    """Асинхронно проверяет готовность папки video-gen, uv, .venv и FFmpeg NVENC."""
    if settings is not None and settings.is_remote:
        return await _probe_remote_environment(settings)

    vg_exists = video_gen_dir.is_dir()
    pyproject_exists = (video_gen_dir / "pyproject.toml").is_file()
    venv_python = video_gen_dir / ".venv" / "bin" / "python"
    cli_bin = video_gen_dir / ".venv" / "bin" / "video-gen"
    venv_ready = venv_python.is_file()
    cli_ready = cli_bin.is_file()

    py_pin_file = video_gen_dir / ".python-version"
    py_pin = py_pin_file.read_text(encoding="utf-8").strip() if py_pin_file.is_file() else ">=3.12"

    uv_path = shutil.which("uv")
    uv_version = ""
    if uv_path:
        with contextlib.suppress(Exception):
            proc = await asyncio.create_subprocess_exec(
                uv_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            uv_version = out.decode("utf-8", errors="replace").strip()

    ffmpeg_path = shutil.which("ffmpeg")
    nvenc_available = False
    if ffmpeg_path:
        with contextlib.suppress(Exception):
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_path,
                "-hide_banner",
                "-encoders",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            nvenc_available = b"h264_nvenc" in out

    return EnvironmentStatus(
        video_gen_exists=vg_exists,
        pyproject_exists=pyproject_exists,
        venv_ready=venv_ready,
        cli_binary_ready=cli_ready,
        uv_path=uv_path,
        uv_version=uv_version,
        ffmpeg_path=ffmpeg_path,
        nvenc_available=nvenc_available,
        python_version_pin=py_pin,
        is_remote=False,
        ssh_reachable=True,
        target_label="local",
    )


async def check_tcp_endpoint(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str]:
    """Проверяет сетевую доступность TCP-хоста (например PostgreSQL)."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()
        return True, f"Соединение с {host}:{port} успешно установлено"
    except TimeoutError:
        return False, f"Таймаут подключения к {host}:{port} ({timeout:.1f} с)"
    except OSError as exc:
        return False, f"Ошибка подключения к {host}:{port}: {exc}"


def parse_json_progress_line(line: str) -> dict[str, Any] | None:
    """Парсит строку JSON-события из stdout `video-gen --json-progress`, если это валидный объект."""
    stripped = line.strip()
    if not (stripped.startswith("{") and stripped.endswith("}")):
        return None
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("event"), str):
        return payload
    return None


class CliRunner:
    """Управляет жизненным циклом дочернего процесса установки зависимостей и CLI video-gen."""

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._stopping: bool = False
        self._remote_settings: GuiSettings | None = None
        self._remote_pid: int | None = None

    @property
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    def _consume_remote_pid_marker(self, text: str) -> bool:
        """Перехватывает маркер `__VG_REMOTE_PID__=<pid>` из stderr удалённого процесса."""
        if not text.startswith(REMOTE_PID_PREFIX):
            return False
        raw_pid = text[len(REMOTE_PID_PREFIX) :].strip()
        if raw_pid.isdigit():
            self._remote_pid = int(raw_pid)
        return True

    async def install_dependencies(
        self,
        video_gen_dir: Path,
        on_line: Callable[[str, str], None],
        *,
        upgrade: bool = False,
        settings: GuiSettings | None = None,
    ) -> int:
        """Запускает `uv sync` в директории video-gen (локально или по SSH) и стримит вывод."""
        if self.is_running:
            raise RuntimeError("Уже выполняется другой процесс")

        if settings is not None and settings.is_remote:
            return await self._install_dependencies_remote(settings, on_line, upgrade=upgrade)

        uv_bin = shutil.which("uv")
        if not uv_bin:
            on_line("ERROR", "Утилита 'uv' не найдена в PATH. Установите uv: https://docs.astral.sh/uv/")
            return 127

        if not (video_gen_dir / "pyproject.toml").is_file():
            on_line("ERROR", f"Файл pyproject.toml не найден в {video_gen_dir}")
            return 2

        cmd = [uv_bin, "sync"]
        if upgrade:
            cmd.append("--upgrade")

        on_line("INFO", f"$ {' '.join(cmd)}  (cwd={video_gen_dir})")
        self._stopping = False
        self._remote_settings = None
        self._remote_pid = None
        env = build_clean_env(video_gen_dir)

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(video_gen_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        async def _pump(stream: asyncio.StreamReader | None, default_level: str) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip()
                if text:
                    on_line(default_level, text)

        try:
            await asyncio.gather(
                _pump(self._proc.stdout, "INFO"),
                _pump(self._proc.stderr, "INFO"),
            )
            return await self._proc.wait()
        finally:
            self._proc = None

    async def _install_dependencies_remote(
        self,
        settings: GuiSettings,
        on_line: Callable[[str, str], None],
        *,
        upgrade: bool,
    ) -> int:
        try:
            ssh_base = build_ssh_cmd(settings)
        except ValueError as exc:
            on_line("ERROR", str(exc))
            return 2

        vg_raw = settings.resolved_remote_video_gen_dir()
        vg_q = sh_remote_path(vg_raw)
        sync_cmd = "uv sync --upgrade" if upgrade else "uv sync"
        script = (
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
            f"vg={vg_q}; "
            'if [ ! -f "$vg/pyproject.toml" ]; then '
            '  echo "Файл pyproject.toml не найден в $vg" >&2; exit 2; '
            "fi; "
            'if ! command -v uv >/dev/null 2>&1; then '
            '  echo "Утилита uv не найдена в PATH на удалённом сервере" >&2; exit 127; '
            "fi; "
            'cd "$vg" || exit 2; '
            'printf "__VG_REMOTE_PID__=%s\\n" "$$" >&2; '
            f"exec {sync_cmd}"
        )

        on_line("INFO", f"$ ssh {settings.ssh_destination()} 'cd {vg_raw} && {sync_cmd}'")
        self._stopping = False
        self._remote_settings = settings
        self._remote_pid = None

        self._proc = await asyncio.create_subprocess_exec(
            *ssh_base,
            "sh",
            "-c",
            shlex.quote(script),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )

        async def _pump(stream: asyncio.StreamReader | None, *, check_pid: bool) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip()
                if not text:
                    continue
                if check_pid and self._consume_remote_pid_marker(text):
                    continue
                on_line("INFO", text)

        try:
            await asyncio.gather(
                _pump(self._proc.stdout, check_pid=False),
                _pump(self._proc.stderr, check_pid=True),
            )
            return await self._proc.wait()
        finally:
            self._proc = None
            self._remote_settings = None
            self._remote_pid = None

    async def run_pipeline(
        self,
        video_gen_dir: Path,
        config_path: Path,
        on_event: Callable[[dict[str, Any]], None],
        on_line: Callable[[str, str], None],
        *,
        extra_env: dict[str, str] | None = None,
        settings: GuiSettings | None = None,
        config_json_text: str | None = None,
    ) -> int:
        """Запускает `video-gen <config.json> --json-progress` (локально или по SSH)."""
        if self.is_running:
            raise RuntimeError("Процесс уже запущен")

        if settings is not None and settings.is_remote:
            return await self._run_pipeline_remote(
                settings,
                config_path,
                on_event,
                on_line,
                extra_env=extra_env,
                config_json_text=config_json_text,
            )

        uv_bin = shutil.which("uv")
        cli_bin = video_gen_dir / ".venv" / "bin" / "video-gen"

        if cli_bin.is_file():
            cmd = [str(cli_bin), str(config_path), "--json-progress"]
        elif uv_bin:
            cmd = [uv_bin, "run", "video-gen", str(config_path), "--json-progress"]
        else:
            on_line("ERROR", "Не найден ни .venv/bin/video-gen, ни uv в PATH")
            return 127

        on_line("INFO", f"$ {' '.join(cmd)}  (cwd={video_gen_dir})")
        self._stopping = False
        self._remote_settings = None
        self._remote_pid = None
        env = build_clean_env(video_gen_dir, extra_env=extra_env)
        env["VIDEOGEN_JSON_PROGRESS"] = "1"

        self._proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(video_gen_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=1024 * 1024,
        )

        async def _read_stdout(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip()
                if not text:
                    continue
                ev = parse_json_progress_line(text)
                if ev is not None:
                    on_event(ev)
                else:
                    on_line("INFO", text)

        async def _read_stderr(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip()
                if text:
                    level = "ERROR" if "error" in text.lower() or "traceback" in text.lower() else "WARNING"
                    on_line(level, text)

        try:
            await asyncio.gather(
                _read_stdout(self._proc.stdout),
                _read_stderr(self._proc.stderr),
            )
            return await self._proc.wait()
        finally:
            self._proc = None

    async def _run_pipeline_remote(
        self,
        settings: GuiSettings,
        config_path: Path,
        on_event: Callable[[dict[str, Any]], None],
        on_line: Callable[[str, str], None],
        *,
        extra_env: dict[str, str] | None,
        config_json_text: str | None,
    ) -> int:
        try:
            ssh_base = build_ssh_cmd(settings)
        except ValueError as exc:
            on_line("ERROR", str(exc))
            return 2

        payload_text = (
            config_json_text
            if config_json_text is not None
            else config_path.read_text(encoding="utf-8")
        )
        remote_cfg = settings.resolved_remote_config_path()
        try:
            await write_remote_text(settings, remote_cfg, payload_text)
        except (ValueError, TimeoutError, RuntimeError, OSError) as exc:
            on_line("ERROR", f"Не удалось загрузить конфиг на сервер: {exc}")
            return 2

        vg_raw = settings.resolved_remote_video_gen_dir()
        vg_q = sh_remote_path(vg_raw)
        cfg_q = sh_remote_path(remote_cfg)

        extra_exports: list[str] = []
        if extra_env:
            for k, v in extra_env.items():
                if v and ENV_KEY_RE.match(k):
                    extra_exports.append(f"export {k}={shlex.quote(v)}")
        exports_block = "; ".join(extra_exports)
        if exports_block:
            exports_block += "; "

        script = (
            'export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; '
            'unset VIRTUAL_ENV PYTHONPATH PYTHONHOME; '
            'export PYTHONUNBUFFERED=1 PYGAME_HIDE_SUPPORT_PROMPT=1 VIDEOGEN_JSON_PROGRESS=1; '
            f"{exports_block}"
            f"vg={vg_q}; cfg={cfg_q}; "
            'cd "$vg" || { echo "Папка video-gen не найдена: $vg" >&2; exit 2; }; '
            'nvidia_libs=""; '
            'for d in "$vg"/.venv/lib/python*/site-packages/nvidia/*/lib; do '
            '  if [ -d "$d" ]; then nvidia_libs="${nvidia_libs:+$nvidia_libs:}$d"; fi; '
            "done; "
            'if [ -n "$nvidia_libs" ]; then '
            '  export LD_LIBRARY_PATH="${nvidia_libs}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"; '
            "fi; "
            'printf "__VG_REMOTE_PID__=%s\\n" "$$" >&2; '
            'if [ -x "$vg/.venv/bin/video-gen" ]; then '
            '  exec "$vg/.venv/bin/video-gen" "$cfg" --json-progress; '
            'elif command -v uv >/dev/null 2>&1; then '
            '  exec uv run video-gen "$cfg" --json-progress; '
            "else "
            '  echo "Не найден ни .venv/bin/video-gen, ни uv в PATH на сервере" >&2; exit 127; '
            "fi"
        )

        on_line(
            "INFO",
            f"$ ssh {settings.ssh_destination()} 'cd {vg_raw} && video-gen {remote_cfg} --json-progress'",
        )
        self._stopping = False
        self._remote_settings = settings
        self._remote_pid = None

        self._proc = await asyncio.create_subprocess_exec(
            *ssh_base,
            "sh",
            "-c",
            shlex.quote(script),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            limit=1024 * 1024,
        )

        async def _read_stdout(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip()
                if not text:
                    continue
                ev = parse_json_progress_line(text)
                if ev is not None:
                    on_event(ev)
                else:
                    on_line("INFO", text)

        async def _read_stderr(stream: asyncio.StreamReader | None) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    break
                text = strip_ansi(raw.decode("utf-8", errors="replace")).rstrip()
                if not text:
                    continue
                if self._consume_remote_pid_marker(text):
                    continue
                level = "ERROR" if "error" in text.lower() or "traceback" in text.lower() else "WARNING"
                on_line(level, text)

        try:
            await asyncio.gather(
                _read_stdout(self._proc.stdout),
                _read_stderr(self._proc.stderr),
            )
            return await self._proc.wait()
        finally:
            self._proc = None
            self._remote_settings = None
            self._remote_pid = None

    async def stop(self) -> None:
        """Мягко останавливает процесс (локальный или удалённый по SSH)."""
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return

        self._stopping = True
        remote_settings = self._remote_settings
        remote_pid = self._remote_pid

        if remote_settings is not None and remote_pid is not None:
            for sig_name, wait_sec in (("INT", 3.0), ("TERM", 2.0), ("KILL", 1.5)):
                if proc.returncode is not None:
                    return
                with contextlib.suppress(Exception):
                    kill_script = (
                        f"pkill -{sig_name} -P {remote_pid} 2>/dev/null || true; "
                        f"kill -{sig_name} -{remote_pid} 2>/dev/null || "
                        f"kill -{sig_name} {remote_pid} 2>/dev/null || true"
                    )
                    await run_ssh_command(remote_settings, kill_script, timeout=5.0)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(proc.wait(), timeout=wait_sec)
                    return

        pid = proc.pid
        for sig, wait_sec in (
            (signal.SIGINT, 4.0),
            (signal.SIGTERM, 2.5),
            (signal.SIGKILL, 1.0),
        ):
            if proc.returncode is not None:
                return
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(os.getpgid(pid), sig)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=wait_sec)
                return
