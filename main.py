"""Скрипт запуска для `uv run flet run` (стандартная точка входа Flet 1.1 CLI)."""

from __future__ import annotations

import flet as ft

from video_gen_gui.app import main

if __name__ == "__main__":
    ft.run(main)
