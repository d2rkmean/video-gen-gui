"""Поиск и валидация шрифтов (.ttf/.otf) и рендер живого превью караоке-субтитров через Pillow."""

from __future__ import annotations

import contextlib
import io
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from video_gen_gui.models import SubtitlesSection, ass_to_hex

FONT_EXTENSIONS = {".ttf", ".otf"}
type _PilFont = ImageFont.FreeTypeFont | ImageFont.ImageFont


def _style_rank(style: str | None, stem: str) -> int:
    """Приоритизирует жирные/тяжёлые начертания (Black/ExtraBold/Bold) для караоке-субтитров."""
    def _score_text(raw: str) -> int:
        norm = raw.lower().replace("-", " ").replace("_", " ")
        if "black" in norm:
            return 90
        if "heavy" in norm:
            return 85
        if "extrabold" in norm or "extra bold" in norm or "ultrabold" in norm:
            return 80
        if "semibold" in norm or "semi bold" in norm or "demibold" in norm or "demi bold" in norm:
            return 60
        if "bold" in norm:
            return 70
        if "medium" in norm:
            return 50
        if "regular" in norm or "normal" in norm or "book" in norm:
            return 40
        if "light" in norm:
            return 30
        if "thin" in norm or "hairline" in norm:
            return 20
        return 0

    stem_score = _score_text(stem)
    style_score = _score_text(style or "")
    # Имя файла (например Montserrat-Black) надёжнее для вариативных шрифтов, где getname() даёт Thin/Light
    if stem_score > 0:
        return stem_score
    if style_score > 0:
        return style_score
    return 35


def discover_fonts(
    fonts_dir: Path | None,
    current_font: str = "",
) -> tuple[list[str], dict[str, Path]]:
    """Сканирует `fonts_dir` на валидные .ttf/.otf и строит `(display_families, font_map)`."""
    font_map: dict[str, Path] = {}
    key_ranks: dict[str, int] = {}
    families: set[str] = set()

    def _register(key: str, path: Path, rank: int, *, force: bool = False) -> None:
        clean = key.strip().lower()
        if not clean:
            return
        prev = key_ranks.get(clean, -1)
        if force or rank > prev:
            font_map[clean] = path
            key_ranks[clean] = rank

    if fonts_dir is not None and fonts_dir.is_dir():
        candidates = sorted(
            (
                p
                for p in fonts_dir.iterdir()
                if p.is_file() and p.suffix.lower() in FONT_EXTENSIONS
            ),
            key=lambda p: p.name.lower(),
        )
        for p in candidates:
            try:
                if p.stat().st_size < 100:
                    continue
                font_obj = ImageFont.truetype(str(p), 24)
                family_raw, style_raw = font_obj.getname()
            except Exception:
                continue

            stem_base = p.stem.split("-")[0].split("_")[0].strip()
            family = (family_raw or stem_base or p.stem).strip()
            rank = _style_rank(style_raw, p.stem)

            families.add(family)
            _register(family, p, rank)
            _register(family.replace(" ", ""), p, rank)
            _register(p.stem, p, rank, force=True)
            if stem_base:
                _register(stem_base, p, rank)

    cur = current_font.strip()
    if cur:
        families.add(cur)

    sorted_families = sorted(families, key=str.casefold)
    return sorted_families, font_map


@lru_cache(maxsize=64)
def _fc_match_font(family: str) -> Path | None:
    """Ищет системный шрифт через `fc-match`, если пользователь указал системное семейство."""
    clean = family.strip()
    if not clean:
        return None
    fc_bin = shutil.which("fc-match")
    if not fc_bin:
        return None
    try:
        res = subprocess.run(
            [fc_bin, "-f", "%{file}", clean],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
    except Exception:
        return None
    if res.returncode != 0:
        return None
    candidate_str = res.stdout.strip()
    if not candidate_str:
        return None
    cand = Path(candidate_str)
    if not cand.is_file():
        return None
    try:
        ImageFont.truetype(str(cand), 24)
    except Exception:
        return None
    return cand


def resolve_font_path(font_name: str, font_map: dict[str, Path]) -> Path | None:
    """Разрешает имя шрифта в путь `.ttf`/`.otf` по `font_map` или системному `fc-match`."""
    clean = font_name.strip().lower()
    if not clean:
        return None

    for key in (clean, clean.replace(" ", "")):
        if key in font_map and font_map[key].is_file():
            return font_map[key]

    return _fc_match_font(font_name.strip())


def _ass_to_rgba(ass_color: str) -> tuple[int, int, int, int]:
    """Преобразует &HAABBGGRR (где AA=00 непрозрачный) в (R, G, B, A)."""
    hx, alpha_byte = ass_to_hex(ass_color)
    r = int(hx[1:3], 16)
    g = int(hx[3:5], 16)
    b = int(hx[5:7], 16)
    a = max(0, min(255, 255 - alpha_byte))
    return (r, g, b, a)


def _apply_variable_weight(font: ImageFont.FreeTypeFont, bold: bool) -> None:
    """Для вариативных шрифтов (wght/Weight) задаёт Bold/Black вес вместо Thin/Light."""
    try:
        axes = font.get_variation_axes()
    except Exception:
        return
    if not axes:
        return

    target_weight = 900.0 if bold else 700.0
    coords: list[float] = []
    has_weight = False
    for ax in axes:
        name_raw = ax.get("name", b"")
        name = (
            name_raw.decode("utf-8", errors="ignore").lower()
            if isinstance(name_raw, bytes)
            else str(name_raw).lower()
        )
        raw_min = ax.get("minimum")
        raw_max = ax.get("maximum")
        raw_def = ax.get("default")
        minimum = float(raw_min) if raw_min is not None else 100.0
        maximum = float(raw_max) if raw_max is not None else 900.0
        default = float(raw_def) if raw_def is not None else minimum
        if "weight" in name or name == "wght":
            has_weight = True
            coords.append(max(minimum, min(maximum, target_weight)))
        else:
            coords.append(max(minimum, min(maximum, default)))

    if has_weight:
        with contextlib.suppress(Exception):
            font.set_variation_by_axes(coords)


def _load_pil_font(
    font_path: Path | None,
    size_px: int,
    *,
    bold: bool,
) -> tuple[_PilFont, str]:
    px = max(10, int(size_px))
    if font_path is not None:
        try:
            f = ImageFont.truetype(str(font_path), px)
            _apply_variable_weight(f, bold)
            return f, font_path.name
        except Exception:
            pass
    try:
        fallback = ImageFont.load_default(size=px)
    except TypeError:
        fallback = ImageFont.load_default()
    return fallback, "system fallback"


def _draw_dashed_hline(
    draw: ImageDraw.ImageDraw,
    y: int,
    width: int,
    color: tuple[int, int, int, int],
) -> None:
    x = 12
    while x < width - 12:
        x_end = min(width - 12, x + 8)
        draw.line([(x, y), (x_end, y)], fill=color, width=1)
        x += 14


def render_subtitle_preview_png(
    subtitles: SubtitlesSection,
    font_map: dict[str, Path],
    *,
    sample_text: str = "ЭТОТ МОМЕНТ РЕШАЕТ",
    width: int = 860,
    height: int = 168,
    top_parts: int = 1,
    bottom_parts: int = 2,
) -> bytes:
    """Рендерит PNG-превью караоке-субтитров в натуральном виде (с учётом вариативных шрифтов)."""
    canvas = Image.new("RGBA", (width, height), (17, 19, 24, 255))
    draw = ImageDraw.Draw(canvas)

    # Техническая разметка зон верх/низ и линии стыка
    total_parts = max(1, top_parts + bottom_parts)
    if subtitles.position == "middle":
        seam_y = height // 2 + round(subtitles.seam_offset * 0.12)
        seam_y = max(28, min(height - 28, seam_y))
        draw.rectangle([(0, 0), (width, seam_y)], fill=(23, 27, 36, 255))
        draw.rectangle([(0, seam_y), (width, height)], fill=(15, 17, 22, 255))
        _draw_dashed_hline(draw, seam_y, width, (62, 74, 98, 255))
    elif subtitles.position == "top":
        seam_y = max(32, min(height - 28, round(height * (top_parts / total_parts) + 28)))
        draw.rectangle([(0, 0), (width, seam_y)], fill=(23, 27, 36, 255))
        _draw_dashed_hline(draw, seam_y, width, (54, 64, 86, 255))
    else:
        seam_y = max(24, min(height - 40, round(height * (top_parts / total_parts) * 0.7)))
        draw.rectangle([(0, seam_y), (width, height)], fill=(14, 16, 21, 255))
        _draw_dashed_hline(draw, seam_y, width, (54, 64, 86, 255))

    if not subtitles.enabled:
        muted_font, _ = _load_pil_font(resolve_font_path(subtitles.font, font_map), 20, bold=False)
        draw.text(
            (width // 2, height // 2),
            "СУБТИТРЫ ОТКЛЮЧЕНЫ (subtitles.enabled = false)",
            font=muted_font,
            fill=(136, 146, 166, 255),
            anchor="mm",
        )
        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()

    matched_path = resolve_font_path(subtitles.font, font_map)
    text_raw = sample_text.upper() if subtitles.uppercase else sample_text
    words = [w for w in text_raw.split() if w] or ["МОМЕНТ"]
    active_idx = len(words) // 2

    pop_factor = (
        subtitles.pop_scale
        if (subtitles.pop_animation and subtitles.animation != "none")
        else 1.0
    )
    bounce_shift = -6 if (subtitles.pop_animation and subtitles.animation == "bounce") else 0

    # Масштабируем с 1080px видео на ширину превью-холста, затем поджимаем, если строка шире холста
    scale = 0.50 * (width / 860.0)
    base_px = max(14, round(subtitles.font_size * scale))
    pop_px = max(14, round(base_px * pop_factor))
    gap_px = max(4, round((base_px * 0.34) + (subtitles.word_spacing * scale)))

    font_base, matched_label = _load_pil_font(matched_path, base_px, bold=subtitles.bold)
    font_pop, _ = _load_pil_font(matched_path, pop_px, bold=subtitles.bold)

    def _measure_line(f_b: _PilFont, f_p: _PilFont, g: int) -> tuple[float, list[float]]:
        widths: list[float] = []
        for idx, word in enumerate(words):
            f_use = f_p if idx == active_idx else f_b
            widths.append(float(f_use.getlength(word)))
        total_w = sum(widths) + g * max(0, len(words) - 1)
        return total_w, widths

    total_w, word_widths = _measure_line(font_base, font_pop, gap_px)
    max_allowed_w = float(width - 48)
    if total_w > max_allowed_w and total_w > 0:
        shrink = max_allowed_w / total_w
        base_px = max(12, round(base_px * shrink))
        pop_px = max(12, round(pop_px * shrink))
        gap_px = max(3, round(gap_px * shrink))
        scale *= shrink
        font_base, matched_label = _load_pil_font(matched_path, base_px, bold=subtitles.bold)
        font_pop, _ = _load_pil_font(matched_path, pop_px, bold=subtitles.bold)
        total_w, word_widths = _measure_line(font_base, font_pop, gap_px)

    spoken_rgba = _ass_to_rgba(subtitles.spoken_color)
    unspoken_rgba = _ass_to_rgba(subtitles.unspoken_color)
    outline_rgba = _ass_to_rgba(subtitles.outline_color)
    stroke_w = max(0, round(subtitles.outline * scale))
    shadow_px = max(0, round(subtitles.shadow * scale))

    if subtitles.position == "top":
        center_y = max(38, min(height - 46, round(height * 0.44)))
    elif subtitles.position == "bottom":
        center_y = max(38, min(height - 46, round(height * 0.54)))
    else:
        center_y = max(38, min(height - 46, seam_y))

    text_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)

    cur_x = (width - total_w) / 2.0
    for idx, word in enumerate(words):
        is_active = idx == active_idx
        f_use = font_pop if is_active else font_base
        fill_rgba = spoken_rgba if is_active else unspoken_rgba
        wy = center_y + (bounce_shift if is_active else 0)

        if shadow_px > 0:
            text_draw.text(
                (cur_x + shadow_px, wy + shadow_px),
                word,
                font=f_use,
                fill=outline_rgba,
                stroke_width=stroke_w,
                stroke_fill=outline_rgba,
                anchor="lm",
            )

        text_draw.text(
            (cur_x, wy),
            word,
            font=f_use,
            fill=fill_rgba,
            stroke_width=stroke_w,
            stroke_fill=outline_rgba,
            anchor="lm",
        )
        cur_x += word_widths[idx] + gap_px

    canvas = Image.alpha_composite(canvas, text_layer)
    footer_draw = ImageDraw.Draw(canvas)

    footer_font, _ = _load_pil_font(None, 12, bold=False)
    font_fam = subtitles.font.strip()
    if font_fam and font_fam.lower() not in matched_label.lower():
        footer_text = f"{matched_label} ({font_fam})  |  {subtitles.font_size}px"
    else:
        footer_text = f"{matched_label}  |  {subtitles.font_size}px"
    footer_draw.text(
        (width - 12, height - 10),
        footer_text,
        font=footer_font,
        fill=(139, 149, 167, 255),
        anchor="rd",
    )

    out = io.BytesIO()
    canvas.save(out, format="PNG")
    return out.getvalue()
