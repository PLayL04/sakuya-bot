"""Работа с картинками без Discord: шаблон с вырезом и освещением, открытие картинки, сохранение результата."""

import io
import json
from dataclasses import dataclass, field
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


@dataclass(frozen=True, eq=False)
class Template:
    """Шаблон с вырезом, куда эффект вставляет картинку.

    Настройки лежат рядом с шаблоном в JSON с тем же именем (beer2.jpg -> beer2.json); без файла — значения по умолчанию:
      "key": "chroma" | "hue" — как искать вырез: по разнице каналов (плоская зелёная заливка)
             или по оттенку (фото, перекрашенное в зелёный: тёмные и оливковые участки тоже попадают в вырез);
      "shading": число — перенос освещения шаблона (капли, свечение, тени) на результат эффекта, 0 — не переносить;
      остальные ключи — настройки конкретного эффекта (например, "beer": {...}).
    """
    rgb: np.ndarray  # RGB шаблона 0..255
    mask: np.ndarray  # вырез 0..1
    bbox: tuple[int, int, int, int]  # рамка выреза (x0, y0, x1, y1)
    shading: np.ndarray | None  # освещение внутри выреза, 1 — средняя яркость; None — не переносить
    settings: dict = field(default_factory=dict)
    scale: float = 1.0  # во сколько раз шаблон уменьшен относительно файла (для анимаций он меньше)

    def resized(self, factor: float) -> "Template":
        """Уменьшенная копия шаблона: для анимаций, где кадров много и крупный размер не нужен."""
        h, w = self.mask.shape
        size = (max(1, round(w * factor)), max(1, round(h * factor)))
        gray = lambda a: np.asarray(Image.fromarray(a.astype(np.float32), mode="F").resize(size, Image.Resampling.BILINEAR))
        rgb = np.asarray(Image.fromarray(np.clip(self.rgb, 0, 255).astype(np.uint8)).resize(size, Image.Resampling.LANCZOS),
                         dtype=np.float32)
        mask = np.clip(gray(self.mask), 0, 1)
        bbox = Image.fromarray((mask > 0.5).astype(np.uint8) * 255).getbbox()
        shading = None if self.shading is None else gray(self.shading)
        return Template(rgb, mask, bbox, shading, self.settings, self.scale * factor)

    def apply_shading(self, rgb: np.ndarray) -> np.ndarray:
        """Переносит освещение шаблона на картинку того же размера, что шаблон (общий шаг для всех эффектов)."""
        if self.shading is None:
            return rgb
        return rgb * self.shading[..., None]

    def composite(self, effect_rgb: np.ndarray) -> np.ndarray:
        """Вставляет результат эффекта (полный размер шаблона, 0..255) в вырез: снаружи остаётся шаблон."""
        m = self.mask[..., None]
        return self.rgb * (1 - m) + self.apply_shading(effect_rgb) * m


def chroma_key(rgb: np.ndarray) -> np.ndarray:
    """Плоская зелёная заливка: вырез там, где зелёный канал заметно больше красного и синего."""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mask = np.clip((g - np.maximum(r, b) - 15) / 45, 0, 1)
    # убираем зелёный ореол вокруг выреза (только рядом с ним, чтобы не трогать остальной шаблон)
    near = to_float(Image.fromarray((mask > 0).astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(7))) > 0
    rgb[..., 1] = np.where(near, np.minimum(g, (r + b) / 2), g)
    # расширяем вырез на 1 px: эффект закрывает смешанные с зелёным пиксели у края
    return to_float(Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(3)))


def hue_key(rgb: np.ndarray) -> np.ndarray:
    """Фото, перекрашенное в зелёный: вырез по оттенку (от жёлто-зелёного до зелёного), с мягкими краями.
    Тёмные и оливковые участки (стенки бокала) попадают в вырез; чёрный фон и бежевая пена — нет."""
    hsv = np.asarray(Image.fromarray(rgb.astype(np.uint8)).convert("HSV"), dtype=np.float32)
    hue, sat, val = hsv[..., 0] * 360 / 255, hsv[..., 1] / 255, hsv[..., 2] / 255
    ramp = lambda x, a, b: np.clip((x - a) / (b - a), 0, 1)
    mask = ramp(hue, 45, 75) * (1 - ramp(hue, 165, 190)) * ramp(sat, 0.15, 0.4) * ramp(val, 0.04, 0.12)
    # у частично зелёных пикселей убираем зелёный оттенок: оливковая стенка становится коричневатой, а не зелёной
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    rgb[..., 1] = np.where(mask > 0, np.minimum(g, (r + b) / 2 + 0.3 * np.maximum(r - b, 0)), g)
    return mask


def template_shading(rgb_original: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Освещение внутри выреза: яркость шаблона, делённая на типичную (медиану). 1 — как в среднем, >1 — блики и
    свечение, <1 — тени. Яркость = максимальный канал, так что цвет заливки (зелёный) не важен."""
    val = rgb_original.max(axis=2) / 255
    typical = float(np.median(val[mask > 0.5])) or 1.0
    shading = np.clip(val / typical, 0, 2.5)
    return np.where(mask > 0, shading, 1).astype(np.float32)


def read_settings(path: Path) -> dict:
    settings_file = path.with_suffix(".json")
    return json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}


@lru_cache(maxsize=16)
def load_template(path: Path, max_side: int | None = None) -> Template:
    """Загружает шаблон и его настройки, находит вырез. Результат кешируется для каждого файла.
    max_side — уменьшить шаблон так, чтобы большая сторона была не больше этого числа (для анимаций)."""
    if max_side:
        full = load_template(path)
        factor = max_side / max(full.mask.shape)
        return full.resized(factor) if factor < 1 else full
    settings = read_settings(path)
    img = Image.open(path)
    if img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info:
        # шаблон с прозрачностью: вырез — прозрачные пиксели
        rgba = np.asarray(img.convert("RGBA"), dtype=np.float32)
        original, mask = rgba[..., :3].copy(), 1 - rgba[..., 3] / 255
        rgb = original.copy()
    else:
        original = np.asarray(img.convert("RGB"), dtype=np.float32)
        rgb = original.copy()
        mask = hue_key(rgb) if settings.get("key") == "hue" else chroma_key(rgb)
    mask = blur(mask, 1)
    bbox = Image.fromarray((mask > 0.5).astype(np.uint8) * 255).getbbox()
    if not bbox:
        raise RuntimeError(f"В шаблоне {path} не найден вырез (зелёный фон или прозрачность)")
    strength = float(settings.get("shading", 0))
    shading = template_shading(original, mask) ** strength if strength else None
    return Template(rgb, mask, bbox, shading, settings)


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
    return flatten(img, keep_alpha)


def flatten(img: Image.Image, keep_alpha: bool) -> Image.Image:
    """Приводит картинку или кадр анимации к RGBA (keep_alpha) или к RGB, где прозрачный фон залит чёрным."""
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
