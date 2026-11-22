"""Модели конфигурации video-gen, настройки GUI и утилиты конвертации цветов."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ASS_COLOR_RE = re.compile(r"^&H([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})([0-9A-Fa-f]{2})$")
HEX_COLOR_RE = re.compile(r"^#?([0-9A-Fa-f]{6})$")


def ass_to_hex(ass_color: str) -> tuple[str, int]:
    """Преобразует цвет ASS (&HAABBGGRR) в (#RRGGBB, alpha_byte 0..255).

    В формате ASS alpha 00 = полностью непрозрачный, FF = полностью прозрачный.
    """
    m = ASS_COLOR_RE.match(ass_color.strip())
    if not m:
        return "#FFFFFF", 0
    aa, bb, gg, rr = m.groups()
    return f"#{rr.upper()}{gg.upper()}{bb.upper()}", int(aa, 16)


def hex_to_ass(hex_color: str, alpha_byte: int = 0) -> str:
    """Преобразует #RRGGBB и alpha_byte (0=непрозрачный..255=прозрачный) в &HAABBGGRR."""
    m = HEX_COLOR_RE.match(hex_color.strip())
    if not m:
        raise ValueError(f"Некорректный HEX-цвет: {hex_color} (ожидается #RRGGBB)")
    raw = m.group(1).upper()
    rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
    clamped_alpha = max(0, min(255, int(alpha_byte)))
    return f"&H{clamped_alpha:02X}{bb}{gg}{rr}"


def _clean_optional_str(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


class VideoSourceOverrideRender(BaseModel):
    """Опциональные локальные переопределения рендера для конкретного источника."""

    model_config = ConfigDict(extra="ignore")

    face_tracking: bool | None = None
    mirror: bool | None = None
    top_zoom: float | None = Field(default=None, ge=1.0, le=3.0)


class VideoSourceConfig(BaseModel):
    """Элемент списка top_videos или bottom_videos."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["video", "pygame", "youtube"] = "video"
    source: str = "input/"
    caption: str | None = None
    caption_name: str | None = None
    codename: str | None = None
    output_dir: str | None = None
    source_number: int | None = None
    mode: Literal["highlights", "speech_split"] | None = None
    is_film: bool | None = None
    base_code: str | None = None
    season: int | None = None
    episode: int | None = None
    part_duration: int | None = Field(default=None, ge=10, le=180)
    min_part_duration: int | None = Field(default=None, ge=5, le=170)
    parts_count: int | None = Field(default=None, ge=1, le=20)
    render: VideoSourceOverrideRender | None = None

    @field_validator("caption", "caption_name", "codename", "output_dir", "base_code", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: Any) -> str | None:
        return _clean_optional_str(v)

    @model_validator(mode="after")
    def check_durations(self) -> Self:
        if (
            self.min_part_duration is not None
            and self.part_duration is not None
            and self.min_part_duration >= self.part_duration
        ):
            raise ValueError(
                f"min_part_duration ({self.min_part_duration}) должен быть меньше part_duration ({self.part_duration})"
            )
        return self


class VideosSection(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    top_videos: list[VideoSourceConfig] = Field(
        default_factory=lambda: [VideoSourceConfig(type="video", source="input/")]
    )
    bottom_videos: list[VideoSourceConfig] = Field(
        default_factory=lambda: [VideoSourceConfig(type="video", source="bottom/")],
    )

    @model_validator(mode="before")
    @classmethod
    def support_buttom_typo(cls, data: Any) -> Any:
        if isinstance(data, dict) and "buttom_videos" in data and "bottom_videos" not in data:
            data = {**data, "bottom_videos": data["buttom_videos"]}
        return data

    @field_validator("top_videos")
    @classmethod
    def validate_top_videos(cls, v: list[VideoSourceConfig]) -> list[VideoSourceConfig]:
        if not v:
            raise ValueError("Список top_videos не может быть пустым")
        for item in v:
            if item.type not in ("video", "youtube"):
                raise ValueError('В top_videos поддерживаются только типы "video" и "youtube"')
        return v

    @field_validator("bottom_videos")
    @classmethod
    def validate_bottom_videos(cls, v: list[VideoSourceConfig]) -> list[VideoSourceConfig]:
        if not v:
            raise ValueError("Список bottom_videos не может быть пустым")
        for item in v:
            if item.type not in ("video", "pygame"):
                raise ValueError('В bottom_videos поддерживаются только типы "video" и "pygame"')
        return v


class ParamsSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    output_dir: str = "output/"

    # Transcription
    transcribe_backend: Literal["local", "groq", "openai"] = "local"
    transcribe_api_key: str = ""
    transcribe_api_base: str = ""
    transcribe_model: str = "whisper-large-v3"

    # AI Analysis
    analysis_backend: Literal["openai", "groq", "custom"] = "openai"
    analysis_api_key: str = ""
    analysis_api_base: str = "https://api.openai.com/v1"
    analysis_model: str = "gpt-4o-mini"

    # Pipeline tuning
    max_retries: int = Field(default=3, ge=1, le=10)
    retry_delay: float = Field(default=2.0, ge=0.5, le=30.0)
    concurrent_renders: int = Field(default=1, ge=1, le=4)

    # Legacy Gemini compatibility
    gemini_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    # Clipping rules
    parts_count: int = Field(default=5, ge=1, le=20)
    min_part_duration: int = Field(default=20, ge=5, le=170)
    part_duration: int = Field(default=60, ge=10, le=180)
    silence_gap_threshold: float = Field(default=7.0, ge=1.0, le=60.0)

    # Local Whisper
    whisper_model: str = "large-v3"
    whisper_device: Literal["cuda", "cpu", "auto"] = "cuda"
    whisper_compute_type: Literal["float16", "int8_float16", "int8", "float32"] = "float16"
    beam_size: int = Field(default=5, ge=1, le=10)
    language: str = Field(default="ru", min_length=2, max_length=5)

    # Mode & SponsorBlock
    mode: Literal["highlights", "speech_split"] = "highlights"
    sponsorblock_enabled: bool = True
    sponsorblock_categories: list[str] = Field(
        default_factory=lambda: ["sponsor", "selfpromo", "interaction", "intro", "outro"]
    )

    # Caches
    use_whisper_cache: bool = True
    use_gemini_cache: bool = True
    use_render_cache: bool = True

    @model_validator(mode="after")
    def validate_durations(self) -> Self:
        if self.min_part_duration >= self.part_duration:
            raise ValueError(
                f"Мин. длительность ({self.min_part_duration} с) должна быть меньше макс. ({self.part_duration} с)"
            )
        return self


class AssetsSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    banner: str | None = None
    banners_dir: str | None = None
    music_dir: str | None = None
    fonts_dir: str = "fonts/"

    @field_validator("banner", "banners_dir", "music_dir", mode="before")
    @classmethod
    def clean_empty_paths(cls, v: Any) -> str | None:
        return _clean_optional_str(v)


class RenderSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    width: int = Field(default=1080, ge=360, le=4096)
    height: int = Field(default=1920, ge=640, le=4096)
    fps: int = Field(default=30, ge=15, le=60)
    top_parts: int = Field(default=1, ge=1, le=10)
    bottom_parts: int = Field(default=2, ge=1, le=10)
    mirror: bool = True
    top_zoom: float = Field(default=1.0, ge=1.0, le=3.0)
    top_x_offset: float = Field(default=0.0, ge=-1.0, le=1.0)
    top_y_offset: float = Field(default=0.0, ge=-1.0, le=1.0)
    contrast: float = Field(default=1.0, ge=0.5, le=2.0)
    saturation: float = Field(default=1.0, ge=0.0, le=3.0)
    brightness: float = Field(default=0.0, ge=-1.0, le=1.0)
    gamma: float = Field(default=1.0, ge=0.1, le=3.0)
    bottom_contrast: float = Field(default=1.10, ge=0.5, le=2.0)
    bottom_saturation: float = Field(default=1.25, ge=0.0, le=3.0)
    bottom_brightness: float = Field(default=0.01, ge=-0.3, le=0.3)
    bottom_gamma: float = Field(default=1.02, ge=0.5, le=2.0)
    noise_top: int = Field(default=0, ge=0, le=100)
    noise_bottom: int = Field(default=0, ge=0, le=100)
    bottom_mode: Literal["random", "sequential"] = "random"
    blur_sigma: float = Field(default=20.0, ge=0.0, le=100.0)
    blur_in: float = Field(default=0.5, ge=0.0, le=5.0)
    blur_out: float = Field(default=0.3, ge=0.0, le=5.0)
    banner_width: int = Field(default=800, ge=100, le=4096)
    banner_chromakey: bool = False
    banner_chroma_color: str = "green"
    banner_chroma_similarity: float = Field(default=0.3, ge=0.01, le=1.0)
    banner_chroma_blend: float = Field(default=0.1, ge=0.0, le=1.0)
    banner_autocrop: bool = False
    banner_autocrop_threshold: int = Field(default=10, ge=0, le=255)
    banner_mode: Literal["center", "overlay", "end", "interval_minutes", "interval_percent"] = (
        "center"
    )
    banner_interval_minutes: float = Field(default=1.0, gt=0.0, le=180.0)
    banner_interval_percent: float = Field(default=25.0, gt=0.0, le=100.0)
    banner_freeze: bool = True
    banner_anchor: Literal["top-left", "top-right", "bottom-left", "bottom-right", "center"] = (
        "center"
    )
    banner_x: int | None = None
    banner_y: int | None = None
    banner_opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    banner_scale: float = Field(default=1.0, ge=0.1, le=5.0)
    duck_volume: float = Field(default=0.10, ge=0.0, le=1.0)
    music_volume: float = Field(default=0.07, ge=0.0, le=1.0)
    speech_volume: float = Field(default=1.0, ge=0.1, le=3.0)
    audio_fade_in: float = Field(default=0.3, ge=0.0, le=3.0)
    audio_fade_out: float = Field(default=0.5, ge=0.0, le=3.0)
    cq: int = Field(default=28, ge=1, le=51)
    maxrate: str = "6M"
    bufsize: str = "12M"
    strip_metadata: bool = True
    enable_antidetect: bool = True
    speed_jitter: bool = True
    speed_jitter_min: float = Field(default=0.985, ge=0.9, le=1.0)
    speed_jitter_max: float = Field(default=1.015, ge=1.0, le=1.1)
    face_tracking: bool = True
    sharpen: bool = True
    unsharp_luma: float = Field(default=0.8, ge=0.0, le=2.0)
    unsharp_chroma: float = Field(default=0.4, ge=0.0, le=2.0)
    video_codec: Literal["h264_nvenc", "libx264", "h265_nvenc", "libx265"] = "h264_nvenc"
    audio_codec: Literal["aac", "mp3", "copy"] = "aac"
    audio_bitrate: str = "192k"
    preset: str = "p4"
    enable_canvas_blur: bool = False
    boxblur_radius: float = Field(default=20.0, ge=1.0, le=100.0)
    boxblur_power: int = Field(default=2, ge=1, le=5)
    canvas_bg_color: str = "black"
    top_offset_y: int = 0
    bottom_offset_y: int = 0
    top_padding: int = 0
    bottom_padding: int = 0


class SubtitlesSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    font: str = "Unbounded"
    font_size: int = Field(default=78, ge=20, le=300)
    bold: bool = False
    italic: bool = False
    fill_color: str = "&H00FFFFFF"
    spoken_color: str = "&H0000FFFF"
    unspoken_color: str = "&H00FFFFFF"
    outline_color: str = "&H00000000"
    outline: int = Field(default=6, ge=0, le=20)
    shadow: int = Field(default=3, ge=0, le=20)
    pop_scale: float = Field(default=1.15, ge=1.0, le=2.0)
    pop_duration: float = Field(default=0.08, ge=0.01, le=0.5)
    fade_in: bool = False
    fade_out: bool = False
    word_spacing: int = Field(default=0, ge=-20, le=50)
    line_spacing: float = Field(default=1.0, ge=0.5, le=3.0)
    alignment: int = Field(default=2, ge=1, le=9)
    position: Literal["bottom", "top", "middle"] = "bottom"
    margin_v: int = Field(default=0, ge=0)
    margin_h: int = Field(default=60, ge=0)
    uppercase: bool = True
    animation: Literal["pop", "bounce", "none"] = "pop"
    pop_animation: bool = True
    seam_offset: int = 0
    max_words: int = Field(default=3, ge=1, le=8)
    max_chars: int = Field(default=24, ge=8, le=60)
    max_gap: float = Field(default=0.6, ge=0.1, le=3.0)
    hold: float = Field(default=0.2, ge=0.0, le=2.0)

    @field_validator("fill_color", "spoken_color", "unspoken_color", "outline_color")
    @classmethod
    def check_ass_color(cls, v: str) -> str:
        s = v.strip().upper()
        if not ASS_COLOR_RE.match(s):
            raise ValueError(f"Цвет должен быть в формате &HAABBGGRR, получено: {v}")
        return s


class DatabaseSection(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=5432, ge=1, le=65535)
    user: str = "videogen"
    password: str = ""
    database: str = "videogen_db"
    min_connections: int = Field(default=1, ge=1, le=50)
    max_connections: int = Field(default=10, ge=1, le=100)
    timeout: float = Field(default=15.0, ge=1.0, le=120.0)


class VideoGenConfig(BaseModel):
    """Полная модель config.json для передачи в CLI video-gen (совместима с Strict-схемой)."""

    model_config = ConfigDict(extra="ignore")

    videos: VideosSection = Field(default_factory=VideosSection)
    params: ParamsSection = Field(default_factory=ParamsSection)
    assets: AssetsSection = Field(default_factory=AssetsSection)
    render: RenderSection = Field(default_factory=RenderSection)
    subtitles: SubtitlesSection = Field(default_factory=SubtitlesSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)

    def to_cli_dict(self) -> dict[str, Any]:
        """Сериализует конфиг в чистый словарь без None для совместимости с Strict-моделью video-gen."""
        return self.model_dump(mode="json", exclude_none=True)

    def to_json_text(self) -> str:
        """Возвращает форматированный JSON для записи в config.json."""
        return json.dumps(self.to_cli_dict(), ensure_ascii=False, indent=2) + "\n"

    def save_json(self, path: str | Path) -> Path:
        """Сохраняет валидированный config.json на диск."""
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_json_text(), encoding="utf-8")
        return target

    @classmethod
    def load_file(cls, path: str | Path) -> VideoGenConfig:
        """Загружает конфигурацию из .json или импортирует из legacy .toml."""
        file_path = Path(path)
        raw_text = file_path.read_text(encoding="utf-8")
        if file_path.suffix.lower() == ".toml":
            data = tomllib.loads(raw_text)
            # Если в старом TOML задан gemini_key, но не настроены analysis_api_*, переносим мягко
            params = data.get("params")
            if isinstance(params, dict) and params.get("gemini_key") and not params.get("analysis_api_key"):
                params["analysis_backend"] = "custom"
                params["analysis_api_key"] = params["gemini_key"]
                params["analysis_api_base"] = "https://generativelanguage.googleapis.com/v1beta/openai/"
                if params.get("gemini_model"):
                    params["analysis_model"] = params["gemini_model"]
            return cls.model_validate(data)
        return cls.model_validate(json.loads(raw_text))

    def preflight_warnings(self, video_gen_dir: Path) -> list[str]:
        """Проверяет существование локальных файлов/папок относительно video_gen_dir перед стартом CLI."""
        warnings: list[str] = []

        def _resolve(p_str: str) -> Path:
            p = Path(p_str)
            return p if p.is_absolute() else (video_gen_dir / p)

        for idx, src in enumerate(self.videos.top_videos, 1):
            if src.type == "video" and not _resolve(src.source).exists():
                warnings.append(f"Top-источник #{idx} не найден: {_resolve(src.source)}")

        for idx, src in enumerate(self.videos.bottom_videos, 1):
            if src.type == "video" and not _resolve(src.source).exists():
                warnings.append(f"Bottom-источник #{idx} не найден: {_resolve(src.source)}")

        if self.assets.banner and not _resolve(self.assets.banner).is_file():
            warnings.append(f"Файл баннера не найден (CLI отклонит конфиг): {_resolve(self.assets.banner)}")
        if self.assets.banners_dir and not _resolve(self.assets.banners_dir).is_dir():
            warnings.append(
                f"Папка баннеров не найдена (CLI отклонит конфиг): {_resolve(self.assets.banners_dir)}"
            )
        if self.assets.music_dir and not _resolve(self.assets.music_dir).is_dir():
            warnings.append(
                f"Папка музыки не найдена (CLI отклонит конфиг): {_resolve(self.assets.music_dir)}"
            )
        return warnings


def default_video_gen_dir() -> Path:
    """Автоматически ищет папку video-gen по соседству с проектом GUI."""
    repo_root = Path(__file__).resolve().parents[2]
    sibling = repo_root.parent / "video-gen"
    if sibling.is_dir():
        return sibling
    home_cand = Path.home() / "projects" / "video-gen"
    if home_cand.is_dir():
        return home_cand
    return sibling


class GuiSettings(BaseModel):
    """Локальные настройки самого GUI-приложения (не передаются в video-gen)."""

    video_gen_dir: str = Field(default_factory=lambda: str(default_video_gen_dir()))
    config_path: str = ""
    theme_mode: Literal["dark", "light"] = "dark"
    auto_sync_before_run: bool = True

    # Удалённый режим выполнения по SSH
    execution_mode: Literal["local", "ssh"] = "local"
    ssh_host: str = ""
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = ""
    ssh_key_path: str = ""
    remote_video_gen_dir: str = "~/projects/video-gen"
    remote_config_path: str = ""

    @property
    def is_remote(self) -> bool:
        return self.execution_mode == "ssh"

    def ssh_destination(self) -> str:
        host = self.ssh_host.strip()
        if not host:
            return ""
        user = self.ssh_user.strip()
        if user and "@" not in host:
            return f"{user}@{host}"
        return host

    def resolved_remote_video_gen_dir(self) -> str:
        return self.remote_video_gen_dir.strip() or "~/projects/video-gen"

    def resolved_remote_config_path(self) -> str:
        raw = self.remote_config_path.strip()
        base = self.resolved_remote_video_gen_dir().rstrip("/") or "/"
        if raw:
            if raw.startswith("/") or raw.startswith("~/") or raw == "~":
                return raw
            return f"{base}/{raw.lstrip('/')}"
        return f"{base}/config.json"

    def remote_cache_dir(self) -> Path:
        dest = self.ssh_destination() or "default"
        raw_slug = f"{dest}_{self.ssh_port}" if self.ssh_port != 22 else dest
        safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "_", raw_slug).strip("._-") or "default"
        cache_dir = Path.home() / ".cache" / "video-gen-gui" / "remote" / safe_slug
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def resolved_video_gen_dir(self) -> Path:
        return Path(self.video_gen_dir).expanduser().resolve()

    def resolved_config_path(self) -> Path:
        if self.config_path.strip():
            p = Path(self.config_path).expanduser()
            return p if p.is_absolute() else (self.resolved_video_gen_dir() / p)
        return self.resolved_video_gen_dir() / "config.json"

    @classmethod
    def settings_file_path(cls) -> Path:
        cfg_home = Path.home() / ".config" / "video-gen-gui"
        cfg_home.mkdir(parents=True, exist_ok=True)
        return cfg_home / "settings.json"

    @classmethod
    def load(cls) -> GuiSettings:
        path = cls.settings_file_path()
        if path.is_file():
            try:
                return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                pass
        settings = cls()
        settings.config_path = str(settings.resolved_video_gen_dir() / "config.json")
        return settings

    def save(self) -> None:
        path = self.settings_file_path()
        path.write_text(json.dumps(self.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
