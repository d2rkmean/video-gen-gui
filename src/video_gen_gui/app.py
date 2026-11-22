"""Главное окно Flet 1.1 приложения Video-Gen."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import flet as ft
from pydantic import ValidationError

from video_gen_gui.models import GuiSettings, VideoGenConfig
from video_gen_gui.remote import check_remote_preflight, read_remote_text, write_remote_text
from video_gen_gui.runner import probe_environment
from video_gen_gui.state import AppState
from video_gen_gui.views.components import show_toast
from video_gen_gui.views.dashboard import DashboardView
from video_gen_gui.views.database import DatabaseView
from video_gen_gui.views.gallery import GalleryView
from video_gen_gui.views.pipeline import PipelineView
from video_gen_gui.views.render import RenderView
from video_gen_gui.views.sources import SourcesView
from video_gen_gui.views.subtitles import SubtitlesView


def _build_strict_theme() -> ft.Theme:
    """Формирует строгую утилитарную тему с прямоугольными скруглениями (radius=4)."""
    sharp_shape = ft.RoundedRectangleBorder(radius=4)
    btn_style = ft.ButtonStyle(shape=sharp_shape)
    return ft.Theme(
        color_scheme_seed=ft.Colors.BLUE_GREY,
        surfaces=ft.ThemeSurfaces.NEUTRAL,
        visual_density=ft.VisualDensity.COMPACT,
        button_theme=ft.ButtonTheme(style=btn_style),
        filled_button_theme=ft.FilledButtonTheme(style=btn_style),
        outlined_button_theme=ft.OutlinedButtonTheme(style=btn_style),
        text_button_theme=ft.TextButtonTheme(style=btn_style),
        segmented_button_theme=ft.SegmentedButtonTheme(style=btn_style),
        dialog_theme=ft.DialogTheme(shape=sharp_shape),
    )


def _parse_and_cache_remote_config(
    settings: GuiSettings,
    raw_text: str,
    remote_path: str,
) -> VideoGenConfig:
    cache_dir = settings.remote_cache_dir()
    if remote_path.lower().endswith(".toml"):
        tmp_toml = cache_dir / "remote_import.toml"
        tmp_toml.write_text(raw_text, encoding="utf-8")
        try:
            cfg = VideoGenConfig.load_file(tmp_toml)
        finally:
            tmp_toml.unlink(missing_ok=True)
    else:
        cfg = VideoGenConfig.model_validate(json.loads(raw_text))
    cfg.save_json(cache_dir / "config.json")
    return cfg


class VideoGenStudioApp:
    """Контроллер верхнего уровня для Flet 1.1 приложения."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self.state = AppState()

        # В Flet 1.1 сервисы живут пока на них есть ссылка в Python
        self.file_picker = ft.FilePicker()
        self.clipboard = ft.Clipboard()

        self._selected_tab = 0
        self._last_ui_flush = 0.0
        self._remote_config_fetched = (
            self.state.settings.is_remote
            and (self.state.settings.remote_cache_dir() / "config.json").is_file()
        )

        self.mode_badge_text = ft.Text(
            "LOCAL",
            size=11,
            weight=ft.FontWeight.W_600,
            font_family="monospace",
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.mode_badge = ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(3),
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            content=self.mode_badge_text,
        )

        self.dashboard_view = DashboardView(
            page=self.page,
            state=self.state,
            file_picker=self.file_picker,
            clipboard=self.clipboard,
            on_start_pipeline=self.trigger_start_pipeline,
            on_stop_process=self.trigger_stop_process,
            on_install_deps=self.trigger_install_deps,
            on_probe_env=self.trigger_probe_env,
            on_reload_config=self.reload_config_from_disk,
            on_mode_changed=self.on_execution_mode_changed,
            on_badge_changed=self.on_ssh_badge_changed,
        )
        self.sources_view = SourcesView(self.page, self.state, self.file_picker)
        self.pipeline_view = PipelineView(self.page, self.state, self.file_picker)
        self.render_view = RenderView(self.page, self.state, self.file_picker)
        self.subtitles_view = SubtitlesView(self.page, self.state, self.file_picker)
        self.database_view = DatabaseView(self.page, self.state)
        self.gallery_view = GalleryView(self.page, self.state, self.clipboard)

        self.body_container = ft.Container(
            expand=True,
            padding=ft.Padding.all(20),
        )

    def refresh_mode_badge(self) -> None:
        """Обновляет индикатор режима выполнения (LOCAL / SSH) в заголовке окна."""
        if self.state.settings.is_remote:
            dest = self.state.settings.ssh_destination() or "не настроен"
            self.mode_badge_text.value = f"SSH: {dest}"
            self.mode_badge_text.color = ft.Colors.CYAN_300
            self.mode_badge.border = ft.Border.all(
                1, ft.Colors.with_opacity(0.45, ft.Colors.CYAN_400)
            )
            self.mode_badge.bgcolor = ft.Colors.with_opacity(0.08, ft.Colors.CYAN_400)
        else:
            self.mode_badge_text.value = "LOCAL"
            self.mode_badge_text.color = ft.Colors.ON_SURFACE_VARIANT
            self.mode_badge.border = ft.Border.all(1, ft.Colors.OUTLINE_VARIANT)
            self.mode_badge.bgcolor = ft.Colors.SURFACE_CONTAINER_HIGHEST

    def on_ssh_badge_changed(self) -> None:
        self.refresh_mode_badge()
        self.page.update()

    def on_execution_mode_changed(self) -> None:
        self._remote_config_fetched = False
        self.subtitles_view.reset_remote_sync()
        self.refresh_mode_badge()
        self.trigger_probe_env()
        if self.state.settings.is_remote and self.state.settings.ssh_destination():
            cached_cfg = self.state.settings.remote_cache_dir() / "config.json"
            if not cached_cfg.is_file():
                self.page.run_task(self._fetch_remote_config_silently_coro)
            else:
                self._remote_config_fetched = True
        self.page.update()

    def setup_page(self) -> None:
        self.page.title = "VIDEO-GEN — Управление конвейером"
        self.page.padding = 0
        self.page.theme_mode = (
            ft.ThemeMode.DARK if self.state.settings.theme_mode == "dark" else ft.ThemeMode.LIGHT
        )
        self.page.theme = _build_strict_theme()
        self.page.dark_theme = _build_strict_theme()
        self.page.window.width = 1340
        self.page.window.height = 880
        self.page.window.min_width = 980
        self.page.window.min_height = 640

        self.refresh_mode_badge()

        self.theme_btn = ft.IconButton(
            icon=ft.Icons.LIGHT_MODE_OUTLINED
            if self.page.theme_mode == ft.ThemeMode.DARK
            else ft.Icons.DARK_MODE_OUTLINED,
            tooltip="Переключить тему",
            on_click=self._toggle_theme,
        )

        self.page.appbar = ft.AppBar(
            leading=ft.Container(
                padding=ft.Padding.only(left=14),
                alignment=ft.Alignment.CENTER,
                content=ft.Icon(
                    ft.Icons.TERMINAL,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                    size=20,
                ),
            ),
            title=ft.Row(
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Text(
                        "VIDEO-GEN",
                        weight=ft.FontWeight.W_700,
                        size=15,
                        font_family="monospace",
                    ),
                    self.mode_badge,
                ],
            ),
            actions=[
                ft.TextButton(
                    content="Импорт .json/.toml",
                    icon=ft.Icons.UPLOAD_FILE_OUTLINED,
                    on_click=self._import_config_dialog,
                ),
                ft.TextButton(
                    content="JSON превью",
                    icon=ft.Icons.DATA_OBJECT,
                    on_click=self._show_json_preview,
                ),
                ft.FilledTonalButton(
                    content="Сохранить config.json",
                    icon=ft.Icons.SAVE_OUTLINED,
                    on_click=lambda _: self.page.run_task(
                        self.async_validate_and_save_config, notify=True
                    ),
                ),
                self.theme_btn,
                ft.Container(width=10),
            ],
        )

        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=96,
            scrollable=True,
            group_alignment=-0.95,
            on_change=self._on_nav_change,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.DASHBOARD_OUTLINED,
                    selected_icon=ft.Icons.DASHBOARD,
                    label="Дашборд",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.VIDEO_COLLECTION_OUTLINED,
                    selected_icon=ft.Icons.VIDEO_COLLECTION,
                    label="Источники",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.ACCOUNT_TREE_OUTLINED,
                    selected_icon=ft.Icons.ACCOUNT_TREE,
                    label="Конвейер",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.TUNE_OUTLINED,
                    selected_icon=ft.Icons.TUNE,
                    label="Рендер",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SUBTITLES_OUTLINED,
                    selected_icon=ft.Icons.SUBTITLES,
                    label="Субтитры",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.STORAGE_OUTLINED,
                    selected_icon=ft.Icons.STORAGE,
                    label="База данных",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.VIDEO_LIBRARY_OUTLINED,
                    selected_icon=ft.Icons.VIDEO_LIBRARY,
                    label="Клипы",
                ),
            ],
        )

        self._render_active_tab()

        self.page.add(
            ft.SafeArea(
                expand=True,
                content=ft.Row(
                    expand=True,
                    spacing=0,
                    controls=[
                        self.rail,
                        ft.VerticalDivider(width=1, thickness=1),
                        self.body_container,
                    ],
                ),
            )
        )

        self.trigger_probe_env()
        if (
            self.state.settings.is_remote
            and not self._remote_config_fetched
            and self.state.settings.ssh_destination()
        ):
            self.page.run_task(self._fetch_remote_config_silently_coro)

    def _toggle_theme(self, _: ft.Event[ft.IconButton]) -> None:
        if self.page.theme_mode == ft.ThemeMode.DARK:
            self.page.theme_mode = ft.ThemeMode.LIGHT
            self.state.settings.theme_mode = "light"
            self.theme_btn.icon = ft.Icons.DARK_MODE_OUTLINED
        else:
            self.page.theme_mode = ft.ThemeMode.DARK
            self.state.settings.theme_mode = "dark"
            self.theme_btn.icon = ft.Icons.LIGHT_MODE_OUTLINED
        self.state.settings.save()

    def _on_nav_change(self, e: ft.Event[ft.NavigationRail]) -> None:
        if e.control.selected_index is not None:
            self._selected_tab = e.control.selected_index
            self._render_active_tab()

    def _render_active_tab(self) -> None:
        builders = [
            self.dashboard_view.build,
            self.sources_view.build,
            self.pipeline_view.build,
            self.render_view.build,
            self.subtitles_view.build,
            self.database_view.build,
            self.gallery_view.build,
        ]
        idx = max(0, min(len(builders) - 1, self._selected_tab))
        self.body_container.content = builders[idx]()

    def validate_and_save_config(self, *, notify: bool = False) -> Path | None:
        """Проверяет конфиг через Pydantic и сохраняет локальную копию в JSON."""
        try:
            validated = VideoGenConfig.model_validate(self.state.config.model_dump())
            self.state.config = validated
        except ValidationError as exc:
            first_err = exc.errors()[0]
            loc = ".".join(str(x) for x in first_err.get("loc", ()))
            msg = first_err.get("msg", str(exc))
            show_toast(self.page, f"Ошибка валидации ({loc}): {msg}", error=True)
            return None

        if self.state.settings.is_remote:
            target = self.state.settings.remote_cache_dir() / "config.json"
        else:
            target = self.state.settings.resolved_config_path()
            if target.suffix.lower() == ".toml":
                # Всегда сохраняем в .json, так как актуальный CLI video-gen читает JSON
                target = target.with_suffix(".json")
                self.state.settings.config_path = str(target)
                self.state.settings.save()
                self.dashboard_view.cfg_path_tf.value = str(target)

        try:
            saved_path = self.state.config.save_json(target)
        except OSError as exc:
            show_toast(self.page, f"Не удалось записать {target}: {exc}", error=True)
            return None

        if notify:
            show_toast(self.page, f"Конфигурация сохранена: {saved_path}")
        return saved_path

    async def async_validate_and_save_config(self, *, notify: bool = False) -> Path | None:
        """Валидирует, сохраняет локально/в кэш и при SSH-режиме загружает config.json на сервер."""
        saved_path = self.validate_and_save_config(notify=False)
        if saved_path is None:
            return None

        if self.state.settings.is_remote:
            remote_cfg = self.state.settings.resolved_remote_config_path()
            try:
                await write_remote_text(
                    self.state.settings,
                    remote_cfg,
                    self.state.config.to_json_text(),
                )
            except (ValueError, TimeoutError, RuntimeError, OSError) as exc:
                show_toast(
                    self.page,
                    f"Не удалось сохранить конфиг на сервер ({remote_cfg}): {exc}",
                    error=True,
                )
                return None

            if notify:
                show_toast(
                    self.page,
                    f"Конфигурация сохранена на сервере: {remote_cfg}",
                )
            return saved_path

        if notify:
            show_toast(self.page, f"Конфигурация сохранена: {saved_path}")
        return saved_path

    def reload_config_from_disk(self) -> None:
        if self.state.settings.is_remote:
            self.page.run_task(self._reload_remote_config_coro)
            return

        cfg_path = self.state.settings.resolved_config_path()
        if not cfg_path.is_file():
            return
        try:
            self.state.config = VideoGenConfig.load_file(cfg_path)
            self._render_active_tab()
            show_toast(self.page, f"Загружен конфиг: {cfg_path.name}")
            self.page.update()
        except Exception as exc:
            show_toast(self.page, f"Ошибка чтения {cfg_path.name}: {exc}", error=True)

    async def _fetch_remote_config_silently_coro(self) -> None:
        settings = self.state.settings
        if not settings.is_remote or not settings.ssh_destination():
            return
        remote_cfg = settings.resolved_remote_config_path()
        try:
            raw_text = await read_remote_text(settings, remote_cfg)
            self.state.config = _parse_and_cache_remote_config(settings, raw_text, remote_cfg)
            self._remote_config_fetched = True
            self.state.add_log("INFO", f"Загружен удалённый конфиг: {remote_cfg}")
            self._render_active_tab()
        except FileNotFoundError:
            self.state.add_log("INFO", f"Удалённый конфиг ещё не создан: {remote_cfg}")
            self.dashboard_view.refresh_logs()
        except Exception as exc:
            self.state.add_log("WARNING", f"Не удалось прочитать удалённый конфиг ({remote_cfg}): {exc}")
            self.dashboard_view.refresh_logs()
        self.page.update()

    async def _reload_remote_config_coro(self) -> None:
        settings = self.state.settings
        remote_cfg = settings.resolved_remote_config_path()
        try:
            raw_text = await read_remote_text(settings, remote_cfg)
            self.state.config = _parse_and_cache_remote_config(settings, raw_text, remote_cfg)
            self._remote_config_fetched = True
            self.state.add_log("INFO", f"Загружен конфиг с сервера: {remote_cfg}")
            self._render_active_tab()
            show_toast(self.page, f"Загружен удалённый конфиг: {remote_cfg}")
        except Exception as exc:
            self.state.add_log("ERROR", f"Ошибка загрузки {remote_cfg}: {exc}")
            show_toast(self.page, f"Ошибка чтения удалённого конфига: {exc}", error=True)
        self.page.update()

    async def _import_config_dialog(self) -> None:
        vg_dir = self.state.settings.resolved_video_gen_dir()
        init_dir = str(vg_dir) if not self.state.settings.is_remote and vg_dir.is_dir() else None
        files = await self.file_picker.pick_files(
            dialog_title="Импорт config.json или старого config.toml",
            initial_directory=init_dir,
            file_type=ft.FilePickerFileType.CUSTOM,
            allowed_extensions=["json", "toml"],
            allow_multiple=False,
        )
        if not files or not files[0].path:
            return
        picked = Path(files[0].path)
        try:
            self.state.config = VideoGenConfig.load_file(picked)
            if self.state.settings.is_remote:
                self.state.config.save_json(self.state.settings.remote_cache_dir() / "config.json")
            elif picked.suffix.lower() == ".json":
                self.state.settings.config_path = str(picked)
                self.state.settings.save()
            self._render_active_tab()
            show_toast(
                self.page,
                f"Импортирован {picked.name}"
                + (" (будет сохранён как config.json)" if picked.suffix.lower() == ".toml" else ""),
            )
        except Exception as exc:
            show_toast(self.page, f"Не удалось импортировать {picked.name}: {exc}", error=True)

    def _show_json_preview(self) -> None:
        try:
            validated = VideoGenConfig.model_validate(self.state.config.model_dump())
            json_str = validated.to_json_text()
        except ValidationError as exc:
            show_toast(
                self.page,
                f"Исправьте ошибки перед экспортом: {exc.errors()[0]['msg']}",
                error=True,
            )
            return

        async def _copy_json() -> None:
            await self.clipboard.set(json_str)
            show_toast(self.page, "JSON скопирован в буфер обмена")

        dlg = ft.AlertDialog(
            title=ft.Text("Итоговый config.json для CLI video-gen"),
            content=ft.Container(
                width=680,
                height=460,
                content=ft.TextField(
                    value=json_str,
                    multiline=True,
                    read_only=True,
                    min_lines=18,
                    max_lines=22,
                    text_style=ft.TextStyle(font_family="monospace", size=12),
                ),
            ),
            actions=[
                ft.TextButton(content="Скопировать", on_click=_copy_json),
                ft.FilledButton(content="Закрыть", on_click=lambda _: self.page.pop_dialog()),
            ],
        )
        self.page.show_dialog(dlg)

    # ---- Фоновые задачи через Flet 1.1 page.run_task ----

    def trigger_probe_env(self) -> None:
        self.page.run_task(self._probe_env_coro)

    async def _probe_env_coro(self) -> None:
        settings = self.state.settings
        vg_dir = settings.resolved_video_gen_dir()
        self.state.env_status = await probe_environment(vg_dir, settings=settings)
        self.dashboard_view.refresh_env()
        self.page.update()

    def trigger_install_deps(self, upgrade: bool = False) -> None:
        if self.state.runner.is_running or self.state.task_kind is not None:
            show_toast(self.page, "Дождитесь завершения текущей задачи", error=True)
            return
        self.state.task_kind = "sync"
        self.dashboard_view.refresh_progress()
        self.page.update()
        self.page.run_task(self._install_deps_coro, upgrade)

    async def _install_deps_coro(self, upgrade: bool) -> None:
        settings = self.state.settings
        vg_dir = settings.resolved_video_gen_dir()
        self.state.task_kind = "sync"
        self.dashboard_view.refresh_progress()
        self.page.update()

        def _on_line(level: str, text: str) -> None:
            self.state.add_log(level, text)
            now = time.monotonic()
            if now - self._last_ui_flush >= 0.08:
                self._last_ui_flush = now
                self.dashboard_view.refresh_logs()
                self.page.update()

        try:
            rc = await self.state.runner.install_dependencies(
                vg_dir,
                _on_line,
                upgrade=upgrade,
                settings=settings,
            )
            self.state.env_status = await probe_environment(vg_dir, settings=settings)
            if rc == 0:
                self.state.add_log("INFO", "Зависимости успешно установлены (uv sync завершён).")
                show_toast(self.page, "Зависимости video-gen успешно установлены!")
            else:
                self.state.add_log("ERROR", f"Установка зависимостей завершилась с кодом {rc}")
                show_toast(self.page, f"Ошибка uv sync (код {rc})", error=True)
        finally:
            self.state.task_kind = None
            self.dashboard_view.refresh_all()
            self.page.update()

    def trigger_start_pipeline(self) -> None:
        if self.state.runner.is_running or self.state.task_kind is not None:
            show_toast(self.page, "Конвейер уже запущен", error=True)
            return

        saved_cfg = self.validate_and_save_config(notify=False)
        if saved_cfg is None:
            return

        self.state.task_kind = "pipeline"
        if self._selected_tab != 0:
            self._selected_tab = 0
            self.rail.selected_index = 0
            self._render_active_tab()
        else:
            self.dashboard_view.refresh_progress()
            self.page.update()

        self.page.run_task(self._run_pipeline_coro, saved_cfg)

    async def _run_pipeline_coro(self, config_path: Path) -> None:
        settings = self.state.settings
        vg_dir = settings.resolved_video_gen_dir()
        self.state.clear_run_state()

        def _flush_ui(*, force: bool = False) -> None:
            now = time.monotonic()
            if force or (now - self._last_ui_flush >= 0.08):
                self._last_ui_flush = now
                self.dashboard_view.refresh_progress()
                self.dashboard_view.refresh_logs()
                self.page.update()

        try:
            saved_cfg = await self.async_validate_and_save_config(notify=False)
            if saved_cfg is None:
                return
            config_path = saved_cfg

            # Preflight-проверка существования источников и ассетов (локально или на сервере)
            try:
                warnings = (
                    await check_remote_preflight(settings, self.state.config)
                    if settings.is_remote
                    else self.state.config.preflight_warnings(vg_dir)
                )
            except Exception as exc:
                self.state.add_log("ERROR", f"Ошибка preflight-проверки: {exc}")
                show_toast(self.page, f"Ошибка preflight-проверки: {exc}", error=True)
                _flush_ui(force=True)
                return

            if warnings:
                for w in warnings:
                    self.state.add_log("WARNING", w)
                _flush_ui(force=True)

                if any("CLI отклонит конфиг" in w for w in warnings):
                    show_toast(self.page, warnings[0], error=True)
                    return

            # Проверка окружения и обязательная синхронизация зависимостей при auto_sync_before_run
            env_st = await probe_environment(vg_dir, settings=settings)
            self.state.env_status = env_st
            self.dashboard_view.refresh_env()

            if settings.auto_sync_before_run:
                self.state.task_kind = "sync"
                self.state.add_log(
                    "INFO",
                    "Проверка и синхронизация зависимостей (`uv sync`) перед запуском...",
                )
                _flush_ui(force=True)

                def _on_sync_line(lvl: str, txt: str) -> None:
                    self.state.add_log(lvl, txt)
                    _flush_ui()

                sync_rc = await self.state.runner.install_dependencies(
                    vg_dir,
                    _on_sync_line,
                    settings=settings,
                )
                self.state.env_status = await probe_environment(vg_dir, settings=settings)
                self.dashboard_view.refresh_env()
                if sync_rc != 0:
                    show_toast(
                        self.page,
                        f"Не удалось установить зависимости (код {sync_rc})",
                        error=True,
                    )
                    return

            self.state.task_kind = "pipeline"
            _flush_ui(force=True)

            def _on_event(ev: dict[str, Any]) -> None:
                self.state.apply_cli_event(ev)
                important = ev.get("event") not in ("transcribe_progress", "render_progress")
                _flush_ui(force=important)

            def _on_line(lvl: str, txt: str) -> None:
                self.state.add_log(lvl, txt)
                _flush_ui()

            extra_env: dict[str, str] = {}
            if self.state.config.params.analysis_api_key:
                extra_env["OPENAI_API_KEY"] = self.state.config.params.analysis_api_key
            if self.state.config.params.gemini_key:
                extra_env["GEMINI_API_KEY"] = self.state.config.params.gemini_key

            rc = await self.state.runner.run_pipeline(
                vg_dir,
                config_path,
                _on_event,
                _on_line,
                extra_env=extra_env,
                settings=settings,
                config_json_text=self.state.config.to_json_text(),
            )

            if rc == 0:
                show_toast(
                    self.page,
                    f"Конвейер завершён! Готово клипов: {self.state.stats['ok']}",
                )
            elif rc in (130, -2, -15):
                self.state.add_log("WARNING", "Конвейер остановлен пользователем.")
                show_toast(self.page, "Выполнение остановлено")
            else:
                self.state.add_log("ERROR", f"CLI video-gen завершился с кодом {rc}")
                show_toast(self.page, f"CLI video-gen завершился с ошибкой (код {rc})", error=True)
        finally:
            self.state.task_kind = None
            self.dashboard_view.refresh_all()
            self.page.update()

    def trigger_stop_process(self) -> None:
        self.page.run_task(self._stop_coro)

    async def _stop_coro(self) -> None:
        self.state.add_log("WARNING", "Отправка сигнала остановки процессу video-gen...")
        self.dashboard_view.refresh_logs()
        self.page.update()
        await self.state.runner.stop()


def main(page: ft.Page) -> None:
    """Точка входа страницы Flet 1.1 (`ft.run(main)`)."""
    app = VideoGenStudioApp(page)
    app.setup_page()
