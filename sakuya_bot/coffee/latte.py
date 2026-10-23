"""Латте-арт: вставляет картинку в шаблон чашки и стилизует её под рисунок пенкой на эспрессо."""

import numpy as np
from PIL import Image, ImageFilter, ImageOps

from ..config import TEMPLATE_DIR
from ..imaging.template import blur, load_template, open_image, to_float, to_png

TEMPLATE_FILE = TEMPLATE_DIR / "coffee.png"

# цвета кофе: тёмные места картинки — эспрессо, средние — крема, светлые — молочная пена
ESPRESSO = np.array([43, 23, 12], dtype=np.float32)
CREMA = np.array([138, 90, 51], dtype=np.float32)
FOAM = np.array([242, 228, 204], dtype=np.float32)


def latte_colors(src: Image.Image, size: tuple[int, int]) -> np.ndarray:
    """Картинка, подогнанная под size и раскрашенная в цвета кофе (RGB 0..255)."""
    w, h = size
    # «cover»: заполняем всю рамку выреза, лишнее обрезаем; центр чуть выше — лица обычно сверху
    fitted = ImageOps.fit(src, size, Image.Resampling.LANCZOS, centering=(0.5, 0.4))
    gray = ImageOps.autocontrast(ImageOps.grayscale(fitted), cutoff=2)
    gray = gray.filter(ImageFilter.GaussianBlur(max(w, h) * 0.004))  # мягкие края, как у пены
    t = to_float(gray)

    # S-кривая: пена и кофе разделяются чётче, как в настоящем латте-арте
    s = lambda x: 1 / (1 + np.exp(-(x - 0.5) * 5))
    t = (s(t) - s(0)) / (s(1) - s(0))

    # зернистая текстура пены
    noise = np.random.default_rng(42).normal(0.5, 0.5, (h, w))
    t = np.clip(t + (blur(noise, 1.2) - 0.5) * 0.12 * t, 0, 1)

    t = t[..., None]
    rgb = np.where(t < 0.5, ESPRESSO + (CREMA - ESPRESSO) * (t * 2), CREMA + (FOAM - CREMA) * (t * 2 - 1))

    # к стенкам чашки кофе темнее
    yy, xx = np.mgrid[0:h, 0:w]
    d = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    rgb *= (1 - 0.3 * np.clip((d - 0.55) / 0.45, 0, 1) ** 2)[..., None]
    return rgb


def make_latte(data: bytes) -> bytes:
    """Принимает байты картинки, возвращает PNG с латте-артом в чашке."""
    template, mask, (x0, y0, x1, y1) = load_template(TEMPLATE_FILE)
    coffee = template.copy()
    coffee[y0:y1, x0:x1] = latte_colors(open_image(data), (x1 - x0, y1 - y0))

    # тень от стенок чашки на поверхности кофе
    shadow = blur(1 - mask, 8) * mask
    coffee *= (1 - 0.6 * shadow)[..., None]

    out = template * (1 - mask[..., None]) + coffee * mask[..., None]
    return to_png(out)
