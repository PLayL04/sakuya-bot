"""Радиальное искажение картинки без шаблона: «рыбий глаз» (!explode) и вдавливание к центру (!implode).

Каждый пиксель результата берёт цвет из исходной картинки на том же луче от центра, но на другом расстоянии:
  r — расстояние пикселя от центра в долях вписанного эллипса (0 — центр, 1 — край эллипса);
  g(r) — откуда брать цвет: g(r) < r — центр увеличен (выпуклость), g(r) > r — центр уменьшен (вдавливание).
g(r) = a·r + 2(1 − a)·r² − (1 − a)·r³, где a — масштаб в центре: a < 1 увеличивает, a > 1 уменьшает.
У этого многочлена g(1) = 1 и g'(1) = 1, поэтому на краю эллипса искажение плавно сходит на нет и шва нет,
а за эллипсом (углы картинки) всё остаётся как было. При 0 < a < 4 g монотонна — картинка не «переворачивается».
"""

from functools import lru_cache

import numpy as np
from PIL import Image

from ..imaging.effect import ImageEffect
from ..imaging.template import open_image, to_png

EXPLODE = 0.3  # центр увеличен в ~3,3 раза
IMPLODE = 2.5  # центр уменьшен в 2,5 раза


def radius_map(r: np.ndarray, a: float) -> np.ndarray:
    """g(r) для r в 0..1: откуда брать цвет для пикселя на расстоянии r от центра."""
    return a * r + 2 * (1 - a) * r ** 2 - (1 - a) * r ** 3


def source_coords(width: int, height: int, a: float) -> tuple[np.ndarray, np.ndarray]:
    """Для каждого пикселя результата width × height — координаты (x, y) в исходной картинке, откуда брать цвет."""
    cx, cy = (width - 1) / 2, (height - 1) / 2
    rx, ry = max(width / 2, 1), max(height / 2, 1)
    nx, ny = np.meshgrid((np.arange(width, dtype=np.float32) - cx) / rx, (np.arange(height, dtype=np.float32) - cy) / ry)
    r = np.hypot(nx, ny)
    inside = r < 1
    k = np.ones_like(r)  # во сколько раз ближе к центру (или дальше от него) брать цвет
    k[inside] = radius_map(r[inside], a) / np.maximum(r[inside], 1e-6)
    return cx + nx * k * rx, cy + ny * k * ry


@lru_cache(maxsize=8)
def sampling(width: int, height: int, a: float) -> tuple[np.ndarray, np.ndarray]:
    """Готовая билинейная выборка: для каждого пикселя результата — индексы четырёх соседей в плоском массиве
    картинки и их веса. Зависит только от размера, поэтому для всех кадров анимации считается один раз."""
    xs, ys = source_coords(width, height, a)
    xs, ys = np.clip(xs, 0, width - 1.001), np.clip(ys, 0, height - 1.001)
    x0, y0 = xs.astype(np.int32), ys.astype(np.int32)
    fx, fy = (xs - x0).astype(np.float32).ravel(), (ys - y0).astype(np.float32).ravel()
    i = (y0 * width + x0).ravel()
    index = np.stack([i, i + 1, i + width, i + width + 1], axis=1)
    weights = np.stack([(1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy], axis=1)
    return index, weights


def radial_distort(img: Image.Image, a: float) -> np.ndarray:
    """Картинка (RGB) -> RGB 0..255 того же размера. a < 1 — выпуклость (explode), a > 1 — вдавливание (implode)."""
    index, weights = sampling(img.width, img.height, float(a))
    src = np.asarray(img.convert("RGB"), dtype=np.float32).reshape(-1, 3)
    return np.einsum("nk,nkc->nc", weights, src[index]).reshape(img.height, img.width, 3)


def explode(img: Image.Image, _template=None) -> np.ndarray:
    return radial_distort(img, EXPLODE)


def implode(img: Image.Image, _template=None) -> np.ndarray:
    return radial_distort(img, IMPLODE)


def make_explode(data: bytes) -> bytes:
    """Принимает байты картинки, возвращает PNG с «рыбьим глазом» (для проверки без Discord)."""
    return to_png(explode(open_image(data)))


def make_implode(data: bytes) -> bytes:
    """Принимает байты картинки, возвращает PNG, вдавленный к центру (для проверки без Discord)."""
    return to_png(implode(open_image(data)))


# описание эффектов для команд: картинка -> PNG, анимация и видео -> GIF (imaging/effect.py); шаблона нет
EXPLODE_EFFECT = ImageEffect("explode", None, explode, keep_alpha=False, encode_still=to_png, still_name="explode.png")
IMPLODE_EFFECT = ImageEffect("implode", None, implode, keep_alpha=False, encode_still=to_png, still_name="implode.png")
