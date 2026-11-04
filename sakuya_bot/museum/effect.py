"""Музей: картинка в раме на стене музея. Только замена зелёного выреза на картинку, без обработки самой картинки."""

import numpy as np
from PIL import Image

from ..config import TEMPLATE_DIR
from ..imaging.effect import ImageEffect
from ..imaging.template import Template, to_jpeg

TEMPLATE_FILE = TEMPLATE_DIR / "museum.jpg"

# паспарту — чем заполняется место в раме, которое не заняла картинка (и прозрачный фон стикеров)
MAT_COLOR = (236, 230, 218)


def fit_inside(src: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Картинка целиком внутри рамки size («вписать»): ничего не обрезается, по краям остаётся паспарту."""
    w, h = size
    scale = min(w / src.width, h / src.height)
    fitted = src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (*MAT_COLOR, 255))
    canvas.alpha_composite(fitted.convert("RGBA"), ((w - fitted.width) // 2, (h - fitted.height) // 2))
    return canvas.convert("RGB")


def render(src: Image.Image, template: Template) -> np.ndarray:
    """Один кадр: картинка (RGBA) вписана в вырез шаблона. Возвращает RGB 0..255 размером с шаблон."""
    x0, y0, x1, y1 = template.bbox
    picture = template.rgb.copy()
    picture[y0:y1, x0:x1] = np.asarray(fit_inside(src, (x1 - x0, y1 - y0)), dtype=np.float32)
    # снаружи выреза (рама, стена, люди перед картиной) остаётся шаблон
    return template.composite(picture)


# описание эффекта для команды: картинка -> JPEG (шаблон — фото), анимация и видео -> GIF (imaging/effect.py)
EFFECT = ImageEffect("музей", TEMPLATE_FILE, render, keep_alpha=True, encode_still=to_jpeg, still_name="museum.jpg")
