"""Эффект для команды с картинкой: как отрисовать один кадр, и общий запуск для картинок и анимаций."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .media import ANIMATION_SIDE, encode_gif, read_media
from .template import Template, load_template

STILL_SIDE = 1280  # большая сторона результата-картинки у эффектов без шаблона


@dataclass(frozen=True)
class ImageEffect:
    name: str  # имя команды, для логов
    template_file: Path | None  # None — эффект без шаблона: меняет саму картинку (например, !explode)
    # кадр -> RGB 0..255 размером с шаблон; без шаблона template=None, а результат — размером с кадр
    render: Callable[[Image.Image, Template | None], np.ndarray]
    keep_alpha: bool  # нужна ли эффекту прозрачность кадра (пиву — да, кофе — нет)
    encode_still: Callable[[np.ndarray], bytes]  # как сохранить обычную картинку (PNG / JPEG)
    still_name: str  # имя файла для картинки, например coffee.png


@dataclass
class EffectResult:
    data: bytes
    filename: str
    info: str  # для логов: что обработали


def run_effect(effect: ImageEffect, data: bytes, size_limit: int) -> EffectResult:
    """Картинка -> картинка; GIF, анимированный PNG/WebP или видео -> GIF не больше size_limit байт."""
    media = read_media(data, effect.keep_alpha)
    if not media.animated:
        frame, template = media.frames[0], _template(effect)
        if template is None:
            frame = _fit(frame, STILL_SIDE)
        result = effect.encode_still(effect.render(frame, template))
        return EffectResult(result, effect.still_name, "картинка")
    # для анимации шаблон уменьшен: кадров много, а большой размер GIF всё равно не влезет в лимит
    template = _template(effect, ANIMATION_SIDE)
    source = media.frames if template is not None else [_fit(f, ANIMATION_SIDE) for f in media.frames]
    frames = [Image.fromarray(np.clip(effect.render(f, template), 0, 255).astype(np.uint8)) for f in source]
    gif = encode_gif(frames, media.durations, size_limit)
    taken = sum(media.durations)
    cut = f" из {media.total_ms / 1000:.1f} с" if media.total_ms > taken + 50 else ""
    info = f"{media.source}: {len(frames)} кадров, {taken / 1000:.1f} с{cut}, {len(gif) // 1024} КБ"
    return EffectResult(gif, Path(effect.still_name).stem + ".gif", info)


def _template(effect: ImageEffect, max_side: int | None = None) -> Template | None:
    return load_template(effect.template_file, max_side) if effect.template_file else None


def _fit(frame: Image.Image, side: int) -> Image.Image:
    """Эффекты без шаблона работают в размере самой картинки: уменьшаем её, чтобы обработка и файл не были огромными."""
    if max(frame.size) > side:
        frame = frame.copy()
        frame.thumbnail((side, side), Image.Resampling.LANCZOS)
    return frame


def warm_up(effect: ImageEffect) -> None:
    """Готовит всё, что зависит только от шаблона (вырез, освещение, геометрию), для картинок и для анимаций."""
    if effect.template_file is None:
        return
    blank = Image.new("RGBA" if effect.keep_alpha else "RGB", (8, 8))
    for template in (load_template(effect.template_file), load_template(effect.template_file, ANIMATION_SIDE)):
        effect.render(blank, template)
