"""Переиспользуемые UI-компоненты в строгом утилитарном стиле (Flet 1.1)."""

from __future__ import annotations

import asyncio
import posixpath
import sys
from collections.abc import Callable
from pathlib import Path

import flet as ft

from video_gen_gui.models import ASS_COLOR_RE, HEX_COLOR_RE, GuiSettings, ass_to_hex, hex_to_ass
from video_gen_gui.remote import list_remote_dir, relativize_remote_path

PRESET_PALETTE: list[tuple[str, str]] = [
    ("Жёлтый", "#FFFF00"),
    ("Белый", "#FFFFFF"),
    ("Чёрный", "#000000"),
    ("Лайм", "#00FF66"),
    ("Циан", "#00E5FF"),
    ("Оранжевый", "#FF6D00"),
    ("Розовый", "#FF2E93"),
    ("Красный", "#FF1744"),
]


def show_toast(page: ft.Page, message: str, *, error: bool = False) -> None:
    """Показывает строгое всплывающее уведомление через Flet 1.1 page.show_dialog(SnackBar)."""
    snack = ft.SnackBar(
        content=ft.Row(
            spacing=8,
            controls=[
                ft.Icon(
                    ft.Icons.ERROR_OUTLINE if error else ft.Icons.CHECK,
                    color=ft.Colors.WHITE,
                    size=18,
                ),
                ft.Text(message, color=ft.Colors.WHITE, size=12, expand=True),
            ],
        ),
        bgcolor="#7F1D1D" if error else "#1E293B",
        behavior=ft.SnackBarBehavior.FLOATING,
        margin=ft.Margin.all(12),
        show_close_icon=True,
        close_icon_color=ft.Colors.WHITE,
    )
    page.show_dialog(snack)


def section_card(
    title: str,
    icon: ft.IconData,
    controls: list[ft.Control],
    *,
    subtitle: str | None = None,
    trailing: ft.Control | None = None,
) -> ft.Container:
    """Строгая техническая карточка секции настроек (минимальное скругление, тонкая рамка)."""
    header_left: list[ft.Control] = [
        ft.Icon(icon, size=18, color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Column(
            spacing=1,
            expand=True,
            controls=[
                ft.Text(title, size=14, weight=ft.FontWeight.W_600),
                *(
                    [ft.Text(subtitle, size=11, color=ft.Colors.ON_SURFACE_VARIANT)]
                    if subtitle
                    else []
                ),
            ],
        ),
    ]
    if trailing is not None:
        header_left.append(trailing)

    return ft.Container(
        bgcolor=ft.Colors.SURFACE_CONTAINER_LOW,
        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
        border_radius=ft.BorderRadius.all(4),
        padding=ft.Padding.all(14),
        content=ft.Column(
            spacing=12,
            controls=[
                ft.Row(
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=header_left,
                ),
                ft.Divider(height=1, color=ft.Colors.OUTLINE_VARIANT),
                *controls,
            ],
        ),
    )


def status_chip(label: str, ok: bool | None, detail: str = "") -> ft.Container:
    """Строгий прямоугольный индикатор состояния компонента окружения."""
    bg: ft.ColorValue
    fg: ft.ColorValue
    border_col: ft.ColorValue
    if ok is None:
        bg = ft.Colors.SURFACE_CONTAINER_HIGHEST
        fg = ft.Colors.ON_SURFACE_VARIANT
        border_col = ft.Colors.OUTLINE_VARIANT
        ic = ft.Icons.REMOVE
    elif ok:
        bg = ft.Colors.with_opacity(0.08, ft.Colors.GREEN_400)
        fg = ft.Colors.GREEN_400
        border_col = ft.Colors.with_opacity(0.35, ft.Colors.GREEN_400)
        ic = ft.Icons.CHECK
    else:
        bg = ft.Colors.with_opacity(0.08, ft.Colors.AMBER_400)
        fg = ft.Colors.AMBER_400
        border_col = ft.Colors.with_opacity(0.35, ft.Colors.AMBER_400)
        ic = ft.Icons.PRIORITY_HIGH

    text = f"{label}: {detail}" if detail else label
    return ft.Container(
        bgcolor=bg,
        border=ft.Border.all(1, border_col),
        border_radius=ft.BorderRadius.all(3),
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        content=ft.Row(
            tight=True,
            spacing=5,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Icon(ic, size=13, color=fg),
                ft.Text(
                    text,
                    size=11,
                    weight=ft.FontWeight.W_500,
                    font_family="monospace",
                    color=fg,
                ),
            ],
        ),
    )


def format_relative_if_inside(picked: str, base_dir: Path | None) -> str:
    """Если выбранный путь внутри base_dir, делает его относительным для переносимости config.json."""
    if not base_dir:
        return picked
    try:
        rel = Path(picked).resolve().relative_to(base_dir.resolve())
        return str(rel)
    except ValueError:
        return picked


async def pick_remote_path_dialog(
    page: ft.Page,
    settings: GuiSettings,
    *,
    title: str,
    pick_mode: str = "dir",  # "dir" | "file"
    allowed_extensions: list[str] | None = None,
    initial_dir: str | None = None,
    relativize_to_vg: bool = True,
    on_picked: Callable[[str], None],
) -> None:
    """Открывает модальный проводник по удалённой машине через SSH."""
    start_dir = (initial_dir or settings.resolved_remote_video_gen_dir()).strip() or "~"
    current_path_tf = ft.TextField(
        label="Удалённый путь на сервере",
        value=start_dir,
        dense=True,
        expand=True,
        text_style=ft.TextStyle(font_family="monospace", size=12),
    )
    status_txt = ft.Text("Загрузка списка...", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
    entries_list = ft.ListView(height=300, spacing=2, padding=ft.Padding.all(4))
    remote_vg_abs: list[str] = [""]

    async def _load_dir(target_dir: str) -> None:
        status_txt.value = f"Чтение {target_dir}..."
        entries_list.controls.clear()
        page.update()
        try:
            if not remote_vg_abs[0] and relativize_to_vg:
                try:
                    vg_abs, _ = await list_remote_dir(
                        settings, settings.resolved_remote_video_gen_dir(), only_dirs=True
                    )
                    remote_vg_abs[0] = vg_abs
                except Exception:
                    remote_vg_abs[0] = ""

            resolved_abs, items = await list_remote_dir(
                settings,
                target_dir,
                only_dirs=(pick_mode == "dir"),
                allowed_exts=allowed_extensions if pick_mode == "file" else None,
            )
        except Exception as exc:
            status_txt.value = f"Ошибка: {exc}"
            page.update()
            return

        current_path_tf.value = resolved_abs
        status_txt.value = f"Объектов: {len(items)}"

        parent_dir = posixpath.dirname(resolved_abs.rstrip("/")) or "/"
        if resolved_abs != "/":
            entries_list.controls.append(
                ft.ListTile(
                    dense=True,
                    leading=ft.Icon(ft.Icons.ARROW_UPWARD, size=16),
                    title=ft.Text(".. (На уровень выше)", size=12, font_family="monospace"),
                    on_click=lambda _: page.run_task(_load_dir, parent_dir),
                )
            )

        for entry in items:
            is_dir = bool(entry["is_dir"])
            entry_path = str(entry["path"])
            entry_name = str(entry["name"])

            def _make_click(p: str, d: bool) -> Callable[[ft.Event[ft.ListTile]], None]:
                def _handler(_: ft.Event[ft.ListTile]) -> None:
                    if d:
                        page.run_task(_load_dir, p)
                    else:
                        final_p = (
                            relativize_remote_path(p, remote_vg_abs[0])
                            if (relativize_to_vg and remote_vg_abs[0])
                            else p
                        )
                        page.pop_dialog()
                        on_picked(final_p)
                        page.update()

                return _handler

            entries_list.controls.append(
                ft.ListTile(
                    dense=True,
                    leading=ft.Icon(
                        ft.Icons.FOLDER_OUTLINED if is_dir else ft.Icons.DESCRIPTION_OUTLINED,
                        size=16,
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                    title=ft.Text(entry_name, size=12, font_family="monospace"),
                    on_click=_make_click(entry_path, is_dir),
                )
            )
        page.update()

    def _select_current_dir(_: ft.Event[ft.Button]) -> None:
        raw = (current_path_tf.value or "").strip()
        if not raw:
            return
        final_p = (
            relativize_remote_path(raw, remote_vg_abs[0])
            if (relativize_to_vg and remote_vg_abs[0])
            else raw
        )
        page.pop_dialog()
        on_picked(final_p)
        page.update()

    actions: list[ft.Control] = [
        ft.TextButton(content="Отмена", on_click=lambda _: page.pop_dialog()),
    ]
    if pick_mode == "dir":
        actions.append(ft.FilledButton(content="Выбрать текущую папку", on_click=_select_current_dir))

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text(f"SSH: {title}", size=15, weight=ft.FontWeight.W_600),
        content=ft.Container(
            width=560,
            content=ft.Column(
                tight=True,
                spacing=8,
                controls=[
                    ft.Row(
                        spacing=6,
                        controls=[
                            current_path_tf,
                            ft.IconButton(
                                icon=ft.Icons.REFRESH,
                                tooltip="Перейти",
                                on_click=lambda _: page.run_task(
                                    _load_dir, (current_path_tf.value or "~").strip()
                                ),
                            ),
                        ],
                    ),
                    status_txt,
                    ft.Container(
                        border=ft.Border.all(1, ft.Colors.OUTLINE_VARIANT),
                        border_radius=ft.BorderRadius.all(4),
                        content=entries_list,
                    ),
                ],
            ),
        ),
        actions=actions,
    )
    page.show_dialog(dlg)
    await _load_dir(start_dir)


def path_picker_row(
    *,
    label: str,
    value: str,
    file_picker: ft.FilePicker,
    on_changed: Callable[[str], None],
    pick_mode: str = "dir",  # "dir" | "file" | "both"
    hint_text: str = "",
    allowed_extensions: list[str] | None = None,
    base_dir_getter: Callable[[], Path] | None = None,
    page: ft.Page | None = None,
    settings_getter: Callable[[], GuiSettings] | None = None,
) -> ft.Row:
    """Строка ввода пути с поддержкой локального FilePicker и удалённого SSH-проводника."""
    tf = ft.TextField(
        label=label,
        value=value,
        hint_text=hint_text,
        expand=True,
        dense=True,
        text_style=ft.TextStyle(font_family="monospace", size=12),
        on_change=lambda e: on_changed(e.control.value or ""),
    )

    def _apply_picked(formatted: str) -> None:
        tf.value = formatted
        on_changed(formatted)
        if page is not None:
            page.update()

    async def _pick_dir(_: ft.Event[ft.IconButton]) -> None:
        settings = settings_getter() if settings_getter else None
        if settings is not None and settings.is_remote and page is not None:
            await pick_remote_path_dialog(
                page,
                settings,
                title=label,
                pick_mode="dir",
                on_picked=_apply_picked,
            )
            return

        init_dir = str(base_dir_getter()) if base_dir_getter else None
        chosen = await file_picker.get_directory_path(
            dialog_title=label,
            initial_directory=init_dir,
        )
        if chosen:
            formatted = format_relative_if_inside(
                chosen, base_dir_getter() if base_dir_getter else None
            )
            _apply_picked(formatted)

    async def _pick_file(_: ft.Event[ft.IconButton]) -> None:
        settings = settings_getter() if settings_getter else None
        if settings is not None and settings.is_remote and page is not None:
            await pick_remote_path_dialog(
                page,
                settings,
                title=label,
                pick_mode="file",
                allowed_extensions=allowed_extensions,
                on_picked=_apply_picked,
            )
            return

        init_dir = str(base_dir_getter()) if base_dir_getter else None
        ft_type = (
            ft.FilePickerFileType.CUSTOM if allowed_extensions else ft.FilePickerFileType.ANY
        )
        files = await file_picker.pick_files(
            dialog_title=label,
            initial_directory=init_dir,
            file_type=ft_type,
            allowed_extensions=allowed_extensions,
            allow_multiple=False,
        )
        if files and files[0].path:
            formatted = format_relative_if_inside(
                files[0].path, base_dir_getter() if base_dir_getter else None
            )
            _apply_picked(formatted)

    buttons: list[ft.Control] = []
    if pick_mode in ("file", "both"):
        buttons.append(
            ft.IconButton(
                icon=ft.Icons.DESCRIPTION_OUTLINED,
                tooltip="Выбрать файл",
                icon_size=18,
                on_click=_pick_file,
            )
        )
    if pick_mode in ("dir", "both"):
        buttons.append(
            ft.IconButton(
                icon=ft.Icons.FOLDER_OPEN_OUTLINED,
                tooltip="Выбрать папку",
                icon_size=18,
                on_click=_pick_dir,
            )
        )

    return ft.Row(
        spacing=6,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[tf, *buttons],
    )


def number_slider_row(
    *,
    label: str,
    value: float,
    min_val: float,
    max_val: float,
    divisions: int,
    on_changed: Callable[[float], None],
    is_int: bool = False,
    suffix: str = "",
) -> ft.Column:
    """Слайдер с моноширинным отображением текущего числового значения."""
    clamped = max(min_val, min(max_val, float(value)))

    def _fmt(v: float) -> str:
        return f"{round(v)}{suffix}" if is_int else f"{v:.2f}{suffix}"

    val_badge = ft.Text(
        _fmt(clamped),
        size=12,
        font_family="monospace",
        weight=ft.FontWeight.W_600,
        color=ft.Colors.ON_SURFACE,
    )

    def _handle(e: ft.Event[ft.Slider]) -> None:
        v = float(e.control.value or min_val)
        val_badge.value = _fmt(v)
        on_changed(float(round(v)) if is_int else round(v, 3))

    return ft.Column(
        spacing=0,
        controls=[
            ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Text(label, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                    val_badge,
                ],
            ),
            ft.Slider(
                min=min_val,
                max=max_val,
                divisions=divisions,
                value=clamped,
                round=0 if is_int else 2,
                label="{value}",
                on_change=_handle,
            ),
        ],
    )


def ass_color_picker_row(
    *,
    page: ft.Page,
    label: str,
    ass_value: str,
    on_changed: Callable[[str], None],
) -> ft.Row:
    """Поле выбора цвета ASS (&HAABBGGRR) со строгим прямоугольным превью и палитрой."""
    hex_str, _ = ass_to_hex(ass_value)

    swatch = ft.Container(
        width=32,
        height=32,
        bgcolor=hex_str,
        border=ft.Border.all(1, ft.Colors.OUTLINE),
        border_radius=ft.BorderRadius.all(3),
    )

    tf = ft.TextField(
        label=label,
        value=ass_value,
        dense=True,
        expand=True,
        text_style=ft.TextStyle(font_family="monospace", size=12),
        helper="ASS: &HAABBGGRR",
    )

    def _apply_ass(new_ass: str) -> None:
        clean = new_ass.strip().upper()
        if ASS_COLOR_RE.match(clean):
            hx, _ = ass_to_hex(clean)
            swatch.bgcolor = hx
            tf.error = None
            tf.value = clean
            on_changed(clean)
        elif HEX_COLOR_RE.match(clean):
            converted = hex_to_ass(clean, 0)
            hx, _ = ass_to_hex(converted)
            swatch.bgcolor = hx
            tf.error = None
            tf.value = converted
            on_changed(converted)
        else:
            tf.error = "Ожидается &HAABBGGRR или #RRGGBB"
        page.update()

    tf.on_change = lambda e: _apply_ass(e.control.value or "")

    def _open_palette() -> None:
        cur_hex, cur_alpha = ass_to_hex(tf.value or ass_value)
        hex_input = ft.TextField(
            label="HEX (#RRGGBB)",
            value=cur_hex,
            dense=True,
            text_style=ft.TextStyle(font_family="monospace", size=12),
        )
        alpha_text = ft.Text(f"Прозрачность (Alpha ASS): {cur_alpha}", size=11)
        preview_box = ft.Container(
            height=38,
            border_radius=ft.BorderRadius.all(3),
            bgcolor=cur_hex,
            border=ft.Border.all(1, ft.Colors.OUTLINE),
            alignment=ft.Alignment.CENTER,
            content=ft.Text(
                hex_to_ass(cur_hex, cur_alpha),
                size=12,
                font_family="monospace",
                weight=ft.FontWeight.BOLD,
                color=ft.Colors.BLACK
                if cur_hex.upper() in ("#FFFFFF", "#FFFF00", "#00FF66", "#00E5FF")
                else ft.Colors.WHITE,
            ),
        )
        alpha_slider = ft.Slider(
            min=0,
            max=255,
            divisions=255,
            value=cur_alpha,
            round=0,
        )

        def _sync_dialog() -> None:
            cand = (hex_input.value or "").strip()
            if HEX_COLOR_RE.match(cand):
                norm_hex = f"#{cand.lstrip('#').upper()}"
                a_val = round(float(alpha_slider.value or 0))
                alpha_text.value = f"Прозрачность (0 = непрозр., 255 = прозр.): {a_val}"
                preview_box.bgcolor = norm_hex
                preview_box.content = ft.Text(
                    hex_to_ass(norm_hex, a_val),
                    size=12,
                    font_family="monospace",
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.BLACK
                    if norm_hex in ("#FFFFFF", "#FFFF00", "#00FF66", "#00E5FF")
                    else ft.Colors.WHITE,
                )
                page.update()

        hex_input.on_change = lambda _: _sync_dialog()
        alpha_slider.on_change = lambda _: _sync_dialog()

        def _pick_preset(hx: str) -> Callable[[], None]:
            def _handler() -> None:
                hex_input.value = hx
                _sync_dialog()

            return _handler

        preset_chips = ft.Row(
            wrap=True,
            spacing=6,
            run_spacing=6,
            controls=[
                ft.Container(
                    bgcolor=hx,
                    width=28,
                    height=28,
                    border_radius=ft.BorderRadius.all(3),
                    border=ft.Border.all(1, ft.Colors.OUTLINE),
                    tooltip=f"{name} ({hx})",
                    on_click=_pick_preset(hx),
                )
                for name, hx in PRESET_PALETTE
            ],
        )

        def _confirm() -> None:
            cand = (hex_input.value or "").strip()
            page.pop_dialog()
            if HEX_COLOR_RE.match(cand):
                a_val = round(float(alpha_slider.value or 0))
                _apply_ass(hex_to_ass(cand, a_val))

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(label, size=14, weight=ft.FontWeight.W_600),
            content=ft.Container(
                width=360,
                content=ft.Column(
                    tight=True,
                    spacing=10,
                    controls=[
                        preview_box,
                        ft.Text("Палитра:", size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                        preset_chips,
                        hex_input,
                        alpha_text,
                        alpha_slider,
                    ],
                ),
            ),
            actions=[
                ft.TextButton(content="Отмена", on_click=lambda _: page.pop_dialog()),
                ft.FilledButton(content="Применить", on_click=lambda _: _confirm()),
            ],
        )
        page.show_dialog(dlg)

    swatch.on_click = lambda _: _open_palette()

    return ft.Row(
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        controls=[
            swatch,
            tf,
            ft.IconButton(
                icon=ft.Icons.TUNE,
                icon_size=18,
                tooltip="Палитра цветов",
                on_click=lambda _: _open_palette(),
            ),
        ],
    )


async def open_in_system(path: Path) -> None:
    """Открывает файл или директорию системным приложением без блокировки цикла событий."""
    if sys.platform.startswith("linux"):
        await asyncio.create_subprocess_exec("xdg-open", str(path))
    elif sys.platform == "darwin":
        await asyncio.create_subprocess_exec("open", str(path))
    elif sys.platform == "win32":
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
