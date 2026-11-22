"""Экран настроек каталогизации в PostgreSQL."""

from __future__ import annotations

import flet as ft

from video_gen_gui.remote import check_remote_tcp_endpoint
from video_gen_gui.runner import check_tcp_endpoint
from video_gen_gui.state import AppState
from video_gen_gui.views.components import section_card, show_toast, status_chip


class DatabaseView:
    """Редактор секции database с проверкой сетевой доступности PostgreSQL."""

    def __init__(self, page: ft.Page, state: AppState) -> None:
        self.page = page
        self.state = state
        self.ping_status = status_chip("Не проверено", None)
        self.status_holder = ft.Container(content=self.ping_status)

    async def _test_connection(self, btn_event: ft.Event[ft.Button]) -> None:
        db = self.state.config.database
        btn = btn_event.control
        btn.disabled = True
        self.status_holder.content = status_chip("Проверка...", None)
        self.page.update()

        timeout = min(db.timeout, 6.0)
        if self.state.settings.is_remote:
            ok, msg = await check_remote_tcp_endpoint(
                self.state.settings, db.host, db.port, timeout=timeout
            )
        else:
            ok, msg = await check_tcp_endpoint(db.host, db.port, timeout=timeout)

        btn.disabled = False
        self.ping_status = status_chip(
            "PostgreSQL", ok, "доступен" if ok else "недоступен"
        )
        self.status_holder.content = self.ping_status
        show_toast(self.page, msg, error=not ok)
        self.page.update()

    def build(self) -> ft.Control:
        db = self.state.config.database
        self.status_holder = ft.Container(content=self.ping_status)

        def _parse_int(val: str | None, fallback: int) -> int:
            try:
                return int((val or "").strip())
            except ValueError:
                return fallback

        def _parse_float(val: str | None, fallback: float) -> float:
            try:
                return max(1.0, min(120.0, float((val or "").strip().replace(",", "."))))
            except ValueError:
                return fallback

        return ft.Column(
            spacing=14,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            controls=[
                section_card(
                    "База данных (PostgreSQL)",
                    ft.Icons.STORAGE_OUTLINED,
                    subtitle="Автоматическая регистрация иерархических кодов фильмов, серий и нарезок (XXXXX-серия-клип)",
                    trailing=ft.Switch(
                        label="Включить запись в БД",
                        value=db.enabled,
                        on_change=lambda e: setattr(db, "enabled", bool(e.control.value)),
                    ),
                    controls=[
                        ft.Row(
                            spacing=10,
                            controls=[
                                ft.TextField(
                                    label="Хост PostgreSQL",
                                    value=db.host,
                                    dense=True,
                                    expand=True,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db, "host", (e.control.value or "").strip()
                                    ),
                                ),
                                ft.TextField(
                                    label="Порт",
                                    value=str(db.port),
                                    dense=True,
                                    width=120,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db, "port", _parse_int(e.control.value, 5432)
                                    ),
                                ),
                                ft.TextField(
                                    label="Имя базы (database)",
                                    value=db.database,
                                    dense=True,
                                    width=220,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db, "database", (e.control.value or "").strip()
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=10,
                            controls=[
                                ft.TextField(
                                    label="Пользователь (user)",
                                    value=db.user,
                                    dense=True,
                                    width=240,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db, "user", (e.control.value or "").strip()
                                    ),
                                ),
                                ft.TextField(
                                    label="Пароль (password)",
                                    value=db.password,
                                    password=True,
                                    can_reveal_password=True,
                                    dense=True,
                                    expand=True,
                                    on_change=lambda e: setattr(
                                        db, "password", e.control.value or ""
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=10,
                            controls=[
                                ft.TextField(
                                    label="Мин. соединений пула",
                                    value=str(db.min_connections),
                                    dense=True,
                                    width=180,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db,
                                        "min_connections",
                                        max(1, _parse_int(e.control.value, 1)),
                                    ),
                                ),
                                ft.TextField(
                                    label="Макс. соединений пула",
                                    value=str(db.max_connections),
                                    dense=True,
                                    width=185,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db,
                                        "max_connections",
                                        max(1, _parse_int(e.control.value, 10)),
                                    ),
                                ),
                                ft.TextField(
                                    label="Таймаут (сек)",
                                    value=str(db.timeout),
                                    dense=True,
                                    width=140,
                                    text_style=ft.TextStyle(font_family="monospace", size=12),
                                    on_change=lambda e: setattr(
                                        db,
                                        "timeout",
                                        _parse_float(e.control.value, 15.0),
                                    ),
                                ),
                            ],
                        ),
                        ft.Row(
                            spacing=12,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.FilledTonalButton(
                                    content="Проверить доступность порта БД",
                                    icon=ft.Icons.NETWORK_CHECK,
                                    on_click=self._test_connection,
                                ),
                                self.status_holder,
                            ],
                        ),
                    ],
                )
            ],
        )
