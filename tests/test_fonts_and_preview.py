"""Тесты обнаружения шрифтов (.ttf/.otf), генерации PNG-превью и SubtitlesView."""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import flet as ft
import pytest
from PIL import Image

from video_gen_gui.fonts import discover_fonts, render_subtitle_preview_png, resolve_font_path
from video_gen_gui.models import SubtitlesSection
from video_gen_gui.state import AppState
from video_gen_gui.views.subtitles import SubtitlesView

VIDEO_GEN_FONTS_DIR = Path(__file__).resolve().parents[2] / "video-gen" / "fonts"


def test_discover_fonts_skips_corrupt_and_resolves_aliases(tmp_path: Path) -> None:
    # 1. Папка только со сломанным 14-байтным файлом bad.ttf
    bad_font = tmp_path / "bad.ttf"
    bad_font.write_bytes(b"404: Not Found")
    assert len(bad_font.read_bytes()) == 14

    empty_families, empty_map = discover_fonts(tmp_path)
    assert empty_families == []
    assert empty_map == {}

    # 2. Добавляем валидный шрифт рядом со сломанным — сломанный должен быть пропущен
    shutil.copy2(VIDEO_GEN_FONTS_DIR / "RussoOne.ttf", tmp_path / "RussoOne.ttf")
    mixed_families, mixed_map = discover_fonts(tmp_path)
    assert len(mixed_families) >= 1
    assert "bad" not in mixed_map
    assert resolve_font_path("RussoOne", mixed_map) == tmp_path / "RussoOne.ttf"

    # 3. Полная директория шрифтов проекта video-gen
    assert VIDEO_GEN_FONTS_DIR.is_dir()
    families, font_map = discover_fonts(VIDEO_GEN_FONTS_DIR)
    assert len(families) >= 5

    aliases = [
        "GoogleSans",
        "Google Sans",
        "RussoOne",
        "Russo One",
        "BebasNeue",
        "Bebas Neue",
        "Unbounded",
        "DelaGothicOne",
    ]
    for alias in aliases:
        resolved = resolve_font_path(alias, font_map)
        assert resolved is not None, f"Шрифт '{alias}' не найден"
        assert resolved.is_file()
        assert resolved.parent == VIDEO_GEN_FONTS_DIR
        assert resolved.suffix.lower() in {".ttf", ".otf"}


def test_render_subtitle_preview_png_dimensions_and_visual_diffs() -> None:
    _, font_map = discover_fonts(VIDEO_GEN_FONTS_DIR)

    base_sub = SubtitlesSection(font="Unbounded")
    png_unbounded = render_subtitle_preview_png(base_sub, font_map, width=860, height=168)
    assert png_unbounded[:8] == b"\x89PNG\r\n\x1a\n"

    with Image.open(io.BytesIO(png_unbounded)) as img:
        assert img.size == (860, 168)
        px_unbounded = img.tobytes()

    # Смена шрифта: Unbounded vs Russo One vs Bebas Neue
    png_russo = render_subtitle_preview_png(
        base_sub.model_copy(update={"font": "Russo One"}), font_map, width=860, height=168
    )
    png_bebas = render_subtitle_preview_png(
        base_sub.model_copy(update={"font": "Bebas Neue"}), font_map, width=860, height=168
    )
    with Image.open(io.BytesIO(png_russo)) as img_r:
        px_russo = img_r.tobytes()
    with Image.open(io.BytesIO(png_bebas)) as img_b:
        px_bebas = img_b.tobytes()

    assert png_unbounded != png_russo
    assert png_unbounded != png_bebas
    assert png_russo != png_bebas
    assert px_unbounded != px_russo
    assert px_unbounded != px_bebas
    assert px_russo != px_bebas

    # Смена spoken_color, uppercase, enabled=False и sample_text
    png_color = render_subtitle_preview_png(
        base_sub.model_copy(update={"spoken_color": "&H000000FF"}), font_map
    )
    png_upper_mixed = render_subtitle_preview_png(
        base_sub.model_copy(update={"uppercase": True}),
        font_map,
        sample_text="Этот момент решает",
    )
    png_lower_mixed = render_subtitle_preview_png(
        base_sub.model_copy(update={"uppercase": False}),
        font_map,
        sample_text="Этот момент решает",
    )
    assert png_upper_mixed != png_lower_mixed

    png_disabled = render_subtitle_preview_png(
        base_sub.model_copy(update={"enabled": False}), font_map
    )
    png_other_text = render_subtitle_preview_png(
        base_sub, font_map, sample_text="ДРУГОЙ ТЕКСТ ПРЕВЬЮ"
    )

    variants = [
        png_unbounded,
        png_color,
        png_lower_mixed,
        png_disabled,
        png_other_text,
    ]
    assert len(set(variants)) == len(variants)


def test_subtitles_view_initializes_and_refreshes_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    fake_page = MagicMock(spec=ft.Page)
    state = AppState()
    state.settings.execution_mode = "local"
    state.settings.video_gen_dir = str(VIDEO_GEN_FONTS_DIR.parent)
    state.config.assets.fonts_dir = "fonts/"
    state.config.subtitles.font = "Unbounded"

    view = SubtitlesView(fake_page, state, ft.FilePicker())
    ctrl = view.build()
    assert isinstance(ctrl, ft.Control)

    initial_src = view.preview_image.src
    assert isinstance(initial_src, bytes)
    assert initial_src[:8] == b"\x89PNG\r\n\x1a\n"

    state.config.subtitles.font = "Russo One"
    view._refresh_preview(update_page=True)
    updated_src = view.preview_image.src
    assert isinstance(updated_src, bytes)
    assert updated_src[:8] == b"\x89PNG\r\n\x1a\n"
    assert updated_src != initial_src
    fake_page.update.assert_called()
