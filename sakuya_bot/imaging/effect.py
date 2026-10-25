"""Эффект для команды с картинкой: как отрисовать один кадр, и общий запуск для картинок и анимаций."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .media import ANIMATION_SIDE, encode_gif, read_media
from .template import Template, load_template


@dataclass(frozen=True)
class ImageEffect:
    name: str  # имя команды, для логов
    template_file: Path
    render: Callable[[Image.Image, Template], np.ndarray]  # кадр -> RGB 0..255 размером с шаблон
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
        result = effect.encode_still(effect.render(media.frames[0], load_template(effect.template_file)))
        return EffectResult(result, effect.still_name, "картинка")
    # для анимации шаблон уменьшен: кадров много, а большой размер GIF всё равно не влезет в лимит
    template = load_template(effect.template_file, ANIMATION_SIDE)
    frames = [Image.fromarray(np.clip(effect.render(f, template), 0, 255).astype(np.uint8)) for f in media.frames]
    gif = encode_gif(frames, media.durations, size_limit)
    taken = sum(media.durations)
    cut = f" из {media.total_ms / 1000:.1f} с" if media.total_ms > taken + 50 else ""
    info = f"{media.source}: {len(frames)} кадров, {taken / 1000:.1f} с{cut}, {len(gif) // 1024} КБ"
    return EffectResult(gif, Path(effect.still_name).stem + ".gif", info)


def warm_up(effect: ImageEffect) -> None:
    """Готовит всё, что зависит только от шаблона (вырез, освещение, геометрию), для картинок и для анимаций."""
    blank = Image.new("RGBA" if effect.keep_alpha else "RGB", (8, 8))
    for template in (load_template(effect.template_file), load_template(effect.template_file, ANIMATION_SIDE)):
        effect.render(blank, template)
