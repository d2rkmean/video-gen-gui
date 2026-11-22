"""Экран управления видеоисточниками: top_videos (локальные / YouTube) и bottom_videos (видео / PyGame)."""

from __future__ import annotations

from typing import Literal

import flet as ft

from video_gen_gui.models import VideoSourceConfig, VideoSourceOverrideRender
from video_gen_gui.state import AppState
from video_gen_gui.views.components import (
    format_relative_if_inside,
    pick_remote_path_dialog,
    section_card,
)


def _type_badge(label: str) -> ft.Container:
    return ft.Container(
        bgcolor=ft.Colors.SURFACE_CONTAINER,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=ft.BorderRadius.all(3),
        padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        content=ft.Text(
            label,
            size=10,
            weight=ft.FontWeight.W_600,
            font_family="monospace",
            color=ft.Colors.ON_SURFACE_VARIANT,
        ),
    )


class SourcesView:
    """Редактор разделов videos.top_videos и videos.bottom_videos."""

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        file_picker: ft.FilePicker,
    ) -> None:
        self.page = page
        self.state = state
        self.file_picker = file_picker
        self.top_list_col = ft.Column(spacing=12)
        self.bottom_list_col = ft.Column(spacing=12)

    def _add_top_source(self, src_type: Literal["video", "youtube"]) -> None:
        default_src = "https://www.youtube.com/watch?v=" if src_type == "youtube" else "input/"
        self.state.config.videos.top_videos.append(
            VideoSourceConfig(type=src_type, source=default_src)
        )
        self._rebuild_lists()
        self.page.update()

    def _remove_top_source(self, idx: int) -> None:
        if len(self.state.config.videos.top_videos) <= 1:
            return
        self.state.config.videos.top_videos.pop(idx)
        self._rebuild_lists()
        self.page.update()

    def _add_bottom_source(self, src_type: Literal["video", "pygame"]) -> None:
        default_src = "runner" if src_type == "pygame" else "bottom/"
        self.state.config.videos.bottom_videos.append(
            VideoSourceConfig(type=src_type, source=default_src)
        )
        self._rebuild_lists()
        self.page.update()

    def _remove_bottom_source(self, idx: int) -> None:
        if len(self.state.config.videos.bottom_videos) <= 1:
            return
        self.state.config.videos.bottom_videos.pop(idx)
        self._rebuild_lists()
        self.page.update()

    def _build_top_item(self, idx: int, item: VideoSourceConfig, can_delete: bool) -> ft.Container:
        vg_dir = self.state.settings.resolved_video_gen_dir()

        src_tf = ft.TextField(
            label="Ссылка YouTube / плейлист"
            if item.type == "youtube"
            else "Путь к папке или видеофайлу",
            value=item.source,
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda e: setattr(item, "source", (e.control.value or "").strip()),
        )

        def _apply_picked(val: str) -> None:
            src_tf.value = val
            item.source = val
            self.page.update()

        async def _pick_dir(_: ft.Event[ft.IconButton]) -> None:
            if self.state.settings.is_remote:
                await pick_remote_path_dialog(
                    self.page,
                    self.state.settings,
                    title="Выбор удалённой папки с видео",
                    pick_mode="dir",
                    on_picked=_apply_picked,
                )
                return

            chosen = await self.file_picker.get_directory_path(initial_directory=str(vg_dir))
            if chosen:
                _apply_picked(format_relative_if_inside(chosen, vg_dir))

        async def _pick_file(_: ft.Event[ft.IconButton]) -> None:
            if self.state.settings.is_remote:
                await pick_remote_path_dialog(
                    self.page,
                    self.state.settings,
                    title="Выбор удалённого видеофайла",
                    pick_mode="file",
                    allowed_extensions=["mp4", "mkv", "mov", "webm", "avi"],
                    on_picked=_apply_picked,
                )
                return

            files = await self.file_picker.pick_files(
                initial_directory=str(vg_dir),
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["mp4", "mkv", "mov", "webm", "avi"],
            )
            if files and files[0].path:
                _apply_picked(format_relative_if_inside(files[0].path, vg_dir))

        def _parse_opt_int(raw: str | None) -> int | None:
            s = (raw or "").strip()
            if not s:
                return None
            try:
                return int(s)
            except ValueError:
                return None

        def _ensure_render_override() -> VideoSourceOverrideRender:
            if item.render is None:
                item.render = VideoSourceOverrideRender()
            return item.render

        def _on_mode_select(e: ft.Event[ft.Dropdown]) -> None:
            v = e.control.value
            if v == "highlights":
                item.mode = "highlights"
            elif v == "speech_split":
                item.mode = "speech_split"
            else:
                item.mode = None

        def _on_film_select(e: ft.Event[ft.Dropdown]) -> None:
            v = e.control.value
            item.is_film = None if v == "auto" else (v == "film")

        def _on_ft_override(e: ft.Event[ft.Dropdown]) -> None:
            v = e.control.value
            ov = _ensure_render_override()
            ov.face_tracking = None if v == "inherit" else (v == "true")

        def _on_mirror_override(e: ft.Event[ft.Dropdown]) -> None:
            v = e.control.value
            ov = _ensure_render_override()
            ov.mirror = None if v == "inherit" else (v == "true")

        def _on_top_zoom_override(e: ft.Event[ft.TextField]) -> None:
            raw = (e.control.value or "").strip().replace(",", ".")
            ov = _ensure_render_override()
            if not raw:
                ov.top_zoom = None
                e.control.error = None
            else:
                try:
                    val = float(raw)
                    if 1.0 <= val <= 3.0:
                        ov.top_zoom = round(val, 2)
                        e.control.error = None
                    else:
                        e.control.error = "1.0..3.0"
                except ValueError:
                    e.control.error = "1.0..3.0"
            self.page.update()

        browse_controls: list[ft.Control] = []
        if item.type == "video":
            browse_controls = [
                ft.IconButton(
                    icon=ft.Icons.VIDEO_FILE_OUTLINED,
                    tooltip="Выбрать видеофайл",
                    icon_size=18,
                    on_click=_pick_file,
                ),
                ft.IconButton(
                    icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                    tooltip="Выбрать папку с видео",
                    icon_size=18,
                    on_click=_pick_dir,
                ),
            ]

        ft_val = (
            "inherit"
            if (item.render is None or item.render.face_tracking is None)
            else ("true" if item.render.face_tracking else "false")
        )
        mirror_val = (
            "inherit"
            if (item.render is None or item.render.mirror is None)
            else ("true" if item.render.mirror else "false")
        )
        top_zoom_val = (
            ""
            if (item.render is None or item.render.top_zoom is None)
            else str(item.render.top_zoom)
        )

        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(4),
            padding=ft.Padding.all(14),
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(
                                ft.Icons.SMART_DISPLAY
                                if item.type == "youtube"
                                else ft.Icons.VIDEO_LIBRARY,
                                size=18,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            ft.Text(
                                f"Источник #{idx + 1}",
                                weight=ft.FontWeight.W_600,
                                size=13,
                            ),
                            _type_badge("YOUTUBE" if item.type == "youtube" else "VIDEO"),
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_color=ft.Colors.ON_SURFACE_VARIANT,
                                icon_size=18,
                                tooltip="Удалить источник",
                                disabled=not can_delete,
                                on_click=lambda _, i=idx: self._remove_top_source(i),
                            ),
                        ],
                    ),
                    ft.Row(spacing=8, controls=[src_tf, *browse_controls]),
                    ft.Row(
                        spacing=10,
                        controls=[
                            ft.TextField(
                                label="Название (caption_name)",
                                value=item.caption_name or "",
                                dense=True,
                                expand=True,
                                on_change=lambda e: setattr(
                                    item, "caption_name", (e.control.value or "").strip() or None
                                ),
                            ),
                            ft.TextField(
                                label="Кодовое имя (codename)",
                                value=item.codename or "",
                                dense=True,
                                width=170,
                                on_change=lambda e: setattr(
                                    item, "codename", (e.control.value or "").strip() or None
                                ),
                            ),
                            ft.TextField(
                                label="Своя папка вывода",
                                value=item.output_dir or "",
                                dense=True,
                                expand=True,
                                hint_text="по умолчанию глобальная",
                                on_change=lambda e: setattr(
                                    item, "output_dir", (e.control.value or "").strip() or None
                                ),
                            ),
                        ],
                    ),
                    ft.ExpansionTile(
                        title=ft.Text(
                            "Дополнительные параметры источника (каталог, длительность, рендер)",
                            size=13,
                        ),
                        controls_padding=ft.Padding.only(top=8, bottom=4),
                        controls=[
                            ft.Column(
                                spacing=10,
                                controls=[
                                    ft.Row(
                                        wrap=True,
                                        spacing=10,
                                        run_spacing=10,
                                        controls=[
                                            ft.Dropdown(
                                                label="Режим нарезки",
                                                dense=True,
                                                width=210,
                                                value=item.mode or "default",
                                                on_select=_on_mode_select,
                                                options=[
                                                    ft.DropdownOption(
                                                        key="default", text="Глобальный"
                                                    ),
                                                    ft.DropdownOption(
                                                        key="highlights", text="LLM-сегментация"
                                                    ),
                                                    ft.DropdownOption(
                                                        key="speech_split", text="По паузам речи"
                                                    ),
                                                ],
                                            ),
                                            ft.TextField(
                                                label="Мин. сек",
                                                value=str(item.min_part_duration or ""),
                                                dense=True,
                                                width=110,
                                                on_change=lambda e: setattr(
                                                    item,
                                                    "min_part_duration",
                                                    _parse_opt_int(e.control.value),
                                                ),
                                            ),
                                            ft.TextField(
                                                label="Макс. сек",
                                                value=str(item.part_duration or ""),
                                                dense=True,
                                                width=110,
                                                on_change=lambda e: setattr(
                                                    item,
                                                    "part_duration",
                                                    _parse_opt_int(e.control.value),
                                                ),
                                            ),
                                            ft.TextField(
                                                label="Макс. клипов",
                                                value=str(item.parts_count or ""),
                                                dense=True,
                                                width=120,
                                                on_change=lambda e: setattr(
                                                    item,
                                                    "parts_count",
                                                    _parse_opt_int(e.control.value),
                                                ),
                                            ),
                                        ],
                                    ),
                                    ft.Row(
                                        wrap=True,
                                        spacing=10,
                                        run_spacing=10,
                                        controls=[
                                            ft.Dropdown(
                                                label="Тип в каталоге",
                                                dense=True,
                                                width=170,
                                                value="auto"
                                                if item.is_film is None
                                                else ("film" if item.is_film else "series"),
                                                on_select=_on_film_select,
                                                options=[
                                                    ft.DropdownOption(key="auto", text="Авто"),
                                                    ft.DropdownOption(key="film", text="Фильм"),
                                                    ft.DropdownOption(key="series", text="Сериал"),
                                                ],
                                            ),
                                            ft.TextField(
                                                label="Базовый код (XXXXX)",
                                                value=item.base_code or "",
                                                dense=True,
                                                width=160,
                                                on_change=lambda e: setattr(
                                                    item,
                                                    "base_code",
                                                    (e.control.value or "").strip() or None,
                                                ),
                                            ),
                                            ft.TextField(
                                                label="Сезон",
                                                value=str(item.season or ""),
                                                dense=True,
                                                width=95,
                                                on_change=lambda e: setattr(
                                                    item, "season", _parse_opt_int(e.control.value)
                                                ),
                                            ),
                                            ft.TextField(
                                                label="Серия",
                                                value=str(item.episode or ""),
                                                dense=True,
                                                width=95,
                                                on_change=lambda e: setattr(
                                                    item,
                                                    "episode",
                                                    _parse_opt_int(e.control.value),
                                                ),
                                            ),
                                        ],
                                    ),
                                    ft.Row(
                                        wrap=True,
                                        spacing=10,
                                        run_spacing=10,
                                        controls=[
                                            ft.Dropdown(
                                                label="Трекинг лиц",
                                                dense=True,
                                                width=190,
                                                value=ft_val,
                                                on_select=_on_ft_override,
                                                options=[
                                                    ft.DropdownOption(
                                                        key="inherit", text="Глобальный"
                                                    ),
                                                    ft.DropdownOption(key="true", text="Включён"),
                                                    ft.DropdownOption(
                                                        key="false", text="Выключен (2D/мульт)"
                                                    ),
                                                ],
                                            ),
                                            ft.Dropdown(
                                                label="Зеркало (mirror)",
                                                dense=True,
                                                width=180,
                                                value=mirror_val,
                                                on_select=_on_mirror_override,
                                                options=[
                                                    ft.DropdownOption(
                                                        key="inherit", text="Глобальный"
                                                    ),
                                                    ft.DropdownOption(key="true", text="Включено"),
                                                    ft.DropdownOption(
                                                        key="false", text="Выключено"
                                                    ),
                                                ],
                                            ),
                                            ft.TextField(
                                                label="Зум верха (top_zoom)",
                                                value=top_zoom_val,
                                                hint_text="глоб. (1.0–3.0)",
                                                dense=True,
                                                width=175,
                                                on_change=_on_top_zoom_override,
                                            ),
                                        ],
                                    ),
                                ],
                            )
                        ],
                    ),
                ],
            ),
        )

    def _build_bottom_item(
        self, idx: int, item: VideoSourceConfig, can_delete: bool
    ) -> ft.Container:
        vg_dir = self.state.settings.resolved_video_gen_dir()

        if item.type == "pygame":
            if item.source not in ("runner", "particles", "matrix"):
                item.source = "runner"
            content_row: ft.Control = ft.Dropdown(
                label="Процедурная сцена PyGame (headless)",
                dense=True,
                width=320,
                value=item.source,
                on_select=lambda e: setattr(item, "source", e.control.value or "runner"),
                options=[
                    ft.DropdownOption(key="runner", text="runner — Неоновый раннер"),
                    ft.DropdownOption(key="particles", text="particles — Частицы"),
                    ft.DropdownOption(key="matrix", text="matrix — Цифровая Матрица"),
                ],
            )
        else:
            src_tf = ft.TextField(
                label="Папка или видеофайл фонового потока",
                value=item.source,
                dense=True,
                expand=True,
                text_style=ft.TextStyle(font_family="monospace", size=12),
                on_change=lambda e: setattr(item, "source", (e.control.value or "").strip()),
            )

            def _apply_picked(val: str) -> None:
                src_tf.value = val
                item.source = val
                self.page.update()

            async def _pick_dir(_: ft.Event[ft.IconButton]) -> None:
                if self.state.settings.is_remote:
                    await pick_remote_path_dialog(
                        self.page,
                        self.state.settings,
                        title="Выбор удалённой папки фона",
                        pick_mode="dir",
                        on_picked=_apply_picked,
                    )
                    return

                chosen = await self.file_picker.get_directory_path(initial_directory=str(vg_dir))
                if chosen:
                    _apply_picked(format_relative_if_inside(chosen, vg_dir))

            async def _pick_file(_: ft.Event[ft.IconButton]) -> None:
                if self.state.settings.is_remote:
                    await pick_remote_path_dialog(
                        self.page,
                        self.state.settings,
                        title="Выбор удалённого видеофайла фона",
                        pick_mode="file",
                        allowed_extensions=["mp4", "mkv", "mov", "webm", "avi"],
                        on_picked=_apply_picked,
                    )
                    return

                files = await self.file_picker.pick_files(
                    initial_directory=str(vg_dir),
                    file_type=ft.FilePickerFileType.CUSTOM,
                    allowed_extensions=["mp4", "mkv", "mov", "webm", "avi"],
                )
                if files and files[0].path:
                    _apply_picked(format_relative_if_inside(files[0].path, vg_dir))

            content_row = ft.Row(
                spacing=8,
                controls=[
                    src_tf,
                    ft.IconButton(
                        icon=ft.Icons.VIDEO_FILE_OUTLINED,
                        tooltip="Выбрать видеофайл",
                        icon_size=18,
                        on_click=_pick_file,
                    ),
                    ft.IconButton(
                        icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                        tooltip="Выбрать папку",
                        icon_size=18,
                        on_click=_pick_dir,
                    ),
                ],
            )

        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(4),
            padding=ft.Padding.all(14),
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        spacing=8,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        controls=[
                            ft.Icon(
                                ft.Icons.GRID_VIEW if item.type == "pygame" else ft.Icons.THEATERS,
                                size=18,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            ft.Text(
                                f"Фон #{idx + 1}",
                                weight=ft.FontWeight.W_600,
                                size=13,
                            ),
                            _type_badge("PYGAME" if item.type == "pygame" else "VIDEO"),
                            ft.Container(expand=True),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_OUTLINE,
                                icon_color=ft.Colors.ON_SURFACE_VARIANT,
                                icon_size=18,
                                tooltip="Удалить фон",
                                disabled=not can_delete,
                                on_click=lambda _, i=idx: self._remove_bottom_source(i),
                            ),
                        ],
                    ),
                    content_row,
                ],
            ),
        )

    def _rebuild_lists(self) -> None:
        tops = self.state.config.videos.top_videos
        self.top_list_col.controls = [
            self._build_top_item(i, src, len(tops) > 1) for i, src in enumerate(tops)
        ]

        bottoms = self.state.config.videos.bottom_videos
        self.bottom_list_col.controls = [
            self._build_bottom_item(i, src, len(bottoms) > 1) for i, src in enumerate(bottoms)
        ]

    def build(self) -> ft.Control:
        self._rebuild_lists()

        return ft.Column(
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "Верхние видео — Основной контент (top_videos)",
                    ft.Icons.VIDEO_COLLECTION_OUTLINED,
                    subtitle="Фильмы, сериалы, подкасты (локальные/удалённые папки/файлы или ссылки YouTube)",
                    trailing=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[
                            ft.FilledTonalButton(
                                content="Видео / Папка",
                                icon=ft.Icons.ADD,
                                on_click=lambda: self._add_top_source("video"),
                            ),
                            ft.OutlinedButton(
                                content="YouTube URL",
                                icon=ft.Icons.ADD_LINK,
                                on_click=lambda: self._add_top_source("youtube"),
                            ),
                        ],
                    ),
                    controls=[self.top_list_col],
                ),
                section_card(
                    "Нижние видео — Фоновый поток (bottom_videos)",
                    ft.Icons.LAYERS_OUTLINED,
                    subtitle="Фоновые видеоролики или процедурный headless-генератор PyGame",
                    trailing=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[
                            ft.FilledTonalButton(
                                content="Видео / Папка",
                                icon=ft.Icons.ADD,
                                on_click=lambda: self._add_bottom_source("video"),
                            ),
                            ft.OutlinedButton(
                                content="PyGame пресет",
                                icon=ft.Icons.GRID_VIEW,
                                on_click=lambda: self._add_bottom_source("pygame"),
                            ),
                        ],
                    ),
                    controls=[self.bottom_list_col],
                ),
            ],
        )
