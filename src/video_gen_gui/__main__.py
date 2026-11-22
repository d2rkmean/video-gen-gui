"""Точка входа CLI пакета: `uv run video-gen-gui` или `python -m video_gen_gui`."""

from __future__ import annotations

import flet as ft

from video_gen_gui.app import main as flet_main


def main() -> None:
    ft.run(flet_main)


if __name__ == "__main__":
    main()
