"""Экран настройки караоке-субтитров ASS с пиксельным предпросмотром и синхронизацией шрифтов."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import flet as ft

from video_gen_gui.fonts import discover_fonts, render_subtitle_preview_png
from video_gen_gui.remote import sync_remote_fonts
from video_gen_gui.state import AppState
from video_gen_gui.views.components import (
    ass_color_picker_row,
    number_slider_row,
    path_picker_row,
    section_card,
    show_toast,
)


class SubtitlesView:
    """Редактор секции subtitles и папки шрифтов assets.fonts_dir."""

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        file_picker: ft.FilePicker,
    ) -> None:
        self.page = page
        self.state = state
        self.file_picker = file_picker
        self._sample_text: str = "ЭТОТ МОМЕНТ РЕШАЕТ"
        self._font_map: dict[str, Path] = {}
        self._font_dropdown: ft.Dropdown | None = None
        self._remote_fonts_sync_key: tuple[str, str] | None = None
        self.preview_image = ft.Image(
            src=b"",
            fit=ft.BoxFit.CONTAIN,
            gapless_playback=True,
            expand=True,
        )

    def reset_remote_sync(self) -> None:
        self._remote_fonts_sync_key = None

    def _resolve_fonts_dir(self) -> Path:
        if self.state.settings.is_remote:
            return self.state.settings.remote_cache_dir() / "fonts"
        vg_dir = self.state.settings.resolved_video_gen_dir()
        f_dir = Path(self.state.config.assets.fonts_dir or "fonts/")
        return f_dir if f_dir.is_absolute() else (vg_dir / f_dir)

    def _register_page_fonts(self, families: list[str]) -> None:
        page_fonts: dict[str, str] = {}
        for fam in families:
            font_path = self._font_map.get(fam.strip().lower())
            if font_path is not None and font_path.is_file():
                page_fonts[fam] = str(font_path)
        existing = getattr(self.page, "fonts", None)
        if isinstance(existing, dict):
            existing.update(page_fonts)
        else:
            self.page.fonts = page_fonts

    def _reload_fonts(
        self,
        *,
        update_dropdown: bool = True,
        refresh_preview: bool = True,
        update_page: bool = False,
    ) -> list[str]:
        families, font_map = discover_fonts(
            self._resolve_fonts_dir(),
            self.state.config.subtitles.font,
        )
        self._font_map = font_map
        self._register_page_fonts(families)
        if update_dropdown and self._font_dropdown is not None:
            self._font_dropdown.options = [ft.DropdownOption(key=f, text=f) for f in families]
        if refresh_preview:
            self._refresh_preview(update_page=False)
        if update_page:
            self.page.update()
        return families

    def _refresh_preview(self, *, update_page: bool = False) -> None:
        s = self.state.config.subtitles
        r = self.state.config.render
        png_bytes = render_subtitle_preview_png(
            s,
            self._font_map,
            sample_text=self._sample_text,
            top_parts=r.top_parts,
            bottom_parts=r.bottom_parts,
        )
        self.preview_image.src = png_bytes
        if update_page:
            self.page.update()

    def build(self) -> ft.Control:
        s = self.state.config.subtitles
        a = self.state.config.assets

        families = self._reload_fonts(
            update_dropdown=False,
            refresh_preview=True,
            update_page=False,
        )

        sync_key = (self.state.settings.ssh_destination(), a.fonts_dir)

        async def _auto_sync_coro() -> None:
            try:
                await sync_remote_fonts(self.state.settings, a.fonts_dir)
                self._remote_fonts_sync_key = sync_key
                self._reload_fonts(update_dropdown=True, refresh_preview=True, update_page=True)
            except Exception:
                pass

        if (
            self.state.settings.is_remote
            and self.state.settings.ssh_destination()
            and self._remote_fonts_sync_key != sync_key
            and type(self.page) is ft.Page
        ):
            self.page.run_task(_auto_sync_coro)

        def _mut_and_preview(attr: str, val: object) -> None:
            setattr(s, attr, val)
            self._refresh_preview(update_page=True)

        def _on_sample_text_change(e: ft.Event[ft.TextField]) -> None:
            self._sample_text = e.control.value or ""
            self._refresh_preview(update_page=True)

        def _on_fonts_dir_changed(v: str) -> None:
            a.fonts_dir = (v or "fonts/").strip() or "fonts/"
            if not self.state.settings.is_remote:
                self._reload_fonts(
                    update_dropdown=True,
                    refresh_preview=True,
                    update_page=True,
                )

        async def _sync_or_rescan_fonts(_: ft.Event[ft.OutlinedButton]) -> None:
            if self.state.settings.is_remote:
                try:
                    local_dir = await sync_remote_fonts(self.state.settings, a.fonts_dir)
                    found = self._reload_fonts(
                        update_dropdown=True,
                        refresh_preview=True,
                        update_page=True,
                    )
                    show_toast(
                        self.page,
                        f"Шрифты синхронизированы с SSH ({local_dir}): {len(found)} сем.",
                    )
                except Exception as exc:
                    show_toast(self.page, f"Ошибка синхронизации шрифтов: {exc}", error=True)
            else:
                found = self._reload_fonts(
                    update_dropdown=True,
                    refresh_preview=True,
                    update_page=True,
                )
                show_toast(self.page, f"Найдено шрифтов: {len(found)}")

        font_dropdown = ft.Dropdown(
            label="Семейство шрифта",
            editable=True,
            enable_filter=True,
            dense=True,
            width=260,
            value=s.font,
            on_select=lambda e: _mut_and_preview("font", (e.control.value or "Unbounded").strip()),
            on_text_change=lambda e: _mut_and_preview(
                "font", (e.control.text or "Unbounded").strip()
            ),
            options=[ft.DropdownOption(key=f, text=f) for f in families],
        )
        self._font_dropdown = font_dropdown

        sync_btn = ft.OutlinedButton(
            content="Обновить шрифты с SSH"
            if self.state.settings.is_remote
            else "Пересканировать шрифты",
            icon=ft.Icons.CLOUD_SYNC if self.state.settings.is_remote else ft.Icons.REFRESH,
            on_click=_sync_or_rescan_fonts,
        )

        return ft.Column(
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "Живое превью караоке-стиля",
                    ft.Icons.SUBTITLES_OUTLINED,
                    subtitle="Пиксельный рендер шрифта (.ttf/.otf), обводки, тени и активного слова",
                    trailing=ft.Switch(
                        label="Включить субтитры",
                        value=s.enabled,
                        on_change=lambda e: _mut_and_preview("enabled", bool(e.control.value)),
                    ),
                    controls=[
                        ft.Container(
                            height=172,
                            bgcolor="#111318",
                            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                            border_radius=ft.BorderRadius.all(4),
                            padding=ft.Padding.all(4),
                            alignment=ft.Alignment.CENTER,
                            content=self.preview_image,
                        ),
                        ft.TextField(
                            label="Текст для проверки шрифта",
                            value=self._sample_text,
                            dense=True,
                            on_change=_on_sample_text_change,
                        ),
                    ],
                ),
                section_card(
                    "Шрифт и Цвета ASS (&HAABBGGRR)",
                    ft.Icons.PALETTE_OUTLINED,
                    trailing=sync_btn,
                    controls=[
                        path_picker_row(
                            label="Папка со шрифтами .ttf/.otf (assets.fonts_dir)",
                            value=a.fonts_dir,
                            file_picker=self.file_picker,
                            pick_mode="dir",
                            base_dir_getter=self.state.settings.resolved_video_gen_dir,
                            page=self.page,
                            settings_getter=lambda: self.state.settings,
                            on_changed=_on_fonts_dir_changed,
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            controls=[
                                font_dropdown,
                                ft.Dropdown(
                                    label="Анимация караоке",
                                    dense=True,
                                    width=220,
                                    value=s.animation,
                                    on_select=lambda e: _mut_and_preview(
                                        "animation",
                                        cast(
                                            Literal["pop", "bounce", "none"],
                                            e.control.value or "pop",
                                        ),
                                    ),
                                    options=[
                                        ft.DropdownOption(key="pop", text="pop (увеличение слова)"),
                                        ft.DropdownOption(key="bounce", text="bounce (прыжок)"),
                                        ft.DropdownOption(key="none", text="none (без анимации)"),
                                    ],
                                ),
                                ft.Dropdown(
                                    label="Позиция на экране",
                                    dense=True,
                                    width=220,
                                    value=s.position,
                                    on_select=lambda e: _mut_and_preview(
                                        "position",
                                        cast(
                                            Literal["bottom", "top", "middle"],
                                            e.control.value or "bottom",
                                        ),
                                    ),
                                    options=[
                                        ft.DropdownOption(key="bottom", text="Снизу (bottom)"),
                                        ft.DropdownOption(
                                            key="middle", text="По стыку/центру (middle)"
                                        ),
                                        ft.DropdownOption(key="top", text="Сверху (top)"),
                                    ],
                                ),
                            ],
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=24,
                            controls=[
                                ft.Switch(
                                    label="ВЕРХНИЙ РЕГИСТР (CAPS)",
                                    value=s.uppercase,
                                    on_change=lambda e: _mut_and_preview(
                                        "uppercase", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Жирный (Bold)",
                                    value=s.bold,
                                    on_change=lambda e: _mut_and_preview(
                                        "bold", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Курсив (Italic)",
                                    value=s.italic,
                                    on_change=lambda e: _mut_and_preview(
                                        "italic", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Pop-увеличение активного слова",
                                    value=s.pop_animation,
                                    on_change=lambda e: _mut_and_preview(
                                        "pop_animation", bool(e.control.value)
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=ass_color_picker_row(
                                        page=self.page,
                                        label="Активное произносимое слово (spoken_color)",
                                        ass_value=s.spoken_color,
                                        on_changed=lambda v: _mut_and_preview("spoken_color", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=ass_color_picker_row(
                                        page=self.page,
                                        label="Остальные слова строки (unspoken_color)",
                                        ass_value=s.unspoken_color,
                                        on_changed=lambda v: _mut_and_preview("unspoken_color", v),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=ass_color_picker_row(
                                        page=self.page,
                                        label="Цвет обводки (outline_color)",
                                        ass_value=s.outline_color,
                                        on_changed=lambda v: _mut_and_preview("outline_color", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=ass_color_picker_row(
                                        page=self.page,
                                        label="Базовая заливка (fill_color)",
                                        ass_value=s.fill_color,
                                        on_changed=lambda v: _mut_and_preview("fill_color", v),
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
                section_card(
                    "Размеры, Обводка и Правила разбивки строк",
                    ft.Icons.FORMAT_SIZE,
                    controls=[
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Размер шрифта (font_size)",
                                        value=s.font_size,
                                        min_val=28,
                                        max_val=150,
                                        divisions=122,
                                        is_int=True,
                                        suffix=" px",
                                        on_changed=lambda v: _mut_and_preview("font_size", int(v)),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Масштаб Pop-анимации",
                                        value=s.pop_scale,
                                        min_val=1.0,
                                        max_val=1.6,
                                        divisions=12,
                                        suffix="x",
                                        on_changed=lambda v: _mut_and_preview("pop_scale", v),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Толщина обводки (outline)",
                                        value=s.outline,
                                        min_val=0,
                                        max_val=16,
                                        divisions=16,
                                        is_int=True,
                                        on_changed=lambda v: _mut_and_preview("outline", int(v)),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Глубина тени (shadow)",
                                        value=s.shadow,
                                        min_val=0,
                                        max_val=16,
                                        divisions=16,
                                        is_int=True,
                                        on_changed=lambda v: _mut_and_preview("shadow", int(v)),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Интервал между словами (word_spacing)",
                                        value=s.word_spacing,
                                        min_val=-20,
                                        max_val=50,
                                        divisions=70,
                                        is_int=True,
                                        suffix=" px",
                                        on_changed=lambda v: _mut_and_preview(
                                            "word_spacing", int(v)
                                        ),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Максимум слов в строке (max_words)",
                                        value=s.max_words,
                                        min_val=1,
                                        max_val=8,
                                        divisions=7,
                                        is_int=True,
                                        on_changed=lambda v: setattr(s, "max_words", int(v)),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Максимум символов в строке (max_chars)",
                                        value=s.max_chars,
                                        min_val=10,
                                        max_val=50,
                                        divisions=40,
                                        is_int=True,
                                        on_changed=lambda v: setattr(s, "max_chars", int(v)),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            controls=[
                                ft.TextField(
                                    label="ASS Alignment (1-9)",
                                    value=str(s.alignment),
                                    dense=True,
                                    width=150,
                                    helper="2=низ, 5=центр, 8=верх",
                                    on_change=lambda e: setattr(
                                        s,
                                        "alignment",
                                        max(1, min(9, int(e.control.value or 2)))
                                        if (e.control.value or "").isdigit()
                                        else 2,
                                    ),
                                ),
                                ft.TextField(
                                    label="Отступ по вертикали (margin_v)",
                                    value=str(s.margin_v),
                                    dense=True,
                                    width=220,
                                    on_change=lambda e: setattr(
                                        s,
                                        "margin_v",
                                        max(0, int(e.control.value or 0))
                                        if (e.control.value or "").isdigit()
                                        else 0,
                                    ),
                                ),
                                ft.TextField(
                                    label="Смещение от стыка (seam_offset)",
                                    value=str(s.seam_offset),
                                    dense=True,
                                    width=220,
                                    on_change=lambda e: _mut_and_preview(
                                        "seam_offset",
                                        int(e.control.value)
                                        if (e.control.value or "").lstrip("-").isdigit()
                                        else 0,
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
