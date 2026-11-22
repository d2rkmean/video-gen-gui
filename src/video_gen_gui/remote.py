"""Асинхронные OpenSSH-утилиты (ssh / scp) для удалённого управления сервером video-gen."""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import posixpath
import shlex
import shutil
import tarfile
from pathlib import Path
from typing import Any

from video_gen_gui.models import GuiSettings, VideoGenConfig


def _ssh_key_args(settings: GuiSettings) -> list[str]:
    key_raw = settings.ssh_key_path.strip()
    if not key_raw:
        return []
    expanded = str(Path(key_raw).expanduser())
    return ["-i", expanded]


def build_ssh_cmd(settings: GuiSettings) -> list[str]:
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        raise ValueError("Утилита 'ssh' не найдена в PATH (установите OpenSSH client)")

    dest = settings.ssh_destination()
    if not dest:
        raise ValueError("Не указан SSH-хост (ssh_host) в настройках подключения")
    if dest.startswith("-") or any(ch.isspace() for ch in dest):
        raise ValueError(f"Некорректный SSH-адрес: {dest!r}")

    return [
        ssh_bin,
        "-p",
        str(settings.ssh_port),
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=8",
        "-o",
        "StrictHostKeyChecking=accept-new",
        *_ssh_key_args(settings),
        "--",
        dest,
    ]


def sh_remote_path(path_str: str) -> str:
    """Безопасно экранирует POSIX-путь для удалённого `sh -c`, раскрывая ведущий `~/`."""
    s = path_str.strip()
    if not s:
        return shlex.quote(".")
    if s == "~":
        return '"$HOME"'
    if s.startswith("~/"):
        rest = s[2:]
        if not rest:
            return '"$HOME"'
        return f'"$HOME"/{shlex.quote(rest)}'
    return shlex.quote(s)


def relativize_remote_path(picked_path: str, remote_vg_dir_abs: str) -> str:
    """Если удалённый путь находится внутри `remote_vg_dir_abs`, возвращает относительный путь."""
    clean_picked = posixpath.normpath(picked_path.strip())
    clean_base = posixpath.normpath(remote_vg_dir_abs.strip())
    if not clean_base or not clean_picked.startswith("/"):
        return picked_path
    try:
        rel = posixpath.relpath(clean_picked, clean_base)
    except ValueError:
        return picked_path
    if rel == ".":
        return "."
    if not rel.startswith(".."):
        return rel
    return picked_path


async def run_ssh_command(
    settings: GuiSettings,
    script: str,
    *,
    stdin_data: bytes | None = None,
    timeout: float = 15.0,
) -> tuple[int, str, str]:
    """Выполняет POSIX shell-скрипт на удалённом хосте через `ssh ... sh -c ...`."""
    cmd = [*build_ssh_cmd(settings), "sh", "-c", shlex.quote(script)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE if stdin_data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=stdin_data),
            timeout=timeout,
        )
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise TimeoutError(f"Таймаут SSH-команды ({timeout:.1f} с)") from None

    return (
        proc.returncode or 0,
        stdout_b.decode("utf-8", errors="replace"),
        stderr_b.decode("utf-8", errors="replace"),
    )


async def test_ssh_connection(settings: GuiSettings) -> tuple[bool, str]:
    """Проверяет SSH-подключение и возвращает `(ok, сообщение)`."""
    script = (
        'h=$(hostname 2>/dev/null || uname -n); '
        'gpu=""; '
        'if command -v nvidia-smi >/dev/null 2>&1; then '
        '  gpu=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1 | tr -d "\\r"); '
        'fi; '
        'if [ -n "$gpu" ]; then info="$gpu"; else info=$(uname -sr); fi; '
        'printf "%s\\n%s\\n" "$h" "$info"'
    )
    try:
        rc, out, err = await run_ssh_command(settings, script, timeout=12.0)
    except (ValueError, TimeoutError, OSError) as exc:
        return False, str(exc)

    if rc != 0:
        detail = err.strip() or out.strip() or f"код выхода {rc}"
        return False, f"Ошибка SSH: {detail}"

    lines = [line.strip() for line in out.splitlines() if line.strip()]
    hostname = lines[0] if lines else settings.ssh_destination()
    extra = lines[1] if len(lines) > 1 else "Linux"
    return True, f"Подключено: {hostname} ({extra})"


async def read_remote_text(settings: GuiSettings, remote_path: str) -> str:
    """Читает текстовый UTF-8 файл с удалённого сервера через SSH."""
    quoted = sh_remote_path(remote_path)
    script = f'p={quoted}; if [ ! -f "$p" ]; then exit 44; fi; cat "$p"'
    rc, out, err = await run_ssh_command(settings, script, timeout=15.0)
    if rc == 44:
        raise FileNotFoundError(f"Удалённый файл не найден: {remote_path}")
    if rc != 0:
        raise RuntimeError(f"Не удалось прочитать {remote_path}: {err.strip() or f'код {rc}'}")
    return out


async def write_remote_text(settings: GuiSettings, remote_path: str, text: str) -> None:
    """Атомарно записывает UTF-8 текст в удалённый файл через stdin SSH."""
    quoted = sh_remote_path(remote_path)
    script = (
        f'p={quoted}; '
        'd=$(dirname "$p"); '
        'mkdir -p "$d" && '
        'tmp=$(mktemp "$d/.vg_tmp.XXXXXX") && '
        '{ cat > "$tmp" && mv -f "$tmp" "$p"; } || { rm -f "$tmp"; exit 1; }'
    )
    rc, _out, err = await run_ssh_command(
        settings,
        script,
        stdin_data=text.encode("utf-8"),
        timeout=20.0,
    )
    if rc != 0:
        raise RuntimeError(f"Не удалось записать {remote_path}: {err.strip() or f'код {rc}'}")


async def check_remote_preflight(settings: GuiSettings, config: VideoGenConfig) -> list[str]:
    """Проверяет наличие файлов/папок конфига на удалённой машине одним SSH-запросом."""
    payload = {
        "vg_dir": settings.resolved_remote_video_gen_dir(),
        "top_videos": [
            {"idx": idx, "type": src.type, "source": src.source}
            for idx, src in enumerate(config.videos.top_videos, 1)
        ],
        "bottom_videos": [
            {"idx": idx, "type": src.type, "source": src.source}
            for idx, src in enumerate(config.videos.bottom_videos, 1)
        ],
        "banner": config.assets.banner,
        "banners_dir": config.assets.banners_dir,
        "music_dir": config.assets.music_dir,
    }
    py_code = "\n".join(
        [
            "import json, os, sys",
            "from pathlib import Path",
            "d = json.load(sys.stdin)",
            "vg = Path(os.path.expanduser(d['vg_dir'])).resolve()",
            "def res(s):",
            "    p = Path(os.path.expanduser(s))",
            "    return p if p.is_absolute() else vg / p",
            "w = []",
            "for x in d['top_videos']:",
            "    if x['type'] == 'video' and not res(x['source']).exists():",
            "        w.append(f\"Top-источник #{x['idx']} не найден: {res(x['source'])}\")",
            "for x in d['bottom_videos']:",
            "    if x['type'] == 'video' and not res(x['source']).exists():",
            "        w.append(f\"Bottom-источник #{x['idx']} не найден: {res(x['source'])}\")",
            "if d['banner'] and not res(d['banner']).is_file():",
            "    w.append(f\"Файл баннера не найден (CLI отклонит конфиг): {res(d['banner'])}\")",
            "if d['banners_dir'] and not res(d['banners_dir']).is_dir():",
            "    w.append(f\"Папка баннеров не найдена (CLI отклонит конфиг): {res(d['banners_dir'])}\")",
            "if d['music_dir'] and not res(d['music_dir']).is_dir():",
            "    w.append(f\"Папка музыки не найдена (CLI отклонит конфиг): {res(d['music_dir'])}\")",
            "print(json.dumps(w, ensure_ascii=False))",
        ]
    )
    script = f"python3 -c {shlex.quote(py_code)}"
    rc, out, err = await run_ssh_command(
        settings,
        script,
        stdin_data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=15.0,
    )
    if rc != 0:
        raise RuntimeError(
            f"Ошибка preflight-проверки на сервере: {err.strip() or f'код выхода {rc}'}"
        )
    data = json.loads(out.strip() or "[]")
    return [str(item) for item in data]


async def list_remote_dir(
    settings: GuiSettings,
    remote_dir: str,
    *,
    only_dirs: bool = False,
    allowed_exts: list[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Возвращает `(resolved_abs_path, entries)` для удалённой директории."""
    payload = {
        "remote_dir": remote_dir or "~",
        "only_dirs": only_dirs,
        "allowed_exts": [e.lower().lstrip(".") for e in allowed_exts] if allowed_exts else None,
    }
    py_code = "\n".join(
        [
            "import json, os, sys",
            "from pathlib import Path",
            "d = json.load(sys.stdin)",
            "p = Path(os.path.expanduser(d['remote_dir'])).resolve()",
            "if not p.exists() or not p.is_dir():",
            "    sys.exit(44)",
            "exts = set(d['allowed_exts']) if d['allowed_exts'] else None",
            "only_dirs = bool(d['only_dirs'])",
            "entries = []",
            "for item in p.iterdir():",
            "    if item.name.startswith('.'):",
            "        continue",
            "    try:",
            "        is_dir = item.is_dir()",
            "        if only_dirs and not is_dir:",
            "            continue",
            "        if not is_dir and exts is not None and item.suffix.lower().lstrip('.') not in exts:",
            "            continue",
            "        size = 0 if is_dir else item.stat().st_size",
            "        entries.append({'name': item.name, 'path': str(item), 'is_dir': is_dir, 'size': size})",
            "    except OSError:",
            "        continue",
            "entries.sort(key=lambda x: (not x['is_dir'], x['name'].lower()))",
            "print(json.dumps({'path': str(p), 'entries': entries}, ensure_ascii=False))",
        ]
    )
    script = f"python3 -c {shlex.quote(py_code)}"
    rc, out, err = await run_ssh_command(
        settings,
        script,
        stdin_data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=15.0,
    )
    if rc == 44:
        raise FileNotFoundError(f"Удалённая папка не найдена: {remote_dir}")
    if rc != 0:
        raise RuntimeError(
            f"Не удалось прочитать папку {remote_dir}: {err.strip() or f'код выхода {rc}'}"
        )
    parsed = json.loads(out)
    return str(parsed["path"]), list(parsed["entries"])


async def sync_remote_fonts(settings: GuiSettings, fonts_dir: str) -> Path:
    """Копирует `.ttf`/`.otf` файлы из удалённой `fonts_dir` через `tar -cf -` в локальный кэш."""
    local_fonts_dir = settings.remote_cache_dir() / "fonts"
    local_fonts_dir.mkdir(parents=True, exist_ok=True)

    vg_q = sh_remote_path(settings.resolved_remote_video_gen_dir())
    fd_q = sh_remote_path(fonts_dir.strip() or "fonts/")
    script = (
        f"vg={vg_q}; fd={fd_q}; "
        'case "$fd" in /*) target="$fd" ;; *) target="$vg/$fd" ;; esac; '
        'if [ ! -d "$target" ]; then exit 44; fi; '
        'cd "$target" || exit 1; '
        'set --; '
        'for f in *; do '
        '  [ -f "$f" ] || continue; '
        '  case "$f" in *.ttf|*.TTF|*.otf|*.OTF) set -- "$@" "$f" ;; esac; '
        'done; '
        'if [ "$#" -eq 0 ]; then exit 0; fi; '
        'tar -cf - "$@"'
    )

    cmd = [*build_ssh_cmd(settings), "sh", "-c", shlex.quote(script)]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=30.0)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        raise TimeoutError("Таймаут синхронизации шрифтов по SSH") from None

    rc = proc.returncode or 0
    if rc == 44:
        return local_fonts_dir
    if rc != 0:
        err_txt = stderr_b.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Ошибка синхронизации шрифтов: {err_txt or f'код {rc}'}")

    # Очищаем устаревшие локальные кэшированные шрифты перед распаковкой
    for old_file in local_fonts_dir.iterdir():
        if old_file.is_file() and old_file.suffix.lower() in (".ttf", ".otf"):
            with contextlib.suppress(OSError):
                old_file.unlink()

    if not stdout_b:
        return local_fonts_dir

    with tarfile.open(fileobj=io.BytesIO(stdout_b), mode="r:") as tf:
        for member in tf.getmembers():
            if not member.isfile():
                continue
            safe_name = Path(member.name).name
            if not safe_name or safe_name in (".", ".."):
                continue
            if Path(safe_name).suffix.lower() not in (".ttf", ".otf"):
                continue
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            data = extracted.read()
            if len(data) < 100:
                continue
            (local_fonts_dir / safe_name).write_bytes(data)

    return local_fonts_dir


async def clear_remote_cache(settings: GuiSettings) -> tuple[bool, str]:
    vg_q = sh_remote_path(settings.resolved_remote_video_gen_dir())
    script = (
        f"vg={vg_q}; "
        'if [ ! -d "$vg" ] || [ ! -f "$vg/pyproject.toml" ]; then '
        '  echo "Папка video-gen не найдена: $vg" >&2; exit 2; '
        "fi; "
        'c="$vg/cache"; rm -rf "$c" && mkdir -p "$c"'
    )
    try:
        rc, _out, err = await run_ssh_command(settings, script, timeout=20.0)
    except (ValueError, TimeoutError, OSError) as exc:
        return False, str(exc)

    if rc != 0:
        return False, f"Не удалось очистить кэш: {err.strip() or f'код выхода {rc}'}"
    return True, "Удалённый кэш (cache/) очищен"


async def list_remote_clips(
    settings: GuiSettings,
    output_dir: str,
    limit: int = 100,
) -> tuple[str, list[dict[str, Any]]]:
    """Сканирует удалённую папку `output_dir` на наличие `.mp4` и метаданных `.json`."""
    payload = {
        "vg_dir": settings.resolved_remote_video_gen_dir(),
        "output_dir": output_dir or "output/",
        "limit": max(1, int(limit)),
    }
    py_code = "\n".join(
        [
            "import json, os, sys",
            "from pathlib import Path",
            "d = json.load(sys.stdin)",
            "vg = Path(os.path.expanduser(d['vg_dir'])).resolve()",
            "raw_out = Path(os.path.expanduser(d['output_dir']))",
            "out_dir = raw_out if raw_out.is_absolute() else (vg / raw_out)",
            "if not out_dir.is_dir():",
            "    print(json.dumps({'out_dir': str(out_dir), 'clips': []}, ensure_ascii=False))",
            "    sys.exit(0)",
            "items = []",
            "for mp4 in out_dir.rglob('*.mp4'):",
            "    try:",
            "        st = mp4.stat()",
            "        items.append((st.st_mtime, st.st_size, mp4))",
            "    except OSError:",
            "        continue",
            "items.sort(key=lambda x: x[0], reverse=True)",
            "clips = []",
            "for mtime, size, mp4 in items[:d['limit']]:",
            "    meta = {}",
            "    mf = mp4.with_suffix('.json')",
            "    if mf.is_file():",
            "        try:",
            "            parsed = json.loads(mf.read_text(encoding='utf-8'))",
            "            if isinstance(parsed, dict):",
            "                meta = parsed",
            "        except Exception:",
            "            pass",
            "    try:",
            "        rel = str(mp4.relative_to(out_dir))",
            "    except ValueError:",
            "        rel = mp4.name",
            "    clips.append({'name': mp4.name, 'rel_path': rel, 'abs_path': str(mp4), 'size_mb': size / (1024 * 1024), 'mtime': mtime, 'meta': meta})",
            "print(json.dumps({'out_dir': str(out_dir), 'clips': clips}, ensure_ascii=False))",
        ]
    )
    script = f"python3 -c {shlex.quote(py_code)}"
    rc, out, err = await run_ssh_command(
        settings,
        script,
        stdin_data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=20.0,
    )
    if rc != 0:
        raise RuntimeError(
            f"Не удалось получить список клипов: {err.strip() or f'код выхода {rc}'}"
        )
    parsed = json.loads(out)
    return str(parsed["out_dir"]), list(parsed["clips"])


async def download_remote_file(
    settings: GuiSettings,
    remote_abs_path: str,
    local_dest: Path,
) -> Path:
    """Скачивает удалённый файл потоково через SSH (поддерживает пробелы и кириллицу в путях)."""
    local_dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = local_dest.with_name(f".{local_dest.name}.part")
    quoted = sh_remote_path(remote_abs_path)
    script = f'p={quoted}; if [ ! -f "$p" ]; then exit 44; fi; cat "$p"'
    cmd = [*build_ssh_cmd(settings), "sh", "-c", shlex.quote(script)]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    async def _read_stderr() -> bytes:
        if proc.stderr is None:
            return b""
        return await proc.stderr.read()

    stderr_task = asyncio.create_task(_read_stderr())
    try:
        assert proc.stdout is not None
        with tmp_dest.open("wb") as fh:
            while True:
                chunk = await asyncio.wait_for(proc.stdout.read(256 * 1024), timeout=60.0)
                if not chunk:
                    break
                fh.write(chunk)
        stderr_b = await asyncio.wait_for(stderr_task, timeout=15.0)
        await asyncio.wait_for(proc.wait(), timeout=15.0)
    except Exception as exc:
        stderr_task.cancel()
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        tmp_dest.unlink(missing_ok=True)
        if isinstance(exc, TimeoutError):
            raise TimeoutError(f"Таймаут скачивания файла: {remote_abs_path}") from None
        raise

    rc = proc.returncode or 0
    if rc == 44:
        tmp_dest.unlink(missing_ok=True)
        raise FileNotFoundError(f"Удалённый файл не найден: {remote_abs_path}")
    if rc != 0:
        tmp_dest.unlink(missing_ok=True)
        err_txt = stderr_b.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Не удалось скачать {remote_abs_path}: {err_txt or f'код {rc}'}")

    tmp_dest.replace(local_dest)
    return local_dest


async def check_remote_tcp_endpoint(
    settings: GuiSettings,
    host: str,
    port: int,
    timeout: float = 4.0,
) -> tuple[bool, str]:
    """Проверяет доступность TCP-порта *с удалённого сервера* через SSH."""
    payload = {"host": host, "port": int(port), "timeout": float(timeout)}
    py_code = "\n".join(
        [
            "import json, socket, sys",
            "d = json.load(sys.stdin)",
            "try:",
            "    with socket.create_connection((d['host'], d['port']), timeout=d['timeout']):",
            "        print(json.dumps({'ok': True, 'msg': f\"Соединение с {d['host']}:{d['port']} с сервера успешно установлено\"}, ensure_ascii=False))",
            "except TimeoutError:",
            "    print(json.dumps({'ok': False, 'msg': f\"Таймаут подключения с сервера к {d['host']}:{d['port']} ({d['timeout']:.1f} с)\"}, ensure_ascii=False))",
            "except OSError as exc:",
            "    print(json.dumps({'ok': False, 'msg': f\"Ошибка подключения с сервера к {d['host']}:{d['port']}: {exc}\"}, ensure_ascii=False))",
        ]
    )
    script = f"python3 -c {shlex.quote(py_code)}"
    try:
        rc, out, err = await run_ssh_command(
            settings,
            script,
            stdin_data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=max(10.0, timeout + 6.0),
        )
    except (ValueError, TimeoutError, OSError) as exc:
        return False, str(exc)

    if rc != 0:
        return False, f"Ошибка проверки TCP через SSH: {err.strip() or f'код {rc}'}"

    parsed = json.loads(out)
    return bool(parsed["ok"]), str(parsed["msg"])
