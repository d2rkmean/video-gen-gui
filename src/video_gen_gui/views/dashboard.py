"""Экран Дашборда: выбор режима (Локально / SSH), проверка окружения и запуск CLI video-gen."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, cast

import flet as ft

from video_gen_gui.remote import test_ssh_connection
from video_gen_gui.state import AppState, JobStatus
from video_gen_gui.views.components import (
    pick_remote_path_dialog,
    section_card,
    show_toast,
    status_chip,
)


class DashboardView:
    """Главный экран мониторинга, SSH-подключения и запуска CLI конвейера."""

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        file_picker: ft.FilePicker,
        clipboard: ft.Clipboard,
        on_start_pipeline: Callable[[], None],
        on_stop_process: Callable[[], None],
        on_install_deps: Callable[[bool], None],
        on_probe_env: Callable[[], None],
        on_reload_config: Callable[[], None],
        on_mode_changed: Callable[[], None] | None = None,
        on_badge_changed: Callable[[], None] | None = None,
    ) -> None:
        self.page = page
        self.state = state
        self.file_picker = file_picker
        self.clipboard = clipboard
        self.on_start_pipeline = on_start_pipeline
        self.on_stop_process = on_stop_process
        self.on_install_deps = on_install_deps
        self.on_probe_env = on_probe_env
        self.on_reload_config = on_reload_config
        self.on_mode_changed = on_mode_changed
        self.on_badge_changed = on_badge_changed

        self.env_chips_row = ft.Row(wrap=True, spacing=8, run_spacing=8)

        self.mode_selector = ft.SegmentedButton(
            selected=[self.state.settings.execution_mode],
            allow_multiple_selection=False,
            show_selected_icon=False,
            on_change=self._on_mode_change,
            segments=[
                ft.Segment(value="local", label=ft.Text("Локально")),
                ft.Segment(value="ssh", label=ft.Text("Удалённый сервер (SSH)")),
            ],
        )

        self.vg_dir_tf = ft.TextField(
            label="Папка проекта video-gen",
            value=self.state.settings.video_gen_dir,
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_submit=self._handle_vg_dir_manual,
            on_blur=self._handle_vg_dir_manual,
        )
        self.cfg_path_tf = ft.TextField(
            label="Путь к файлу конфигурации (config.json)",
            value=str(self.state.settings.resolved_config_path()),
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_submit=self._handle_cfg_manual,
            on_blur=self._handle_cfg_manual,
        )
        self.local_box = ft.Column(
            spacing=10,
            visible=not self.state.settings.is_remote,
            controls=[
                ft.Row(
                    spacing=8,
                    controls=[
                        self.vg_dir_tf,
                        ft.IconButton(
                            icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                            tooltip="Выбрать папку video-gen",
                            on_click=self._pick_vg_dir,
                        ),
                    ],
                ),
                ft.Row(
                    spacing=8,
                    controls=[
                        self.cfg_path_tf,
                        ft.IconButton(
                            icon=ft.Icons.FILE_OPEN_OUTLINED,
                            tooltip="Выбрать файл config.json / config.toml",
                            on_click=self._pick_cfg_file,
                        ),
                    ],
                ),
            ],
        )

        s = self.state.settings
        self.ssh_host_tf = ft.TextField(
            label="Хост / IP или user@host",
            value=s.ssh_host,
            hint_text="192.168.1.50 или user@gpu-server",
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda _: self._save_ssh_fields(refresh_badge=True),
            on_submit=lambda _: self._commit_ssh_fields(),
            on_blur=lambda _: self._commit_ssh_fields(),
        )
        self.ssh_port_tf = ft.TextField(
            label="Порт",
            value=str(s.ssh_port),
            dense=True,
            width=100,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda _: self._save_ssh_fields(refresh_badge=False),
            on_submit=lambda _: self._commit_ssh_fields(),
            on_blur=lambda _: self._commit_ssh_fields(),
        )
        self.ssh_user_tf = ft.TextField(
            label="Пользователь SSH (опционально)",
            value=s.ssh_user,
            hint_text="ubuntu",
            dense=True,
            width=220,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda _: self._save_ssh_fields(refresh_badge=True),
            on_submit=lambda _: self._commit_ssh_fields(),
            on_blur=lambda _: self._commit_ssh_fields(),
        )
        self.ssh_key_tf = ft.TextField(
            label="Путь к приватному ключу (опционально)",
            value=s.ssh_key_path,
            hint_text="~/.ssh/id_ed25519",
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda _: self._save_ssh_fields(refresh_badge=False),
            on_submit=lambda _: self._commit_ssh_fields(),
            on_blur=lambda _: self._commit_ssh_fields(),
        )
        self.remote_vg_dir_tf = ft.TextField(
            label="Удалённая директория video-gen",
            value=s.remote_video_gen_dir,
            hint_text="~/projects/video-gen",
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda _: self._save_ssh_fields(refresh_badge=False),
            on_submit=lambda _: self._commit_ssh_fields(),
            on_blur=lambda _: self._commit_ssh_fields(),
        )
        self.remote_cfg_path_tf = ft.TextField(
            label="Удалённый config.json",
            value=s.remote_config_path,
            hint_text="config.json (по умолчанию внутри удалённой папки video-gen)",
            dense=True,
            expand=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
            on_change=lambda _: self._save_ssh_fields(refresh_badge=False),
            on_submit=lambda _: self._save_ssh_fields(refresh_badge=False),
            on_blur=lambda _: self._save_ssh_fields(refresh_badge=False),
        )
        self.test_ssh_btn = ft.OutlinedButton(
            content="Проверить SSH",
            icon=ft.Icons.WIFI_TETHERING,
            on_click=self._test_ssh_click,
        )
        self.load_remote_cfg_btn = ft.OutlinedButton(
            content="Загрузить конфиг с сервера",
            icon=ft.Icons.CLOUD_DOWNLOAD_OUTLINED,
            on_click=lambda _: self.on_reload_config(),
        )
        self.ssh_box = ft.Column(
            spacing=10,
            visible=self.state.settings.is_remote,
            controls=[
                ft.Row(
                    spacing=10,
                    controls=[
                        self.ssh_host_tf,
                        self.ssh_port_tf,
                        self.ssh_user_tf,
                    ],
                ),
                ft.Row(
                    spacing=8,
                    controls=[
                        self.ssh_key_tf,
                        ft.IconButton(
                            icon=ft.Icons.KEY_OUTLINED,
                            tooltip="Выбрать приватный SSH-ключ",
                            on_click=self._pick_ssh_key,
                        ),
                    ],
                ),
                ft.Row(
                    spacing=8,
                    controls=[
                        self.remote_vg_dir_tf,
                        ft.IconButton(
                            icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                            tooltip="Обзор папок на удалённом сервере",
                            on_click=self._pick_remote_vg_dir,
                        ),
                    ],
                ),
                ft.Row(
                    spacing=8,
                    controls=[
                        self.remote_cfg_path_tf,
                        ft.IconButton(
                            icon=ft.Icons.FILE_OPEN_OUTLINED,
                            tooltip="Выбрать config.json на удалённом сервере",
                            on_click=self._pick_remote_cfg_file,
                        ),
                    ],
                ),
                ft.Row(
                    wrap=True,
                    spacing=10,
                    controls=[
                        self.test_ssh_btn,
                        self.load_remote_cfg_btn,
                    ],
                ),
            ],
        )

        self.start_btn = ft.FilledButton(
            content="Запустить video-gen CLI",
            icon=ft.Icons.PLAY_ARROW,
            on_click=lambda _: self.on_start_pipeline(),
        )
        self.stop_btn = ft.Button(
            content="Остановить",
            icon=ft.Icons.STOP,
            bgcolor=ft.Colors.RED_800,
            color=ft.Colors.WHITE,
            disabled=True,
            on_click=lambda _: self.on_stop_process(),
        )
        self.sync_btn = ft.FilledTonalButton(
            content="Установить зависимости (uv sync)",
            icon=ft.Icons.DOWNLOAD,
            on_click=lambda _: self.on_install_deps(False),
        )
        self.upgrade_btn = ft.OutlinedButton(
            content="Обновить (uv sync --upgrade)",
            icon=ft.Icons.SYSTEM_UPDATE_ALT,
            on_click=lambda _: self.on_install_deps(True),
        )
        self.status_banner_text = ft.Text(
            "Готов к запуску",
            size=12,
            weight=ft.FontWeight.W_500,
            font_family="monospace",
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.running_ring = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False)

        self.kpi_total = ft.Text(
            "0", size=20, weight=ft.FontWeight.W_700, font_family="monospace"
        )
        self.kpi_ok = ft.Text(
            "0",
            size=20,
            weight=ft.FontWeight.W_700,
            font_family="monospace",
            color=ft.Colors.GREEN_400,
        )
        self.kpi_failed = ft.Text(
            "0",
            size=20,
            weight=ft.FontWeight.W_700,
            font_family="monospace",
            color=ft.Colors.AMBER_400,
        )
        self.kpi_broken = ft.Text(
            "0",
            size=20,
            weight=ft.FontWeight.W_700,
            font_family="monospace",
            color=ft.Colors.RED_400,
        )

        self.jobs_column = ft.Column(spacing=8)
        self.log_list = ft.ListView(
            height=290,
            spacing=3,
            auto_scroll=True,
            padding=ft.Padding.all(10),
        )

    def _on_mode_change(self, e: ft.Event[ft.SegmentedButton]) -> None:
        sel = e.control.selected
        mode = cast(Literal["local", "ssh"], sel[0] if sel else "local")
        if mode == self.state.settings.execution_mode:
            return
        self.state.settings.execution_mode = mode
        self.state.settings.save()
        self.local_box.visible = mode == "local"
        self.ssh_box.visible = mode == "ssh"
        if self.on_mode_changed is not None:
            self.on_mode_changed()
        else:
            self.on_probe_env()
        self.page.update()

    def _save_ssh_fields(self, *, refresh_badge: bool) -> None:
        s = self.state.settings
        s.ssh_host = (self.ssh_host_tf.value or "").strip()
        s.ssh_user = (self.ssh_user_tf.value or "").strip()
        s.ssh_key_path = (self.ssh_key_tf.value or "").strip()
        s.remote_video_gen_dir = (
            self.remote_vg_dir_tf.value or ""
        ).strip() or "~/projects/video-gen"
        s.remote_config_path = (self.remote_cfg_path_tf.value or "").strip()

        raw_port = (self.ssh_port_tf.value or "").strip()
        if raw_port.isdigit():
            port_val = int(raw_port)
            if 1 <= port_val <= 65535:
                s.ssh_port = port_val
                self.ssh_port_tf.error = None
            else:
                self.ssh_port_tf.error = "1..65535"
        elif raw_port:
            self.ssh_port_tf.error = "1..65535"

        s.save()
        if refresh_badge and self.on_badge_changed is not None:
            self.on_badge_changed()

    def _commit_ssh_fields(self) -> None:
        self._save_ssh_fields(refresh_badge=True)
        if self.state.settings.is_remote and self.state.settings.ssh_destination():
            if self.on_mode_changed is not None:
                self.on_mode_changed()
            else:
                self.on_probe_env()

    async def _test_ssh_click(self, _: ft.Event[ft.OutlinedButton]) -> None:
        self._save_ssh_fields(refresh_badge=True)
        self.test_ssh_btn.disabled = True
        self.page.update()
        try:
            ok, msg = await test_ssh_connection(self.state.settings)
            self.state.add_log("INFO" if ok else "ERROR", msg)
            self.refresh_logs()
            show_toast(self.page, msg, error=not ok)
            if ok:
                self.on_probe_env()
        finally:
            self.test_ssh_btn.disabled = False
            self.page.update()

    async def _pick_ssh_key(self, _: ft.Event[ft.IconButton]) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Выберите приватный SSH-ключ",
            allow_multiple=False,
        )
        if files and files[0].path:
            self.ssh_key_tf.value = files[0].path
            self._commit_ssh_fields()
            self.page.update()

    def _apply_remote_vg_dir(self, picked: str) -> None:
        self.remote_vg_dir_tf.value = picked
        self._save_ssh_fields(refresh_badge=False)
        self.on_probe_env()

    async def _pick_remote_vg_dir(self, _: ft.Event[ft.IconButton]) -> None:
        self._save_ssh_fields(refresh_badge=False)
        await pick_remote_path_dialog(
            self.page,
            self.state.settings,
            title="Удалённая директория video-gen",
            pick_mode="dir",
            initial_dir=self.state.settings.resolved_remote_video_gen_dir(),
            relativize_to_vg=False,
            on_picked=self._apply_remote_vg_dir,
        )

    def _apply_remote_cfg_file(self, picked: str) -> None:
        self.remote_cfg_path_tf.value = picked
        self._save_ssh_fields(refresh_badge=False)
        self.on_reload_config()

    async def _pick_remote_cfg_file(self, _: ft.Event[ft.IconButton]) -> None:
        self._save_ssh_fields(refresh_badge=False)
        await pick_remote_path_dialog(
            self.page,
            self.state.settings,
            title="Удалённый config.json",
            pick_mode="file",
            allowed_extensions=["json", "toml"],
            initial_dir=self.state.settings.resolved_remote_video_gen_dir(),
            relativize_to_vg=True,
            on_picked=self._apply_remote_cfg_file,
        )

    def _handle_vg_dir_manual(self, e: ft.Event[ft.TextField]) -> None:
        val = (e.control.value or "").strip()
        if val and val != self.state.settings.video_gen_dir:
            self.state.settings.video_gen_dir = val
            self.state.settings.config_path = str(
                self.state.settings.resolved_video_gen_dir() / "config.json"
            )
            self.cfg_path_tf.value = self.state.settings.config_path
            self.state.settings.save()
            self.on_probe_env()
            self.on_reload_config()

    def _handle_cfg_manual(self, e: ft.Event[ft.TextField]) -> None:
        val = (e.control.value or "").strip()
        if val and val != self.state.settings.config_path:
            self.state.settings.config_path = val
            self.state.settings.save()
            self.on_reload_config()

    async def _pick_vg_dir(self, _: ft.Event[ft.IconButton]) -> None:
        chosen = await self.file_picker.get_directory_path(
            dialog_title="Выберите директорию проекта video-gen",
            initial_directory=str(self.state.settings.resolved_video_gen_dir()),
        )
        if chosen:
            self.vg_dir_tf.value = chosen
            self.state.settings.video_gen_dir = chosen
            self.state.settings.config_path = str(
                self.state.settings.resolved_video_gen_dir() / "config.json"
            )
            self.cfg_path_tf.value = self.state.settings.config_path
            self.state.settings.save()
            self.on_probe_env()
            self.on_reload_config()

    async def _pick_cfg_file(self, _: ft.Event[ft.IconButton]) -> None:
        files = await self.file_picker.pick_files(
            dialog_title="Выберите config.json или config.toml",
            initial_directory=str(self.state.settings.resolved_video_gen_dir()),
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json", "toml"],
            allow_multiple=False,
        )
        if files and files[0].path:
            picked = files[0].path
            self.cfg_path_tf.value = picked
            self.state.settings.config_path = picked
            self.state.settings.save()
            self.on_reload_config()

    async def _copy_logs(self, _: ft.Event[ft.IconButton]) -> None:
        lines = [f"[{x.timestamp}] [{x.level}] {x.message}" for x in self.state.logs]
        await self.clipboard.set("\n".join(lines))
        show_toast(self.page, "Журнал скопирован в буфер обмена")

    def _clear_logs(self, _: ft.Event[ft.IconButton]) -> None:
        self.state.logs.clear()
        self.refresh_logs()

    def _kpi_box(self, title: str, value_ctrl: ft.Text) -> ft.Container:
        return ft.Container(
            expand=True,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(4),
            padding=ft.Padding.symmetric(horizontal=14, vertical=10),
            content=ft.Column(
                spacing=4,
                controls=[
                    ft.Text(
                        title.upper(),
                        size=11,
                        weight=ft.FontWeight.W_500,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    value_ctrl,
                ],
            ),
        )

    def _build_job_card(self, job: JobStatus) -> ft.Container:
        border_col = (
            ft.Colors.RED_400
            if job.broken
            else (ft.Colors.GREEN_400 if job.finished else ft.Colors.OUTLINE_VARIANT)
        )
        clips_badge = (
            f"Клипы: {job.clips_done}/{job.clips_total}" if job.clips_total else "Клипы: —"
        )
        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, border_col),
            border_radius=ft.BorderRadius.all(4),
            padding=ft.Padding.all(12),
            content=ft.Column(
                spacing=8,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        controls=[
                            ft.Row(
                                spacing=8,
                                expand=True,
                                controls=[
                                    ft.Icon(
                                        ft.Icons.ERROR_OUTLINE
                                        if job.broken
                                        else (
                                            ft.Icons.CHECK_CIRCLE_OUTLINE
                                            if job.finished
                                            else ft.Icons.MOVIE_CREATION_OUTLINED
                                        ),
                                        size=16,
                                        color=border_col,
                                    ),
                                    ft.Text(
                                        job.name,
                                        weight=ft.FontWeight.W_600,
                                        size=13,
                                        font_family="monospace",
                                        expand=True,
                                    ),
                                ],
                            ),
                            ft.Text(job.stage, size=12, color=ft.Colors.PRIMARY),
                            ft.Container(
                                bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                                border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                                border_radius=ft.BorderRadius.all(3),
                                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                                content=ft.Text(
                                    clips_badge, size=11, font_family="monospace"
                                ),
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.Text("Whisper:", size=11, width=60, font_family="monospace"),
                            ft.ProgressBar(
                                value=max(0.0, min(1.0, job.transcribe_pct / 100.0)),
                                expand=True,
                            ),
                            ft.Text(
                                f"{job.transcribe_pct:.0f}%",
                                size=11,
                                width=42,
                                font_family="monospace",
                            ),
                        ],
                    ),
                    ft.Row(
                        spacing=12,
                        controls=[
                            ft.Text("FFmpeg:", size=11, width=60, font_family="monospace"),
                            ft.ProgressBar(
                                value=max(0.0, min(1.0, job.render_pct / 100.0)),
                                color=ft.Colors.GREEN_400,
                                expand=True,
                            ),
                            ft.Text(
                                f"{job.render_pct:.0f}%",
                                size=11,
                                width=42,
                                font_family="monospace",
                            ),
                        ],
                    ),
                ],
            ),
        )

    def refresh_env(self) -> None:
        st = self.state.env_status
        if st is None:
            self.env_chips_row.controls = [status_chip("Проверка окружения...", None)]
            return

        chips: list[ft.Control] = []
        if st.is_remote:
            chips.append(status_chip("SSH", st.ssh_reachable, st.target_label))

        chips.extend(
            [
                status_chip(
                    "video-gen",
                    st.video_gen_exists and st.pyproject_exists,
                    f"Python {st.python_version_pin}" if st.pyproject_exists else "не найден",
                ),
                status_chip(
                    "uv",
                    st.uv_path is not None,
                    st.uv_version.replace("uv ", "") if st.uv_version else "нет в PATH",
                ),
                status_chip(
                    ".venv зависимости",
                    st.venv_ready and st.cli_binary_ready,
                    "установлены" if st.cli_binary_ready else "требуется uv sync",
                ),
                status_chip(
                    "FFmpeg",
                    st.ffmpeg_path is not None,
                    "найден" if st.ffmpeg_path else "не найден",
                ),
                status_chip(
                    "NVENC GPU",
                    st.nvenc_available,
                    "доступен" if st.nvenc_available else "только CPU",
                ),
            ]
        )
        self.env_chips_row.controls = chips

    def refresh_progress(self) -> None:
        busy = self.state.runner.is_running or self.state.task_kind is not None
        self.start_btn.disabled = busy
        self.sync_btn.disabled = busy
        self.upgrade_btn.disabled = busy
        self.stop_btn.disabled = not self.state.runner.is_running
        self.running_ring.visible = busy

        if self.state.task_kind == "sync":
            self.status_banner_text.value = "Синхронизация зависимостей (uv sync)..."
        elif self.state.task_kind == "pipeline":
            self.status_banner_text.value = "Выполняется CLI конвейер video-gen..."
        else:
            self.status_banner_text.value = "Готов к запуску"

        self.kpi_total.value = str(self.state.total_jobs or len(self.state.jobs))
        self.kpi_ok.value = str(self.state.stats["ok"])
        self.kpi_failed.value = str(self.state.stats["failed"])
        self.kpi_broken.value = str(self.state.stats["broken_videos"])

        if not self.state.jobs:
            self.jobs_column.controls = [
                ft.Text(
                    "Очередь видео появится здесь сразу после запуска CLI конвейера.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
        else:
            self.jobs_column.controls = [self._build_job_card(j) for j in self.state.jobs]

    def refresh_logs(self) -> None:
        flt = self.state.log_filter
        items: list[ft.Control] = []
        for entry in self.state.logs:
            if flt != "ALL" and entry.level != flt:
                continue
            color = ft.Colors.ON_SURFACE
            if entry.level == "ERROR":
                color = ft.Colors.RED_300
            elif entry.level in ("WARNING", "WARN"):
                color = ft.Colors.AMBER_300
            elif entry.message.startswith("$ "):
                color = ft.Colors.CYAN_300

            items.append(
                ft.Text(
                    f"[{entry.timestamp}] [{entry.level}] {entry.message}",
                    font_family="monospace",
                    size=12,
                    color=color,
                    selectable=True,
                )
            )
        self.log_list.controls = items

    def refresh_all(self) -> None:
        s = self.state.settings
        self.mode_selector.selected = [s.execution_mode]
        self.local_box.visible = not s.is_remote
        self.ssh_box.visible = s.is_remote
        self.vg_dir_tf.value = s.video_gen_dir
        self.cfg_path_tf.value = str(s.resolved_config_path())
        self.ssh_host_tf.value = s.ssh_host
        self.ssh_port_tf.value = str(s.ssh_port)
        self.ssh_user_tf.value = s.ssh_user
        self.ssh_key_tf.value = s.ssh_key_path
        self.remote_vg_dir_tf.value = s.remote_video_gen_dir
        self.remote_cfg_path_tf.value = s.remote_config_path
        self.refresh_env()
        self.refresh_progress()
        self.refresh_logs()

    def build(self) -> ft.Control:
        self.refresh_all()

        def _on_auto_sync_toggle(e: ft.Event[ft.Switch]) -> None:
            self.state.settings.auto_sync_before_run = bool(e.control.value)
            self.state.settings.save()

        def _on_filter_change(e: ft.Event[ft.SegmentedButton]) -> None:
            sel = e.control.selected
            self.state.log_filter = sel[0] if sel else "ALL"
            self.refresh_logs()

        return ft.Column(
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "Окружение и Подключение video-gen",
                    ft.Icons.TUNE,
                    subtitle="Локальный или удалённый SSH-запуск CLI, проверка .venv и синхронизация зависимостей",
                    trailing=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[
                            self.mode_selector,
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                tooltip="Перепроверить окружение",
                                on_click=lambda _: self.on_probe_env(),
                            ),
                        ],
                    ),
                    controls=[
                        self.env_chips_row,
                        self.local_box,
                        self.ssh_box,
                        ft.Row(
                            wrap=True,
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                self.sync_btn,
                                self.upgrade_btn,
                                ft.Switch(
                                    label="Прогонять зависимости перед запуском (uv sync)",
                                    value=self.state.settings.auto_sync_before_run,
                                    on_change=_on_auto_sync_toggle,
                                ),
                            ],
                        ),
                    ],
                ),
                section_card(
                    "Запуск и Мониторинг Конвейера",
                    ft.Icons.PLAY_CIRCLE_OUTLINE,
                    subtitle="Выполняет `video-gen <config.json> --json-progress` и отслеживает этапы обработки",
                    trailing=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[self.running_ring, self.status_banner_text],
                    ),
                    controls=[
                        ft.Row(
                            spacing=10,
                            controls=[
                                self.start_btn,
                                self.stop_btn,
                            ],
                        ),
                        ft.Row(
                            spacing=10,
                            controls=[
                                self._kpi_box("Видео в очереди", self.kpi_total),
                                self._kpi_box("Клипов готово", self.kpi_ok),
                                self._kpi_box("Ошибки рендера", self.kpi_failed),
                                self._kpi_box("Проблемные видео", self.kpi_broken),
                            ],
                        ),
                        ft.Text("Очередь видео", weight=ft.FontWeight.W_600, size=13),
                        self.jobs_column,
                    ],
                ),
                section_card(
                    "Консоль вывода CLI",
                    ft.Icons.TERMINAL,
                    trailing=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[
                            ft.SegmentedButton(
                                selected=[self.state.log_filter],
                                allow_multiple_selection=False,
                                show_selected_icon=False,
                                on_change=_on_filter_change,
                                segments=[
                                    ft.Segment(value="ALL", label=ft.Text("Все")),
                                    ft.Segment(value="INFO", label=ft.Text("INFO")),
                                    ft.Segment(value="WARNING", label=ft.Text("WARN")),
                                    ft.Segment(value="ERROR", label=ft.Text("ERR")),
                                ],
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CONTENT_COPY,
                                tooltip="Копировать вывод",
                                on_click=self._copy_logs,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.DELETE_SWEEP_OUTLINED,
                                tooltip="Очистить консоль",
                                on_click=self._clear_logs,
                            ),
                        ],
                    ),
                    controls=[
                        ft.Container(
                            bgcolor=ft.Colors.BLACK,
                            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                            border_radius=ft.BorderRadius.all(4),
                            content=self.log_list,
                        )
                    ],
                ),
            ],
        )
