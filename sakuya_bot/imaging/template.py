"""Работа с картинками без Discord: шаблон с вырезом (хромакей), открытие картинки, сохранение результата."""

import io
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

MAX_PIXELS = 40_000_000  # защита от гигантских картинок


def to_float(img: Image.Image) -> np.ndarray:
    return np.asarray(img, dtype=np.float32) / 255


def blur(arr: np.ndarray, radius: float) -> np.ndarray:
    img = Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8))
    return to_float(img.filter(ImageFilter.GaussianBlur(radius)))


@lru_cache(maxsize=8)
def load_template(path: Path) -> tuple[np.ndarray, np.ndarray, tuple[int, int, int, int]]:
    """Возвращает (RGB шаблона 0..255, маска выреза 0..1, рамка выреза). Результат кешируется для каждого файла."""
    img = Image.open(path)
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
        # расширяем вырез на 1 px: эффект закрывает смешанные с зелёным пиксели у края
        mask = to_float(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)))
    mask = blur(mask, 1)
    bbox = Image.fromarray((mask > 0.5).astype(np.uint8) * 255).getbbox()
    if not bbox:
        raise RuntimeError(f"В шаблоне {path} не найден вырез (зелёный фон или прозрачность)")
    return rgb, mask, bbox


def open_image(data: bytes, keep_alpha: bool = False) -> Image.Image:
    """Открывает картинку (у GIF — первый кадр, с учётом поворота EXIF).

    keep_alpha=False: прозрачный фон (стикеры, PNG) заливается чёрным, результат RGB.
    keep_alpha=True: прозрачность сохраняется, результат RGBA.
    """
    try:
        img = Image.open(io.BytesIO(data))
        if img.width * img.height > MAX_PIXELS:
            raise ValueError("Картинка слишком большая")
        img.seek(0)  # у GIF берём первый кадр
        img = ImageOps.exif_transpose(img)
        img.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, EOFError) as e:
        raise ValueError("Не удалось открыть картинку") from e
    if keep_alpha:
        return img.convert("RGBA")
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        img = img.convert("RGBA")
        img = Image.alpha_composite(Image.new("RGBA", img.size, (0, 0, 0, 255)), img)
    return img.convert("RGB")


def to_png(rgb: np.ndarray) -> bytes:
    """Массив RGB 0..255 -> байты PNG."""
    buf = io.BytesIO()
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def to_jpeg(rgb: np.ndarray, quality: int = 90) -> bytes:
    """Массив RGB 0..255 -> байты JPEG: для фото-шаблонов в разы легче PNG, и сохраняется быстрее."""
    buf = io.BytesIO()
    Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(buf, "JPEG", quality=quality)
    return buf.getvalue()
