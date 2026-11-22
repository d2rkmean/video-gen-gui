"""Галерея готовых клипов: просмотр сгенерированных .mp4 и метаданных .json (локально и по SSH)."""

from __future__ import annotations

import json
from collections.abc import Callable, Coroutine
from datetime import datetime
from pathlib import Path
from typing import Any

import flet as ft

from video_gen_gui.remote import download_remote_file, list_remote_clips
from video_gen_gui.state import AppState
from video_gen_gui.views.components import open_in_system, section_card, show_toast


def _safe_rel_path(rel_path: str) -> Path:
    parts = [p for p in Path(rel_path).parts if p not in ("", ".", "..", "/")]
    return Path(*parts) if parts else Path("clip.mp4")


class GalleryView:
    """Обозреватель готовых роликов и их метаданных."""

    def __init__(
        self,
        page: ft.Page,
        state: AppState,
        clipboard: ft.Clipboard,
    ) -> None:
        self.page = page
        self.state = state
        self.clipboard = clipboard
        self.clips_col = ft.Column(spacing=8)
        self.summary_text = ft.Text(
            "",
            size=12,
            font_family="monospace",
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.open_folder_btn = ft.OutlinedButton(
            content="Открыть папку вывода",
            icon=ft.Icons.FOLDER_OPEN_OUTLINED,
            on_click=self._open_out_folder,
        )

    def _resolve_out_dir(self) -> Path:
        vg_dir = self.state.settings.resolved_video_gen_dir()
        raw = Path(self.state.config.params.output_dir)
        return raw if raw.is_absolute() else (vg_dir / raw)

    def _load_meta(self, mp4_path: Path) -> dict[str, Any]:
        meta_file = mp4_path.with_suffix(".json")
        if meta_file.is_file():
            try:
                data = json.loads(meta_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
        return {}

    async def _copy_caption(self, text: str) -> None:
        await self.clipboard.set(text)
        show_toast(self.page, "Описание скопировано в буфер обмена")

    def _build_clip_card_data(
        self,
        *,
        rel_name: str,
        size_mb: float,
        mtime: float,
        meta: dict[str, Any],
        action_btn: ft.Control,
    ) -> ft.Container:
        mtime_str = datetime.fromtimestamp(mtime).strftime("%d.%m.%Y %H:%M")
        title = str(meta.get("viral_title") or meta.get("reason") or rel_name)
        score = meta.get("score")
        code = meta.get("code")
        start = meta.get("start")
        end = meta.get("end")
        dur_str = (
            f"{float(end) - float(start):.1f} с ({float(start):.0f}–{float(end):.0f} с)"
            if isinstance(start, (int, float)) and isinstance(end, (int, float))
            else ""
        )
        desc = str(meta.get("description") or meta.get("hook") or "")

        badges: list[ft.Control] = []
        if code:
            badges.append(
                ft.Container(
                    bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
                    border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                    border_radius=ft.BorderRadius.all(3),
                    padding=ft.Padding.symmetric(horizontal=7, vertical=2),
                    content=ft.Text(
                        f"Код: {code}",
                        size=11,
                        font_family="monospace",
                        weight=ft.FontWeight.W_600,
                        color=ft.Colors.ON_SURFACE,
                    ),
                )
            )
        if score is not None:
            badges.append(
                ft.Container(
                    bgcolor=ft.Colors.with_opacity(0.10, ft.Colors.AMBER_400),
                    border=ft.Border.all(1, ft.Colors.with_opacity(0.35, ft.Colors.AMBER_400)),
                    border_radius=ft.BorderRadius.all(3),
                    padding=ft.Padding.symmetric(horizontal=7, vertical=2),
                    content=ft.Text(
                        f"Оценка: {score}/10",
                        size=11,
                        font_family="monospace",
                        weight=ft.FontWeight.W_600,
                        color=ft.Colors.AMBER_300,
                    ),
                )
            )

        def _make_copy_handler(txt: str) -> Callable[[], Coroutine[Any, Any, None]]:
            async def _copy_btn() -> None:
                await self._copy_caption(txt)

            return _copy_btn

        return ft.Container(
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
            border_radius=ft.BorderRadius.all(4),
            padding=ft.Padding.all(12),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Column(
                        spacing=4,
                        expand=True,
                        controls=[
                            ft.Row(
                                spacing=8,
                                wrap=True,
                                controls=[
                                    ft.Icon(
                                        ft.Icons.PLAY_CIRCLE_OUTLINE,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                        size=18,
                                    ),
                                    ft.Text(title, weight=ft.FontWeight.W_600, size=13),
                                    *badges,
                                ],
                            ),
                            ft.Text(
                                " • ".join(
                                    x for x in [rel_name, dur_str, f"{size_mb:.1f} МБ", mtime_str] if x
                                ),
                                size=11,
                                font_family="monospace",
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            *(
                                [ft.Text(desc, size=12, max_lines=2)]
                                if desc
                                else []
                            ),
                        ],
                    ),
                    ft.Row(
                        tight=True,
                        spacing=6,
                        controls=[
                            ft.IconButton(
                                icon=ft.Icons.CONTENT_COPY,
                                tooltip="Скопировать заголовок и описание",
                                on_click=_make_copy_handler(f"{title}\n\n{desc}".strip()),
                            ),
                            action_btn,
                        ],
                    ),
                ],
            ),
        )

    def _build_clip_card(self, mp4: Path, out_root: Path) -> ft.Container:
        meta = self._load_meta(mp4)
        stat = mp4.stat()
        size_mb = stat.st_size / (1024 * 1024)

        try:
            rel_name = str(mp4.relative_to(out_root))
        except ValueError:
            rel_name = mp4.name

        async def _open_video(_: ft.Event[ft.Button]) -> None:
            await open_in_system(mp4)

        btn = ft.FilledTonalButton(
            content="Открыть",
            icon=ft.Icons.PLAY_ARROW,
            on_click=_open_video,
        )
        return self._build_clip_card_data(
            rel_name=rel_name,
            size_mb=size_mb,
            mtime=stat.st_mtime,
            meta=meta,
            action_btn=btn,
        )

    def _build_remote_clip_card(self, clip: dict[str, Any]) -> ft.Container:
        rel_path = str(clip.get("rel_path") or clip.get("name") or "clip.mp4")
        abs_path = str(clip.get("abs_path") or "")
        size_mb = float(clip.get("size_mb") or 0.0)
        mtime = float(clip.get("mtime") or 0.0)
        meta_raw = clip.get("meta")
        meta = meta_raw if isinstance(meta_raw, dict) else {}

        async def _download_and_open(e: ft.Event[ft.Button]) -> None:
            btn_ctrl = e.control
            btn_ctrl.disabled = True
            self.page.update()
            local_dest = (
                self.state.settings.remote_cache_dir() / "downloads" / _safe_rel_path(rel_path)
            )
            try:
                show_toast(self.page, f"Скачивание {Path(rel_path).name}...")
                downloaded = await download_remote_file(self.state.settings, abs_path, local_dest)
                await open_in_system(downloaded)
            except Exception as exc:
                show_toast(self.page, f"Ошибка скачивания клипа: {exc}", error=True)
            finally:
                btn_ctrl.disabled = False
                self.page.update()

        btn = ft.FilledTonalButton(
            content="Скачать и открыть",
            icon=ft.Icons.DOWNLOAD,
            on_click=_download_and_open,
        )
        return self._build_clip_card_data(
            rel_name=rel_path,
            size_mb=size_mb,
            mtime=mtime,
            meta=meta,
            action_btn=btn,
        )

    async def _refresh_remote_coro(self) -> None:
        settings = self.state.settings
        dest = settings.ssh_destination() or "не настроен"
        out_dir_cfg = self.state.config.params.output_dir
        try:
            remote_out_dir, clips = await list_remote_clips(settings, out_dir_cfg)
        except Exception as exc:
            self.summary_text.value = f"SSH ({dest}): {out_dir_cfg}"
            self.clips_col.controls = [
                ft.Text(
                    f"Не удалось получить список клипов с сервера: {exc}",
                    size=12,
                    color=ft.Colors.RED_300,
                )
            ]
            self.page.update()
            return

        self.summary_text.value = (
            f"SSH ({dest}): {remote_out_dir} (найдено клипов: {len(clips)})"
        )
        if not clips:
            self.clips_col.controls = [
                ft.Text(
                    "На удалённом сервере пока нет готовых .mp4 роликов.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
        else:
            self.clips_col.controls = [self._build_remote_clip_card(c) for c in clips]
        self.page.update()

    def refresh(self) -> None:
        self.open_folder_btn.visible = not self.state.settings.is_remote
        if self.state.settings.is_remote:
            dest = self.state.settings.ssh_destination() or "не настроен"
            out_dir_cfg = self.state.config.params.output_dir
            self.summary_text.value = f"SSH ({dest}): {out_dir_cfg}"
            self.clips_col.controls = [
                ft.Text(
                    "Загрузка списка клипов с удалённого сервера...",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
            if hasattr(self.page, "run_task"):
                self.page.run_task(self._refresh_remote_coro)
            return

        out_root = self._resolve_out_dir()
        if not out_root.is_dir():
            self.summary_text.value = f"Папка не создана: {out_root}"
            self.clips_col.controls = [
                ft.Text(
                    "Папка готовых клипов пока отсутствует. Запустите генерацию на вкладке Дашборд.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
            return

        sized_files: list[tuple[float, Path]] = []
        for candidate in out_root.rglob("*.mp4"):
            try:
                sized_files.append((candidate.stat().st_mtime, candidate))
            except OSError:
                continue
        sized_files.sort(key=lambda pair: pair[0], reverse=True)
        mp4_files = [pair[1] for pair in sized_files]
        self.summary_text.value = f"Найдено клипов: {len(mp4_files)} (папка: {out_root})"
        if not mp4_files:
            self.clips_col.controls = [
                ft.Text(
                    "В папке вывода пока нет готовых .mp4 роликов.",
                    size=12,
                    color=ft.Colors.ON_SURFACE_VARIANT,
                )
            ]
        else:
            self.clips_col.controls = [self._build_clip_card(f, out_root) for f in mp4_files[:100]]

    async def _open_out_folder(self) -> None:
        out_root = self._resolve_out_dir()
        out_root.mkdir(parents=True, exist_ok=True)
        await open_in_system(out_root)

    def build(self) -> ft.Control:
        self.refresh()
        return ft.Column(
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "Готовые вертикальные клипы",
                    ft.Icons.VIDEO_LIBRARY_OUTLINED,
                    trailing=ft.Row(
                        tight=True,
                        spacing=8,
                        controls=[
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                tooltip="Обновить список клипов",
                                on_click=lambda _: self.refresh(),
                            ),
                            self.open_folder_btn,
                        ],
                    ),
                    controls=[
                        self.summary_text,
                        self.clips_col,
                    ],
                )
            ],
        )
