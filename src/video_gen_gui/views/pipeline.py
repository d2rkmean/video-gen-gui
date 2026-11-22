"""Экран настроек конвейера и LLM (секция params): Whisper, LLM-анализ, длительность и кэш."""

from __future__ import annotations

import shutil
from typing import Literal, cast

import flet as ft

from video_gen_gui.remote import clear_remote_cache
from video_gen_gui.state import AppState
from video_gen_gui.views.components import (
    number_slider_row,
    path_picker_row,
    section_card,
    show_toast,
)


class PipelineView:
    """Редактор секции params конфигурации video-gen."""

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        file_picker: ft.FilePicker,
    ) -> None:
        self.page = page
        self.state = state
        self.file_picker = file_picker

    def _clear_cache_dialog(self) -> None:
        is_remote = self.state.settings.is_remote
        if is_remote:
            remote_base = self.state.settings.resolved_remote_video_gen_dir().rstrip("/") or "~"
            dest = self.state.settings.ssh_destination() or "ssh"
            target_desc = f"{dest}:{remote_base}/cache"
        else:
            cache_dir = self.state.settings.resolved_video_gen_dir() / "cache"
            target_desc = str(cache_dir)

        async def _do_remote_clear() -> None:
            ok, msg = await clear_remote_cache(self.state.settings)
            show_toast(self.page, msg, error=not ok)
            self.page.update()

        def _confirm() -> None:
            self.page.pop_dialog()
            if is_remote:
                self.page.run_task(_do_remote_clear)
                return
            local_cache = self.state.settings.resolved_video_gen_dir() / "cache"
            if local_cache.is_dir():
                shutil.rmtree(local_cache, ignore_errors=True)
                local_cache.mkdir(parents=True, exist_ok=True)
                show_toast(self.page, f"Кэш очищен: {local_cache}")
            else:
                show_toast(self.page, "Папка кэша уже пуста")

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Очистить кэш конвейера?"),
            content=ft.Text(
                "Будут удалены кэшированные транскрипции Whisper, результаты LLM-анализа и промежуточные рендеры в:\n"
                f"{target_desc}"
            ),
            actions=[
                ft.TextButton(content="Отмена", on_click=lambda: self.page.pop_dialog()),
                ft.FilledButton(
                    content="Удалить кэш",
                    bgcolor=ft.Colors.RED_700,
                    color=ft.Colors.WHITE,
                    on_click=_confirm,
                ),
            ],
        )
        self.page.show_dialog(dlg)

    def build(self) -> ft.Control:
        p = self.state.config.params

        # Контролы для динамического обновления пресетов LLM
        ai_backend_dd = ft.Dropdown(
            label="Бэкенд анализа",
            dense=True,
            width=190,
            value=p.analysis_backend,
            options=[
                ft.DropdownOption(key="openai", text="OpenAI"),
                ft.DropdownOption(key="groq", text="Groq"),
                ft.DropdownOption(key="custom", text="Custom (Gemini / Ollama / др.)"),
            ],
        )
        ai_base_tf = ft.TextField(
            label="Base URL (OpenAI-compatible)",
            value=p.analysis_api_base,
            dense=True,
            width=340,
            on_change=lambda e: setattr(p, "analysis_api_base", (e.control.value or "").strip()),
        )
        ai_model_tf = ft.TextField(
            label="Модель LLM-анализа",
            value=p.analysis_model,
            dense=True,
            width=260,
            on_change=lambda e: setattr(p, "analysis_model", (e.control.value or "").strip()),
        )
        ai_key_tf = ft.TextField(
            label="API Ключ (analysis_api_key)",
            value=p.analysis_api_key,
            password=True,
            can_reveal_password=True,
            dense=True,
            expand=True,
            hint_text="Если пусто — берётся из OPENAI_API_KEY / GEMINI_API_KEY",
            on_change=lambda e: setattr(p, "analysis_api_key", (e.control.value or "").strip()),
        )

        def _apply_ai_preset(
            backend: Literal["openai", "groq", "custom"], base_url: str, model: str
        ) -> None:
            p.analysis_backend = backend
            p.analysis_api_base = base_url
            p.analysis_model = model
            ai_backend_dd.value = backend
            ai_base_tf.value = base_url
            ai_model_tf.value = model
            self.page.update()

        def _on_ai_backend_select(e: ft.Event[ft.Dropdown]) -> None:
            val = cast(Literal["openai", "groq", "custom"], e.control.value or "openai")
            p.analysis_backend = val
            if val == "openai":
                ai_base_tf.value = "https://api.openai.com/v1"
                p.analysis_api_base = ai_base_tf.value
            elif val == "groq":
                ai_base_tf.value = "https://api.groq.com/openai/v1"
                p.analysis_api_base = ai_base_tf.value
            self.page.update()

        ai_backend_dd.on_select = _on_ai_backend_select

        # Переключение локального / облачного Whisper
        local_whisper_box = ft.Column(
            visible=p.transcribe_backend == "local",
            spacing=12,
            controls=[
                ft.Row(
                    wrap=True,
                    spacing=12,
                    run_spacing=10,
                    controls=[
                        ft.Dropdown(
                            label="Модель faster-whisper",
                            dense=True,
                            width=220,
                            value=p.whisper_model,
                            on_select=lambda e: setattr(
                                p, "whisper_model", e.control.value or "large-v3"
                            ),
                            options=[
                                ft.DropdownOption(key="tiny", text="tiny"),
                                ft.DropdownOption(key="base", text="base"),
                                ft.DropdownOption(key="small", text="small"),
                                ft.DropdownOption(key="medium", text="medium"),
                                ft.DropdownOption(key="large-v3", text="large-v3 (макс. точность)"),
                                ft.DropdownOption(key="large-v3-turbo", text="large-v3-turbo"),
                            ],
                        ),
                        ft.Dropdown(
                            label="Устройство",
                            dense=True,
                            width=160,
                            value=p.whisper_device,
                            on_select=lambda e: setattr(
                                p,
                                "whisper_device",
                                cast(Literal["cuda", "cpu", "auto"], e.control.value or "cuda"),
                            ),
                            options=[
                                ft.DropdownOption(key="cuda", text="CUDA (GPU)"),
                                ft.DropdownOption(key="cpu", text="CPU"),
                                ft.DropdownOption(key="auto", text="Auto"),
                            ],
                        ),
                        ft.Dropdown(
                            label="Тип вычислений (Compute Type)",
                            dense=True,
                            width=210,
                            value=p.whisper_compute_type,
                            on_select=lambda e: setattr(
                                p,
                                "whisper_compute_type",
                                cast(
                                    Literal["float16", "int8_float16", "int8", "float32"],
                                    e.control.value or "float16",
                                ),
                            ),
                            options=[
                                ft.DropdownOption(key="float16", text="float16 (GPU стандарт)"),
                                ft.DropdownOption(
                                    key="int8_float16", text="int8_float16 (экономия VRAM)"
                                ),
                                ft.DropdownOption(key="int8", text="int8"),
                                ft.DropdownOption(key="float32", text="float32 (CPU)"),
                            ],
                        ),
                        ft.TextField(
                            label="Язык (ISO)",
                            value=p.language,
                            dense=True,
                            width=110,
                            on_change=lambda e: setattr(
                                p, "language", (e.control.value or "ru").strip()
                            ),
                        ),
                    ],
                ),
                number_slider_row(
                    label="Beam Size (ширина луча декодирования)",
                    value=p.beam_size,
                    min_val=1,
                    max_val=10,
                    divisions=9,
                    is_int=True,
                    on_changed=lambda v: setattr(p, "beam_size", int(v)),
                ),
            ],
        )

        cloud_whisper_box = ft.Column(
            visible=p.transcribe_backend != "local",
            spacing=10,
            controls=[
                ft.Row(
                    spacing=12,
                    controls=[
                        ft.TextField(
                            label="API Ключ транскрипции (Groq / OpenAI)",
                            value=p.transcribe_api_key,
                            password=True,
                            can_reveal_password=True,
                            dense=True,
                            expand=True,
                            on_change=lambda e: setattr(
                                p, "transcribe_api_key", (e.control.value or "").strip()
                            ),
                        ),
                        ft.TextField(
                            label="Модель API",
                            value=p.transcribe_model,
                            dense=True,
                            width=220,
                            on_change=lambda e: setattr(
                                p, "transcribe_model", (e.control.value or "").strip()
                            ),
                        ),
                    ],
                ),
                ft.TextField(
                    label="Кастомный Base URL транскрипции (опционально)",
                    value=p.transcribe_api_base,
                    dense=True,
                    on_change=lambda e: setattr(
                        p, "transcribe_api_base", (e.control.value or "").strip()
                    ),
                ),
            ],
        )

        def _on_transcribe_backend(e: ft.Event[ft.SegmentedButton]) -> None:
            sel = e.control.selected
            backend = cast(Literal["local", "groq", "openai"], sel[0] if sel else "local")
            p.transcribe_backend = backend
            local_whisper_box.visible = backend == "local"
            cloud_whisper_box.visible = backend != "local"
            self.page.update()

        def _on_mode_change(e: ft.Event[ft.SegmentedButton]) -> None:
            sel = e.control.selected
            p.mode = cast(Literal["highlights", "speech_split"], sel[0] if sel else "highlights")

        return ft.Column(
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "Правила нарезки и Вывод",
                    ft.Icons.CONTENT_CUT,
                    subtitle="Параметры сегментации клипов, лимиты длительности и число параллельных потоков FFmpeg",
                    controls=[
                        path_picker_row(
                            label="Папка сохранения готовых клипов (output_dir)",
                            value=p.output_dir,
                            file_picker=self.file_picker,
                            pick_mode="dir",
                            base_dir_getter=self.state.settings.resolved_video_gen_dir,
                            page=self.page,
                            settings_getter=lambda: self.state.settings,
                            on_changed=lambda v: setattr(p, "output_dir", v or "output/"),
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=16,
                            run_spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Text("Режим нарезки:", weight=ft.FontWeight.W_500),
                                ft.SegmentedButton(
                                    selected=[p.mode],
                                    allow_multiple_selection=False,
                                    on_change=_on_mode_change,
                                    segments=[
                                        ft.Segment(
                                            value="highlights",
                                            label=ft.Text("LLM-сегментация (highlights)"),
                                            icon=ft.Icon(ft.Icons.ANALYTICS_OUTLINED),
                                        ),
                                        ft.Segment(
                                            value="speech_split",
                                            label=ft.Text("По паузам речи (speech_split)"),
                                            icon=ft.Icon(ft.Icons.GRAPHIC_EQ),
                                        ),
                                    ],
                                ),
                                ft.Switch(
                                    label="SponsorBlock (исключать рекламные интеграции)",
                                    value=p.sponsorblock_enabled,
                                    on_change=lambda e: setattr(
                                        p, "sponsorblock_enabled", bool(e.control.value)
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
                                        label="Максимум клипов с 1 видео (parts_count)",
                                        value=p.parts_count,
                                        min_val=1,
                                        max_val=20,
                                        divisions=19,
                                        is_int=True,
                                        on_changed=lambda v: setattr(p, "parts_count", int(v)),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Параллельных процессов FFmpeg",
                                        value=p.concurrent_renders,
                                        min_val=1,
                                        max_val=4,
                                        divisions=3,
                                        is_int=True,
                                        on_changed=lambda v: setattr(
                                            p, "concurrent_renders", int(v)
                                        ),
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
                                        label="Минимальная длительность клипа",
                                        value=p.min_part_duration,
                                        min_val=5,
                                        max_val=170,
                                        divisions=165,
                                        is_int=True,
                                        suffix=" с",
                                        on_changed=lambda v: setattr(
                                            p, "min_part_duration", int(v)
                                        ),
                                    ),
                                ),
                                ft.Container(
                                    expand=True,
                                    content=number_slider_row(
                                        label="Максимальная длительность клипа",
                                        value=p.part_duration,
                                        min_val=10,
                                        max_val=180,
                                        divisions=170,
                                        is_int=True,
                                        suffix=" с",
                                        on_changed=lambda v: setattr(p, "part_duration", int(v)),
                                    ),
                                ),
                            ],
                        ),
                        number_slider_row(
                            label="Порог тишины для отсечения заставок/титров (silence_gap_threshold)",
                            value=p.silence_gap_threshold,
                            min_val=1.0,
                            max_val=30.0,
                            divisions=58,
                            suffix=" с",
                            on_changed=lambda v: setattr(p, "silence_gap_threshold", v),
                        ),
                    ],
                ),
                section_card(
                    "Распознавание речи (Whisper / Облако)",
                    ft.Icons.MIC_NONE,
                    subtitle="Пословная транскрипция и таймкоды для субтитров и LLM-сегментации",
                    controls=[
                        ft.SegmentedButton(
                            selected=[p.transcribe_backend],
                            allow_multiple_selection=False,
                            on_change=_on_transcribe_backend,
                            segments=[
                                ft.Segment(
                                    value="local",
                                    label=ft.Text("Локальный faster-whisper"),
                                    icon=ft.Icon(ft.Icons.MEMORY),
                                ),
                                ft.Segment(
                                    value="groq",
                                    label=ft.Text("Groq Cloud API"),
                                    icon=ft.Icon(ft.Icons.TERMINAL),
                                ),
                                ft.Segment(
                                    value="openai",
                                    label=ft.Text("OpenAI Whisper API"),
                                    icon=ft.Icon(ft.Icons.CLOUD_OUTLINED),
                                ),
                            ],
                        ),
                        local_whisper_box,
                        cloud_whisper_box,
                    ],
                ),
                section_card(
                    "LLM-анализ сегментов",
                    ft.Icons.ANALYTICS_OUTLINED,
                    subtitle="Отбор релевантных фрагментов по транскрипции (используется в режиме highlights)",
                    controls=[
                        ft.Row(
                            wrap=True,
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Text(
                                    "Пресеты провайдеров:",
                                    size=12,
                                    color=ft.Colors.ON_SURFACE_VARIANT,
                                ),
                                ft.OutlinedButton(
                                    content="OpenAI (gpt-4o-mini)",
                                    on_click=lambda _: _apply_ai_preset(
                                        "openai", "https://api.openai.com/v1", "gpt-4o-mini"
                                    ),
                                ),
                                ft.OutlinedButton(
                                    content="Groq (llama-3.3-70b)",
                                    on_click=lambda _: _apply_ai_preset(
                                        "groq",
                                        "https://api.groq.com/openai/v1",
                                        "llama-3.3-70b-versatile",
                                    ),
                                ),
                                ft.OutlinedButton(
                                    content="Google Gemini API",
                                    on_click=lambda _: _apply_ai_preset(
                                        "custom",
                                        "https://generativelanguage.googleapis.com/v1beta/openai/",
                                        "gemini-2.5-flash",
                                    ),
                                ),
                                ft.OutlinedButton(
                                    content="OpenRouter",
                                    on_click=lambda _: _apply_ai_preset(
                                        "custom",
                                        "https://openrouter.ai/api/v1",
                                        "google/gemini-2.5-flash",
                                    ),
                                ),
                                ft.OutlinedButton(
                                    content="Ollama (Local)",
                                    on_click=lambda _: _apply_ai_preset(
                                        "custom", "http://localhost:11434/v1", "qwen2.5:14b"
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            run_spacing=10,
                            controls=[ai_backend_dd, ai_base_tf, ai_model_tf],
                        ),
                        ft.Row(
                            spacing=12,
                            controls=[
                                ai_key_tf,
                                ft.TextField(
                                    label="Повторов API",
                                    value=str(p.max_retries),
                                    dense=True,
                                    width=120,
                                    on_change=lambda e: setattr(
                                        p,
                                        "max_retries",
                                        max(1, min(10, int(e.control.value or 3)))
                                        if (e.control.value or "").isdigit()
                                        else 3,
                                    ),
                                ),
                            ],
                        ),
                    ],
                ),
                section_card(
                    "Кэширование этапов",
                    ft.Icons.CACHED,
                    subtitle="Повторное использование транскрипций, LLM-сегментации и отрендеренных клипов",
                    trailing=ft.OutlinedButton(
                        content="Очистить кэш (cache/)",
                        icon=ft.Icons.DELETE_OUTLINE,
                        on_click=self._clear_cache_dialog,
                    ),
                    controls=[
                        ft.Row(
                            wrap=True,
                            spacing=24,
                            controls=[
                                ft.Switch(
                                    label="Кэш транскрипций Whisper",
                                    value=p.use_whisper_cache,
                                    on_change=lambda e: setattr(
                                        p, "use_whisper_cache", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Кэш LLM-анализа сегментов",
                                    value=p.use_gemini_cache,
                                    on_change=lambda e: setattr(
                                        p, "use_gemini_cache", bool(e.control.value)
                                    ),
                                ),
                                ft.Switch(
                                    label="Пропускать уже отрендеренные клипы (Render Cache)",
                                    value=p.use_render_cache,
                                    on_change=lambda e: setattr(
                                        p, "use_render_cache", bool(e.control.value)
                                    ),
                                ),
                            ],
                        )
                    ],
                ),
            ],
        )
