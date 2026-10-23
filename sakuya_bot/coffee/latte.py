"""Латте-арт: вставляет картинку в шаблон чашки и стилизует её под рисунок пенкой на эспрессо."""

import io
from functools import lru_cache

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

from ..config import TEMPLATE_DIR

TEMPLATE_FILE = TEMPLATE_DIR / "coffee.png"
MAX_PIXELS = 40_000_000  # защита от гигантских картинок

# цвета кофе: тёмные места картинки — эспрессо, средние — крема, светлые — молочная пена
ESPRESSO = np.array([43, 23, 12], dtype=np.float32)
CREMA = np.array([138, 90, 51], dtype=np.float32)
FOAM = np.array([242, 228, 204], dtype=np.float32)


def to_float(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.float32) / 255


def blur(arr: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    return to_float(img.filter(ImageFilter.GaussianBlur(radius)))


@lru_cache(maxsize=1)
def load_template() -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """Возвращает (RGB шаблона 0..255, маска выреза 0..1, рамка выреза)."""
    img = Image.open(TEMPLATE_FILE)
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        # шаблон с прозрачностью: вырез — прозрачные пиксели
        rgba = np.asarray(img.convert("RGBA"), dtype=np.float32)
        rgb, mask = rgba[..., :3].copy(), 1 - rgba[..., 3] / 255
    else:
        # шаблон с зелёным фоном: хромакей с мягким краем
        rgb = np.asarray(img.convert("RGB"), dtype=np.float32).copy()
        r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
        mask = np.clip((g - np.maximum(r, b) - 15) / 45, 0, 1)
        # убираем зелёный ореол вокруг выреза (только рядом с ним, чтобы не трогать остальной шаблон)
        near = to_float(Image.fromarray((mask > 0).astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))) > 0
        rgb[..., 1] = np.where(near, np.minimum(g, (r + b) / 2), g)
        # расширяем вырез на 1 px: кофе закрывает смешанные с зелёным пиксели у ободка
        mask = to_float(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)))
    mask = blur(mask, 1)
    bbox = Image.fromarray((mask > 0.5).astype(np.uint8) * 255).getbbox()
    if not bbox:
        raise RuntimeError(f"В шаблоне {TEMPLATE_FILE} не найден вырез (зелёный фон или прозрачность)")
    return rgb, mask, bbox


def open_image(data: bytes) -> Image.Image:
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("Картинка слишком большая")
        img.seek(0)  # у GIF берём первый кадр
        img = ImageOps.exif_transpose(img)
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, EOFError) as e:
        raise ValueError("Не удалось открыть картинку") from e
    # прозрачный фон (стикеры, PNG) становится эспрессо
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        img = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)), img)
    return img.convert("RGB")


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
    template, mask, (x0, y0, x1, y1) = load_template()
    coffee = template.copy()
    coffee[y0:y1, x0:x1] = latte_colors(open_image(data), (x1 - x0, y1 - y0))

    # тень от стенок чашки на поверхности кофе
    shadow = blur(1 - mask, 8) * mask
    coffee *= (1 - 0.6 * shadow)[..., None]

    out = template * (1 - mask[..., None]) + coffee * mask[..., None]
    buf = io.BytesIO()
    Image.fromarray(np.clip(out, 0, 255).astype(np.uint8)).save(buf, "PNG", optimize=True)
    return buf.getvalue()
