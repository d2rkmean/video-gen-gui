"""Тесты расписания баннера, строгой совместимости с AppConfig и аудит наследия БД."""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from video_gen_gui.models import RenderSection, VideoGenConfig

VIDEO_GEN_GUI_ROOT = Path(__file__).resolve().parents[1]
VIDEO_GEN_ROOT = VIDEO_GEN_GUI_ROOT.parent / "video-gen"
VIDEO_GEN_SRC = VIDEO_GEN_ROOT / "src"

EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "cache",
    "output",
}
BINARY_EXTS = {
    ".ttf",
    ".otf",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".avi",
    ".mp3",
    ".wav",
    ".pyc",
    ".pyo",
    ".so",
    ".whl",
    ".lock",
}


def test_render_section_banner_schedule_validation_and_cli_dict() -> None:
    r_min = RenderSection(
        banner_mode="interval_minutes",
        banner_interval_minutes=2.5,
        banner_freeze=False,
    )
    cfg_min = VideoGenConfig(render=r_min)
    cli_min = cfg_min.to_cli_dict()
    assert cli_min["render"]["banner_mode"] == "interval_minutes"
    assert cli_min["render"]["banner_interval_minutes"] == 2.5
    assert cli_min["render"]["banner_freeze"] is False

    r_pct = RenderSection(
        banner_mode="interval_percent",
        banner_interval_percent=33.5,
        banner_freeze=True,
    )
    cfg_pct = VideoGenConfig(render=r_pct)
    cli_pct = cfg_pct.to_cli_dict()
    assert cli_pct["render"]["banner_mode"] == "interval_percent"
    assert cli_pct["render"]["banner_interval_percent"] == 33.5
    assert cli_pct["render"]["banner_freeze"] is True

    with pytest.raises(ValidationError):
        RenderSection(banner_interval_minutes=0.0)
    with pytest.raises(ValidationError):
        RenderSection(banner_interval_minutes=181.0)
    with pytest.raises(ValidationError):
        RenderSection(banner_interval_percent=0.0)
    with pytest.raises(ValidationError):
        RenderSection(banner_interval_percent=101.0)
    with pytest.raises(ValidationError):
        RenderSection.model_validate({"banner_mode": "every_second"})


def test_gui_config_validates_against_strict_cli_app_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    src_str = str(VIDEO_GEN_SRC)
    added_to_path = False
    if src_str not in sys.path:
        sys.path.insert(0, src_str)
        added_to_path = True

    try:
        vg_config_mod = importlib.import_module("video_gen.config")
    finally:
        if added_to_path and src_str in sys.path:
            sys.path.remove(src_str)

    app_config_cls = vg_config_mod.AppConfig
    render_config_cls = vg_config_mod.RenderConfig
    assert app_config_cls.model_config.get("extra") == "forbid"
    assert render_config_cls.model_config.get("extra") == "forbid"

    # 1. Конфиг по умолчанию
    default_gui = VideoGenConfig()
    parsed_default = app_config_cls.model_validate_json(default_gui.to_json_text())
    assert parsed_default.render.banner_mode == "center"
    assert parsed_default.render.banner_freeze is True

    # 2. Режим interval_minutes
    gui_min = VideoGenConfig(
        render=RenderSection(
            banner_mode="interval_minutes",
            banner_interval_minutes=1.5,
            banner_freeze=False,
        )
    )
    parsed_min = app_config_cls.model_validate_json(gui_min.to_json_text())
    assert parsed_min.render.banner_mode == "interval_minutes"
    assert parsed_min.render.banner_interval_minutes == 1.5
    assert parsed_min.render.banner_freeze is False

    # 3. Режим interval_percent
    gui_pct = VideoGenConfig(
        render=RenderSection(
            banner_mode="interval_percent",
            banner_interval_percent=25.0,
            banner_freeze=True,
        )
    )
    parsed_pct = app_config_cls.model_validate_json(gui_pct.to_json_text())
    assert parsed_pct.render.banner_mode == "interval_percent"
    assert parsed_pct.render.banner_interval_percent == 25.0
    assert parsed_pct.render.banner_freeze is True

    # Проверка, что лишнее поле действительно отклоняется strict-схемой CLI
    with pytest.raises(ValidationError):
        app_config_cls.model_validate({"unknown_extra_key": 1})


def test_no_legacy_brand_references_in_both_repos() -> None:
    needle = "cut" + "box"
    pattern = re.compile(needle, re.IGNORECASE)
    self_path = Path(__file__).resolve()

    hits: list[str] = []
    for root in (VIDEO_GEN_ROOT, VIDEO_GEN_GUI_ROOT):
        assert root.is_dir(), f"Не найдена папка проекта: {root}"
        for path in root.rglob("*"):
            if any(part in EXCLUDED_DIRS for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.resolve() == self_path:
                continue
            if path.suffix.lower() in BINARY_EXTS:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for lineno, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    hits.append(f"{path}:{lineno}: {line.strip()}")

    assert hits == [], f"Обнаружены запрещённые упоминания ({needle}):\n" + "\n".join(hits)
