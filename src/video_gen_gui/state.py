"""Состояние GUI-приложения: активный конфиг, прогресс очереди видео и журнал событий."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from video_gen_gui.models import GuiSettings, VideoGenConfig
from video_gen_gui.runner import CliRunner, EnvironmentStatus


@dataclass(slots=True)
class LogEntry:
    timestamp: str
    level: str
    message: str


@dataclass(slots=True)
class JobStatus:
    name: str
    stem: str
    stage: str = "Ожидание"
    transcribe_pct: float = 0.0
    clips_total: int = 0
    clips_done: int = 0
    current_clip: str = ""
    render_pct: float = 0.0
    broken: bool = False
    finished: bool = False
    clip_titles: list[str] = field(default_factory=list)


class AppState:
    """Центральное хранилище состояния для всех вкладок интерфейса."""

    def __init__(self) -> None:
        self.settings: GuiSettings = GuiSettings.load()
        self.config: VideoGenConfig = self._load_initial_config()
        self.runner: CliRunner = CliRunner()
        self.env_status: EnvironmentStatus | None = None

        self.task_kind: str | None = None
        self.total_jobs: int = 0
        self.jobs: list[JobStatus] = []
        self._clip_to_job: dict[str, JobStatus] = {}
        self.stats: dict[str, int] = {"ok": 0, "failed": 0, "broken_videos": 0}
        self.logs: deque[LogEntry] = deque(maxlen=500)
        self.log_filter: str = "ALL"

    def _load_initial_config(self) -> VideoGenConfig:
        if self.settings.is_remote:
            cached_cfg = self.settings.remote_cache_dir() / "config.json"
            if cached_cfg.is_file():
                try:
                    return VideoGenConfig.load_file(cached_cfg)
                except Exception:
                    pass
            return VideoGenConfig()

        cfg_path = self.settings.resolved_config_path()
        vg_dir = self.settings.resolved_video_gen_dir()

        candidates = [
            cfg_path,
            vg_dir / "config.json",
            vg_dir / "config.toml",
            vg_dir / "config.json.example",
        ]
        for cand in candidates:
            if cand.is_file():
                try:
                    return VideoGenConfig.load_file(cand)
                except Exception:
                    continue
        return VideoGenConfig()

    def add_log(self, level: str, message: str) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        self.logs.append(LogEntry(timestamp=now, level=level.upper(), message=message))

    def clear_run_state(self) -> None:
        self.total_jobs = 0
        self.jobs.clear()
        self._clip_to_job.clear()
        self.stats = {"ok": 0, "failed": 0, "broken_videos": 0}

    def find_job(self, name_or_stem: str | None) -> JobStatus | None:
        if not name_or_stem:
            return None
        target_stem = Path(name_or_stem).stem
        for j in self.jobs:
            if j.name == name_or_stem or j.stem == name_or_stem or j.stem == target_stem:
                return j
        new_job = JobStatus(name=name_or_stem, stem=target_stem)
        self.jobs.append(new_job)
        return new_job

    def apply_cli_event(self, ev: dict[str, Any]) -> None:
        """Обновляет прогресс карточек и счётчиков по JSON-событию от `video-gen --json-progress`."""
        kind = ev.get("event", "")

        if kind == "pipeline_started":
            self.clear_run_state()
            job_names = [str(x) for x in ev.get("jobs", [])]
            self.total_jobs = int(ev.get("total", len(job_names)))
            self.jobs = [JobStatus(name=n, stem=Path(n).stem) for n in job_names]
            self.add_log("INFO", f"Конвейер запущен: видео в очереди — {self.total_jobs}")

        elif kind == "transcribe_start":
            job = self.find_job(ev.get("job"))
            if job:
                job.stage = "Распознавание речи (Whisper)"
                job.transcribe_pct = 0.0

        elif kind == "transcribe_progress":
            job = self.find_job(ev.get("job"))
            if job:
                job.stage = "Распознавание речи (Whisper)"
                job.transcribe_pct = float(ev.get("pct", 0.0))

        elif kind == "transcribe_done":
            job = self.find_job(ev.get("job"))
            if job and not job.broken:
                job.transcribe_pct = 100.0
                if job.stage.startswith("Распознавание"):
                    job.stage = "Ожидание AI-анализа"

        elif kind == "analyze_start":
            job = self.find_job(ev.get("job"))
            if job:
                job.stage = "AI-анализ хайлайтов"

        elif kind == "analyze_done":
            job = self.find_job(ev.get("job"))
            clips_count = int(ev.get("clips", 0))
            titles = [str(t) for t in ev.get("titles", [])]
            if job:
                job.clips_total = clips_count
                job.clip_titles = titles
                for clip_file in titles:
                    self._clip_to_job[clip_file] = job
                if clips_count > 0:
                    job.stage = f"В очереди на рендер ({clips_count} кл.)"
                else:
                    job.stage = "Нет подходящих клипов"

        elif kind == "render_start":
            job = self.find_job(ev.get("job"))
            clip_name = str(ev.get("clip", ""))
            idx = int(ev.get("idx", 1))
            total = int(ev.get("total", 1))
            if job:
                job.clips_total = max(job.clips_total, total)
                job.current_clip = clip_name
                job.render_pct = 0.0
                job.stage = f"Рендер клипа {idx}/{total} ({clip_name})"
                if clip_name:
                    self._clip_to_job[clip_name] = job

        elif kind == "render_progress":
            clip_name = str(ev.get("clip", ""))
            pct = float(ev.get("pct", 0.0))
            job = self._clip_to_job.get(clip_name)
            if job:
                job.current_clip = clip_name
                job.render_pct = pct

        elif kind == "render_done":
            clip_name = str(ev.get("clip", ""))
            success = bool(ev.get("success", True))
            job = self._clip_to_job.get(clip_name)
            if job:
                if success:
                    job.clips_done += 1
                    job.render_pct = 100.0
                if job.clips_total and job.clips_done >= job.clips_total:
                    job.finished = True
                    job.stage = f"Готово ({job.clips_done}/{job.clips_total} кл.)"

        elif kind == "video_done":
            job = self.find_job(ev.get("job"))
            broken = bool(ev.get("broken", False))
            if job:
                job.broken = broken
                job.finished = True
                job.stage = "Ошибка обработки" if broken else f"Готово ({job.clips_done} кл.)"

        elif kind == "pipeline_finished":
            self.stats["ok"] = int(ev.get("ok", self.stats["ok"]))
            self.stats["failed"] = int(ev.get("failed", self.stats["failed"]))
            self.stats["broken_videos"] = int(ev.get("broken_videos", self.stats["broken_videos"]))
            self.add_log(
                "INFO",
                f"Итог конвейера: клипов готово {self.stats['ok']}, ошибок рендера {self.stats['failed']}, проблемных видео {self.stats['broken_videos']}",
            )

        elif kind == "log":
            self.add_log(str(ev.get("level", "INFO")), str(ev.get("message", "")))

        elif kind == "error":
            job_name = ev.get("job")
            err = str(ev.get("error", "Неизвестная ошибка"))
            prefix = f"[{job_name}] " if job_name else ""
            self.add_log("ERROR", f"{prefix}{err}")
