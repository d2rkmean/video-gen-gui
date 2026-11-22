"""Тесты моделей конфигурации, конвертера ASS-цветов, парсера CLI JSON и сборки UI Flet 1.1."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol
from unittest.mock import MagicMock

import flet as ft
import pytest

from video_gen_gui.models import (
    VideoGenConfig,
    ass_to_hex,
    hex_to_ass,
)
from video_gen_gui.runner import build_clean_env, parse_json_progress_line
from video_gen_gui.state import AppState
from video_gen_gui.views.dashboard import DashboardView
from video_gen_gui.views.database import DatabaseView
from video_gen_gui.views.gallery import GalleryView
from video_gen_gui.views.pipeline import PipelineView
from video_gen_gui.views.render import RenderView
from video_gen_gui.views.sources import SourcesView
from video_gen_gui.views.subtitles import SubtitlesView


def test_ass_hex_roundtrip() -> None:
    # Жёлтый #FFFF00 в BGR ASS -> &H0000FFFF
    ass_yellow = hex_to_ass("#FFFF00", 0)
    assert ass_yellow == "&H0000FFFF"
    hx, alpha = ass_to_hex(ass_yellow)
    assert hx == "#FFFF00"
    assert alpha == 0

    # Оранжевый #FF6D00 с прозрачностью 128 (0x80) -> &H80006DFF
    ass_orange = hex_to_ass("#FF6D00", 128)
    assert ass_orange == "&H80006DFF"
    hx2, alpha2 = ass_to_hex(ass_orange)
    assert hx2 == "#FF6D00"
    assert alpha2 == 128


def test_config_validation_and_toml_migration(tmp_path: Path) -> None:
    toml_sample = tmp_path / "legacy.toml"
    toml_sample.write_text(
        """
[[videos.top_videos]]
type = "video"
source = "input/top"
caption_name = "Тест"

[[videos.buttom_videos]]
type = "pygame"
source = "runner"

[params]
gemini_key = "test-gemini-key"
gemini_model = "gemini-2.5-flash"
min_part_duration = 25
part_duration = 55

[telegram]
enabled = true
""",
        encoding="utf-8",
    )

    cfg = VideoGenConfig.load_file(toml_sample)
    assert cfg.videos.top_videos[0].caption_name == "Тест"
    assert cfg.videos.bottom_videos[0].type == "pygame"
    assert cfg.videos.bottom_videos[0].source == "runner"
    assert cfg.params.analysis_backend == "custom"
    assert cfg.params.analysis_api_key == "test-gemini-key"

    out_json = tmp_path / "config.json"
    cfg.save_json(out_json)
    reloaded = VideoGenConfig.load_file(out_json)
    assert reloaded.params.min_part_duration == 25
    assert "telegram" not in reloaded.to_cli_dict()
    assert "banner" not in reloaded.to_cli_dict().get("assets", {})


def test_duration_validation_error() -> None:
    with pytest.raises(ValueError, match=r"Мин\. длительность"):
        VideoGenConfig.model_validate(
            {
                "params": {
                    "min_part_duration": 60,
                    "part_duration": 40,
                }
            }
        )


def test_parse_json_progress_and_state_lifecycle() -> None:
    assert parse_json_progress_line("обычный лог ffmpeg") is None
    ev = parse_json_progress_line('{"event": "pipeline_started", "total": 1, "jobs": ["ep01.mp4"]}')
    assert ev is not None

    st = AppState()
    st.apply_cli_event(ev)
    assert st.total_jobs == 1
    assert len(st.jobs) == 1
    assert st.jobs[0].name == "ep01.mp4"

    st.apply_cli_event(
        {"event": "transcribe_progress", "job": "ep01.mp4", "current": 30, "total": 60, "pct": 50.0}
    )
    assert st.jobs[0].transcribe_pct == 50.0

    # В analyze_done и render_start CLI передаёт stem без расширения ("ep01")
    st.apply_cli_event(
        {"event": "analyze_done", "job": "ep01", "clips": 2, "titles": ["video010101.mp4", "video010102.mp4"]}
    )
    assert st.jobs[0].clips_total == 2

    st.apply_cli_event(
        {"event": "render_start", "job": "ep01", "clip": "video010101.mp4", "idx": 1, "total": 2}
    )
    st.apply_cli_event({"event": "render_progress", "clip": "video010101.mp4", "pct": 75.5})
    assert st.jobs[0].render_pct == 75.5

    st.apply_cli_event({"event": "render_done", "clip": "video010101.mp4", "success": True})
    assert st.jobs[0].clips_done == 1

    st.apply_cli_event({"event": "pipeline_finished", "ok": 2, "failed": 0, "broken_videos": 0})
    assert st.stats["ok"] == 2


def test_clean_env_strips_virtualenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VIRTUAL_ENV", "/some/gui/.venv")
    monkeypatch.setenv("PYTHONPATH", "/some/gui/src")
    cublas_dir = tmp_path / ".venv" / "lib" / "python3.12" / "site-packages" / "nvidia" / "cublas" / "lib"
    cublas_dir.mkdir(parents=True)

    env = build_clean_env(tmp_path, extra_env={"OPENAI_API_KEY": "sk-test"})
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert env["OPENAI_API_KEY"] == "sk-test"
    assert str(cublas_dir) in env["LD_LIBRARY_PATH"]


class _Buildable(Protocol):
    def build(self) -> ft.Control: ...


def test_all_flet_views_build_controls() -> None:
    fake_page = MagicMock(spec=ft.Page)
    state = AppState()
    picker = ft.FilePicker()
    clip = ft.Clipboard()

    views: list[_Buildable] = [
        DashboardView(
            fake_page,
            state,
            picker,
            clip,
            lambda: None,
            lambda: None,
            lambda _: None,
            lambda: None,
            lambda: None,
        ),
        SourcesView(fake_page, state, picker),
        PipelineView(fake_page, state, picker),
        RenderView(fake_page, state, picker),
        SubtitlesView(fake_page, state, picker),
        DatabaseView(fake_page, state),
        GalleryView(fake_page, state, clip),
    ]
    for v in views:
        ctrl = v.build()
        assert isinstance(ctrl, ft.Control)
