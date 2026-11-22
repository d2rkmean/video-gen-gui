"""Агрессивные тесты контроллера VideoGenStudioApp и всех вкладок Flet GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest

from video_gen_gui.app import VideoGenStudioApp
from video_gen_gui.views.database import DatabaseView
from video_gen_gui.views.gallery import GalleryView
from video_gen_gui.views.render import RenderView
from video_gen_gui.views.subtitles import SubtitlesView


def _make_fake_page() -> MagicMock:
    page = MagicMock(spec=ft.Page)
    page.window = MagicMock()
    page.fonts = {}
    page.run_task = MagicMock()
    page.show_dialog = MagicMock()
    page.pop_dialog = MagicMock()
    page.update = MagicMock()
    page.add = MagicMock()
    return page


def test_ssh_typing_does_not_spam_probes_and_commit_probes_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = _make_fake_page()
    app = VideoGenStudioApp(page)
    app.setup_page()

    app.state.settings.execution_mode = "ssh"
    page.run_task.reset_mock()

    typed = ""
    for ch in "192.168.1.50":
        typed += ch
        app.dashboard_view.ssh_host_tf.value = typed
        app.dashboard_view._save_ssh_fields(refresh_badge=True)

    assert page.run_task.call_count == 0
    assert app.mode_badge_text.value == "SSH: 192.168.1.50"

    app.dashboard_view._commit_ssh_fields()
    assert page.run_task.call_count >= 1


def test_double_start_pipeline_and_sync_blocked_while_task_active(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = _make_fake_page()
    app = VideoGenStudioApp(page)
    app.setup_page()
    page.run_task.reset_mock()
    page.show_dialog.reset_mock()

    app.trigger_start_pipeline()
    assert app.state.task_kind == "pipeline"
    assert page.run_task.call_count == 1
    assert app.dashboard_view.start_btn.disabled is True

    app.trigger_start_pipeline()
    app.trigger_install_deps(upgrade=False)
    assert page.run_task.call_count == 1
    assert page.show_dialog.call_count == 2


def test_navigation_all_tabs_theme_toggle_and_json_preview(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = _make_fake_page()
    app = VideoGenStudioApp(page)
    app.setup_page()

    for idx in range(7):
        ev = MagicMock()
        ev.control = MagicMock(selected_index=idx)
        app._on_nav_change(ev)
        assert app._selected_tab == idx
        assert isinstance(app.body_container.content, ft.Control)

    initial_theme = app.page.theme_mode
    app._toggle_theme(MagicMock())
    assert app.page.theme_mode != initial_theme

    page.show_dialog.reset_mock()
    app._show_json_preview()
    assert page.show_dialog.call_count == 1
    dlg = page.show_dialog.call_args.args[0]
    assert isinstance(dlg, ft.AlertDialog)


@pytest.mark.anyio
async def test_database_view_float_timeout_and_persistent_ping_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = _make_fake_page()
    app = VideoGenStudioApp(page)
    db_view = DatabaseView(page, app.state)
    ctrl = db_view.build()
    assert isinstance(ctrl, ft.Control)

    async def _fake_tcp(_host: str, _port: int, timeout: float = 4.0) -> tuple[bool, str]:
        assert timeout == 2.5
        return True, "OK"

    monkeypatch.setattr("video_gen_gui.views.database.check_tcp_endpoint", _fake_tcp)
    app.state.config.database.timeout = 2.5

    btn_ev = MagicMock()
    btn_ev.control = MagicMock(disabled=False)
    await db_view._test_connection(btn_ev)

    rebuilt = db_view.build()
    assert isinstance(rebuilt, ft.Control)
    assert db_view.status_holder.content is db_view.ping_status


def test_gallery_view_survives_broken_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = _make_fake_page()
    app = VideoGenStudioApp(page)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    good_mp4 = out_dir / "ok.mp4"
    good_mp4.write_bytes(b"0" * 2048)
    broken_link = out_dir / "broken.mp4"
    broken_link.symlink_to(tmp_path / "missing_target.mp4")

    app.state.config.params.output_dir = str(out_dir)
    gallery = GalleryView(page, app.state, ft.Clipboard())
    ctrl = gallery.build()
    assert isinstance(ctrl, ft.Control)
    assert "Найдено клипов: 1" in (gallery.summary_text.value or "")


def test_subtitles_and_render_views_edge_cases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    page = _make_fake_page()
    app = VideoGenStudioApp(page)

    sub_view = SubtitlesView(page, app.state, ft.FilePicker())
    sub_view.build()
    for enabled in (False, True):
        for pos in ("top", "middle", "bottom"):
            for anim in ("pop", "bounce", "none"):
                app.state.config.subtitles.enabled = enabled
                app.state.config.subtitles.position = pos  # type: ignore[assignment]
                app.state.config.subtitles.animation = anim  # type: ignore[assignment]
                sub_view._refresh_preview(update_page=False)
                assert isinstance(sub_view.preview_image.src, bytes)
                assert sub_view.preview_image.src.startswith(b"\x89PNG")

    render_view = RenderView(page, app.state, ft.FilePicker())
    for mode in ("center", "overlay", "end", "interval_minutes", "interval_percent"):
        app.state.config.render.banner_mode = mode  # type: ignore[assignment]
        built: Any = render_view.build()
        assert isinstance(built, ft.Control)


def _find_flutter_layout_violations(
    ctrl: ft.Control, parent: ft.Control | None = None
) -> list[str]:
    issues: list[str] = []
    exp = getattr(ctrl, "expand", None)
    if exp:
        if isinstance(parent, (ft.Row, ft.Column)) and getattr(parent, "wrap", False):
            issues.append(f"expand={exp} on {type(ctrl).__name__} inside {type(parent).__name__}(wrap=True)")
        if isinstance(parent, ft.Column) and getattr(parent, "scroll", None) is not None:
            issues.append(f"expand={exp} on {type(ctrl).__name__} inside scrollable Column")
        if isinstance(parent, ft.ListView):
            issues.append(f"expand={exp} on {type(ctrl).__name__} inside ListView")
    if (
        isinstance(ctrl, ft.TextField)
        and isinstance(parent, ft.Row)
        and getattr(parent, "wrap", False)
        and getattr(ctrl, "width", None) is None
    ):
        issues.append(f"TextField({ctrl.label!r}) without width inside Row(wrap=True)")

    for attr in ("content", "leading", "trailing", "title", "subtitle"):
        val = getattr(ctrl, attr, None)
        if isinstance(val, ft.Control):
            issues.extend(_find_flutter_layout_violations(val, ctrl))
    for attr in ("controls", "actions"):
        val = getattr(ctrl, attr, None)
        if isinstance(val, list):
            for child in val:
                if isinstance(child, ft.Control):
                    issues.extend(_find_flutter_layout_violations(child, ctrl))
    return issues


def test_no_flutter_layout_violations_across_all_views(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from video_gen_gui.models import VideoSourceConfig
    from video_gen_gui.state import JobStatus

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "demo.mp4").write_bytes(b"0" * 2048)

    page = _make_fake_page()
    app = VideoGenStudioApp(page)
    app.setup_page()
    app.state.config.params.output_dir = str(out_dir)
    app.state.config.videos.top_videos = [
        VideoSourceConfig(type="video", source="input/"),
        VideoSourceConfig(type="youtube", source="https://youtu.be/test"),
    ]
    app.state.config.videos.bottom_videos = [
        VideoSourceConfig(type="video", source="bottom/"),
        VideoSourceConfig(type="pygame", source="runner"),
    ]
    app.state.jobs = [JobStatus(name="ep01.mp4", stem="ep01", clips_total=2, clips_done=1)]

    all_issues: list[str] = []
    for mode in ("local", "ssh"):
        app.state.settings.execution_mode = mode  # type: ignore[assignment]
        app.state.settings.ssh_host = "127.0.0.1"
        for tab_idx in range(7):
            app._selected_tab = tab_idx
            app._render_active_tab()
            content = app.body_container.content
            assert isinstance(content, ft.Control)
            for issue in _find_flutter_layout_violations(content, app.body_container):
                all_issues.append(f"[{mode} tab={tab_idx}] {issue}")

    assert all_issues == [], "\n".join(all_issues)
