"""Экран настроек видеомонтажа, цветокоррекции, антидетекта и ассетов (секции render + assets)."""

from __future__ import annotations

from typing import Literal, cast

import flet as ft

from video_gen_gui.state import AppState
from video_gen_gui.views.components import number_slider_row, path_picker_row, section_card


class RenderView:
    """Редактор секций render и assets."""

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        file_picker: ft.FilePicker,
    ) -> None:
        self.page = page
        self.state = state
        self.file_picker = file_picker

    def _safe_int(self, raw: str | None, fallback: int) -> int:
        try:
            return int((raw or "").strip())
        except ValueError:
            return fallback

    def _parse_opt_int(self, raw: str | None) -> int | None:
        s = (raw or "").strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            return None

    def build(self) -> ft.Control:
        r = self.state.config.render
        a = self.state.config.assets

        interval_minutes_tf = ft.TextField(
            label="Интервал показа (минуты, N)",
            value=str(r.banner_interval_minutes),
            dense=True,
            width=250,
            helper="Например: 0.5 (30 сек), 1, 2, 5",
            visible=r.banner_mode == "interval_minutes",
            disabled=r.banner_mode != "interval_minutes",
        )

        def _on_interval_minutes_change(e: ft.Event[ft.TextField]) -> None:
            raw = (e.control.value or "").strip().replace(",", ".")
            try:
                val = float(raw)
                if 0.0 < val <= 180.0:
                    r.banner_interval_minutes = val
                    e.control.error = None
                else:
                    e.control.error = "Допустимо: 0 < N <= 180"
            except ValueError:
                e.control.error = "Введите число (например 0.5 или 2)"
            self.page.update()

        interval_minutes_tf.on_change = _on_interval_minutes_change

        interval_percent_tf = ft.TextField(
            label="Интервал показа (% длительности видео)",
            value=str(r.banner_interval_percent),
            dense=True,
            width=280,
            suffix="%",
            helper="Например: 25% → показ на 25%, 50%, 75%",
            visible=r.banner_mode == "interval_percent",
            disabled=r.banner_mode != "interval_percent",
        )

        def _on_interval_percent_change(e: ft.Event[ft.TextField]) -> None:
            raw = (e.control.value or "").strip().replace(",", ".")
            try:
                val = float(raw)
                if 5.0 <= val <= 100.0:
                    r.banner_interval_percent = val
                    e.control.error = None
                else:
                    e.control.error = "Допустимо: 5..100%"
            except ValueError:
                e.control.error = "Введите число от 5 до 100"
            self.page.update()

        interval_percent_tf.on_change = _on_interval_percent_change

        banner_freeze_switch = ft.Switch(
            label="Стоп-кадр и размытие фона при показе баннера (banner_freeze)",
            value=r.banner_freeze,
            disabled=r.banner_mode == "overlay",
            on_change=lambda e: setattr(r, "banner_freeze", bool(e.control.value)),
        )

        def _on_banner_mode_select(e: ft.Event[ft.Dropdown]) -> None:
            mode = cast(
                Literal["center", "overlay", "end", "interval_minutes", "interval_percent"],
                e.control.value or "center",
            )
            r.banner_mode = mode
            interval_minutes_tf.visible = mode == "interval_minutes"
            interval_minutes_tf.disabled = mode != "interval_minutes"
            interval_percent_tf.visible = mode == "interval_percent"
            interval_percent_tf.disabled = mode != "interval_percent"
            banner_freeze_switch.disabled = mode == "overlay"
            self.page.update()

        banner_width_tf = ft.TextField(
            label="Ширина баннера (px)",
            value=str(r.banner_width),
            dense=True,
            width=165,
            helper="100..4096",
        )

        def _on_banner_width_change(e: ft.Event[ft.TextField]) -> None:
            raw = (e.control.value or "").strip()
            try:
                val = int(raw)
                if 100 <= val <= 4096:
                    r.banner_width = val
                    e.control.error = None
                else:
                    e.control.error = "100..4096"
            except ValueError:
                e.control.error = "Целое 100..4096"
            self.page.update()

        banner_width_tf.on_change = _on_banner_width_change

        return ft.Column(
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "Разрешение, Сетка кадра и Кодирование FFmpeg",
                    ft.Icons.MEMORY,
                    subtitle="Параметры выходного вертикального видео и аппаратного ускорения NVENC",
                    controls=[
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            controls=[
                                ft.TextField(
                                    label="Ширина (px)",
                                    value=str(r.width),
                                    dense=True,
                                    width=130,
                                    on_change=lambda e: setattr(
                                        r, "width", self._safe_int(e.control.value, 1080)
                                    ),
                                ),
                                ft.TextField(
                                    label="Высота (px)",
                                    value=str(r.height),
                                    dense=True,
                                    width=130,
                                    on_change=lambda e: setattr(
                                        r, "height", self._safe_int(e.control.value, 1920)
                                    ),
                                ),
                                ft.TextField(
                                    label="FPS",
                                    value=str(r.fps),
                                    dense=True,
                                    width=100,
                                    on_change=lambda e: setattr(
                                        r, "fps", self._safe_int(e.control.value, 30)
                                    ),
                                ),
                                ft.TextField(
                                    label="Доля верха (top_parts)",
                                    value=str(r.top_parts),
                                    dense=True,
                                    width=170,
                                    helper="1:1 = пополам, 1:2 = 1/3 верх",
                                    on_change=lambda e: setattr(
                                        r, "top_parts", max(1, self._safe_int(e.control.value, 1))
                                    ),
                                ),
                                ft.TextField(
                                    label="Доля низа (bottom_parts)",
                                    value=str(r.bottom_parts),
                                    dense=True,
                                    width=185,
                                    on_change=lambda e: setattr(
                                        r,
                                        "bottom_parts",
                                        max(1, self._safe_int(e.control.value, 2)),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            controls=[
                                ft.Dropdown(
                                    label="Видеокодек",
                                    dense=True,
                                    width=220,
                                    value=r.video_codec,
                                    on_select=lambda e: setattr(
                                        r,
                                        "video_codec",
                                        cast(
                                            Literal[
                                                "h264_nvenc", "libx264", "h265_nvenc", "libx265"
                                            ],
                                            e.control.value or "h264_nvenc",
                                        ),
                                    ),
                                    options=[
                                        ft.DropdownOption(
                                            key="h264_nvenc", text="h264_nvenc (NVIDIA GPU)"
                                        ),
                                        ft.DropdownOption(
                                            key="h265_nvenc", text="h265_nvenc (HEVC GPU)"
                                        ),
                                        ft.DropdownOption(key="libx264", text="libx264 (CPU)"),
                                        ft.DropdownOption(key="libx265", text="libx265 (HEVC CPU)"),
                                    ],
                                ),
                                ft.TextField(
                                    label="Preset (p1..p7 / fast)",
                                    value=r.preset,
                                    dense=True,
                                    width=165,
                                    on_change=lambda e: setattr(
                                        r, "preset", (e.control.value or "p4").strip()
                                    ),
                                ),
                                ft.TextField(
                                    label="Maxrate",
                                    value=r.maxrate,
                                    dense=True,
                                    width=120,
                                    on_change=lambda e: setattr(
                                        r, "maxrate", (e.control.value or "6M").strip()
                                    ),
                                ),
                                ft.Dropdown(
                                    label="Выбор фрагмента фона",
                                    dense=True,
                                    width=210,
                                    value=r.bottom_mode,
                                    on_select=lambda e: setattr(
                                        r,
                                        "bottom_mode",
                                        cast(
                                            Literal["random", "sequential"],
                                            e.control.value or "random",
                                        ),
                                    ),
                                    options=[
                                        ft.DropdownOption(key="random", text="Случайный (random)"),
                                        ft.DropdownOption(
                                            key="sequential", text="По порядку (sequential)"
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        number_slider_row(
                            label="Качество CQ / CRF (меньше = выше качество и больше размер файла)",
                            value=r.cq,
                            min_val=14,
                            max_val=40,
                            divisions=26,
                            is_int=True,
                            on_changed=lambda v: setattr(r, "cq", int(v)),
                        ),
                    ],
                ),
                section_card(
                    "Трекинг лиц, Кадрирование и Антидетект (Content ID)",
                    ft.Icons.CROP_FREE,
                    subtitle="Автофокус на спикере и уникализация видео/аудио хэшей каждого клипа",
                    controls=[
                        ft.Row(
                            wrap=True,
                            spacing=22,
                            controls=[
                                ft.Switch(
                                    label="Face Tracking (центрирование по лицу)",
                                    value=r.face_tracking,
                                    on_change=lambda e: setattr(
                                        r, "face_tracking", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Зеркало верхнего видео (hflip)",
                                    value=r.mirror,
                                    on_change=lambda e: setattr(r, "mirror", bool(e.control.value)),
                                ),
                                ft.Switch(
                                    label="Антидетект уникализация",
                                    value=r.enable_antidetect,
                                    on_change=lambda e: setattr(
                                        r, "enable_antidetect", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Микро-джиттер скорости (0.985x–1.015x)",
                                    value=r.speed_jitter,
                                    on_change=lambda e: setattr(
                                        r, "speed_jitter", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Очищать метаданные файла",
                                    value=r.strip_metadata,
                                    on_change=lambda e: setattr(
                                        r, "strip_metadata", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Размытая подложка (boxblur)",
                                    value=r.enable_canvas_blur,
                                    on_change=lambda e: setattr(
                                        r, "enable_canvas_blur", bool(e.control.value)
                                    ),
                                ),
                            ],
                        ),
                        number_slider_row(
                            label="Кроп-зум верхнего видео (top_zoom)",
                            value=r.top_zoom,
                            min_val=1.0,
                            max_val=2.5,
                            divisions=30,
                            suffix="x",
                            on_changed=lambda v: setattr(r, "top_zoom", v),
                        ),
                    ],
                ),
                section_card(
                    "Цветокоррекция и Резкость",
                    ft.Icons.TUNE,
                    subtitle="Цветокоррекция и резкость верхнего/нижнего слоёв, фильтр unsharp mask",
                    controls=[
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Контраст верха",
                                        value=r.contrast,
                                        min_val=0.7,
                                        max_val=1.6,
                                        divisions=18,
                                        on_changed=lambda v: setattr(r, "contrast", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Насыщенность верха",
                                        value=r.saturation,
                                        min_val=0.5,
                                        max_val=2.0,
                                        divisions=30,
                                        on_changed=lambda v: setattr(r, "saturation", v),
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
                                        label="Контраст фона (низа)",
                                        value=r.bottom_contrast,
                                        min_val=0.7,
                                        max_val=1.6,
                                        divisions=18,
                                        on_changed=lambda v: setattr(r, "bottom_contrast", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Насыщенность фона (низа)",
                                        value=r.bottom_saturation,
                                        min_val=0.5,
                                        max_val=2.0,
                                        divisions=30,
                                        on_changed=lambda v: setattr(r, "bottom_saturation", v),
                                    ),
                                ),
                            ],
                        ),
                        ft.Switch(
                            label="Фильтр резкости (sharpen)",
                            value=r.sharpen,
                            on_change=lambda e: setattr(r, "sharpen", bool(e.control.value)),
                        ),
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Сила резкости Luma",
                                        value=r.unsharp_luma,
                                        min_val=0.0,
                                        max_val=2.0,
                                        divisions=20,
                                        on_changed=lambda v: setattr(r, "unsharp_luma", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Зерно антидетекта (noise_top)",
                                        value=r.noise_top,
                                        min_val=0,
                                        max_val=25,
                                        divisions=25,
                                        is_int=True,
                                        on_changed=lambda v: setattr(r, "noise_top", int(v)),
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
                section_card(
                    "Баннеры, расписание показа и фоновая музыка",
                    ft.Icons.LAYERS_OUTLINED,
                    subtitle="Оставьте пути пустыми, если баннер или музыка не требуются",
                    controls=[
                        path_picker_row(
                            label="Одиночный файл баннера (assets.banner)",
                            value=a.banner or "",
                            hint_text="Пусто = без одиночного баннера",
                            file_picker=self.file_picker,
                            pick_mode="file",
                            allowed_extensions=["mp4", "mov", "webm", "png", "gif"],
                            base_dir_getter=self.state.settings.resolved_video_gen_dir,
                            page=self.page,
                            settings_getter=lambda: self.state.settings,
                            on_changed=lambda v: setattr(a, "banner", v.strip() or None),
                        ),
                        path_picker_row(
                            label="Папка со случайными баннерами (assets.banners_dir)",
                            value=a.banners_dir or "",
                            hint_text="Пусто = отключено",
                            file_picker=self.file_picker,
                            pick_mode="dir",
                            base_dir_getter=self.state.settings.resolved_video_gen_dir,
                            page=self.page,
                            settings_getter=lambda: self.state.settings,
                            on_changed=lambda v: setattr(a, "banners_dir", v.strip() or None),
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                            controls=[
                                ft.Dropdown(
                                    label="Режим показа баннера (banner_mode)",
                                    dense=True,
                                    width=285,
                                    value=r.banner_mode,
                                    on_select=_on_banner_mode_select,
                                    options=[
                                        ft.DropdownOption(
                                            key="center", text="В середине клипа (center)"
                                        ),
                                        ft.DropdownOption(
                                            key="end", text="В конце клипа (end)"
                                        ),
                                        ft.DropdownOption(
                                            key="overlay", text="Постоянный оверлей (overlay)"
                                        ),
                                        ft.DropdownOption(
                                            key="interval_minutes",
                                            text="Каждые N минут (interval_minutes)",
                                        ),
                                        ft.DropdownOption(
                                            key="interval_percent",
                                            text="Каждые N% видео (interval_percent)",
                                        ),
                                    ],
                                ),
                                interval_minutes_tf,
                                interval_percent_tf,
                                ft.Dropdown(
                                    label="Привязка оверлея",
                                    dense=True,
                                    width=200,
                                    value=r.banner_anchor,
                                    on_select=lambda e: setattr(
                                        r,
                                        "banner_anchor",
                                        cast(
                                            Literal[
                                                "top-left",
                                                "top-right",
                                                "bottom-left",
                                                "bottom-right",
                                                "center",
                                            ],
                                            e.control.value or "center",
                                        ),
                                    ),
                                    options=[
                                        ft.DropdownOption(key="center", text="По центру"),
                                        ft.DropdownOption(key="top-left", text="Сверху слева"),
                                        ft.DropdownOption(key="top-right", text="Сверху справа"),
                                        ft.DropdownOption(key="bottom-left", text="Снизу слева"),
                                        ft.DropdownOption(key="bottom-right", text="Снизу справа"),
                                    ],
                                ),
                            ],
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=20,
                            run_spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                banner_freeze_switch,
                                ft.Switch(
                                    label="Автообрезка полей (banner_autocrop)",
                                    value=r.banner_autocrop,
                                    on_change=lambda e: setattr(
                                        r, "banner_autocrop", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Chromakey (зелёный фон)",
                                    value=r.banner_chromakey,
                                    on_change=lambda e: setattr(
                                        r, "banner_chromakey", bool(e.control.value)
                                    ),
                                ),
                                ft.TextField(
                                    label="Цвет хромакея",
                                    value=r.banner_chroma_color,
                                    dense=True,
                                    width=140,
                                    on_change=lambda e: setattr(
                                        r,
                                        "banner_chroma_color",
                                        (e.control.value or "green").strip(),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                            controls=[
                                banner_width_tf,
                                ft.TextField(
                                    label="Смещение X (banner_x)",
                                    value="" if r.banner_x is None else str(r.banner_x),
                                    hint_text="авто",
                                    dense=True,
                                    width=145,
                                    on_change=lambda e: setattr(
                                        r, "banner_x", self._parse_opt_int(e.control.value)
                                    ),
                                ),
                                ft.TextField(
                                    label="Смещение Y (banner_y)",
                                    value="" if r.banner_y is None else str(r.banner_y),
                                    hint_text="авто",
                                    dense=True,
                                    width=145,
                                    on_change=lambda e: setattr(
                                        r, "banner_y", self._parse_opt_int(e.control.value)
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
                                        label="Масштаб баннера (banner_scale)",
                                        value=max(0.2, min(2.5, r.banner_scale)),
                                        min_val=0.2,
                                        max_val=2.5,
                                        divisions=23,
                                        suffix="x",
                                        on_changed=lambda v: setattr(r, "banner_scale", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Прозрачность баннера (banner_opacity)",
                                        value=max(0.1, min(1.0, r.banner_opacity)),
                                        min_val=0.1,
                                        max_val=1.0,
                                        divisions=18,
                                        on_changed=lambda v: setattr(r, "banner_opacity", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Размытие фона под баннер (blur_sigma)",
                                        value=max(0.0, min(80.0, r.blur_sigma)),
                                        min_val=0.0,
                                        max_val=80.0,
                                        divisions=40,
                                        on_changed=lambda v: setattr(r, "blur_sigma", v),
                                    ),
                                ),
                            ],
                        ),
                        ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                        path_picker_row(
                            label="Папка с фоновой музыкой (assets.music_dir)",
                            value=a.music_dir or "",
                            hint_text="Пусто = без фоновой музыки",
                            file_picker=self.file_picker,
                            pick_mode="dir",
                            base_dir_getter=self.state.settings.resolved_video_gen_dir,
                            page=self.page,
                            settings_getter=lambda: self.state.settings,
                            on_changed=lambda v: setattr(a, "music_dir", v.strip() or None),
                        ),
                        ft.Row(
                            spacing=20,
                            controls=[
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Громкость речи (speech_volume)",
                                        value=r.speech_volume,
                                        min_val=0.2,
                                        max_val=2.5,
                                        divisions=46,
                                        on_changed=lambda v: setattr(r, "speech_volume", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Громкость фоновой музыки",
                                        value=r.music_volume,
                                        min_val=0.0,
                                        max_val=0.5,
                                        divisions=50,
                                        on_changed=lambda v: setattr(r, "music_volume", v),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Приглушение под баннер (duck)",
                                        value=r.duck_volume,
                                        min_val=0.0,
                                        max_val=1.0,
                                        divisions=20,
                                        on_changed=lambda v: setattr(r, "duck_volume", v),
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        )
