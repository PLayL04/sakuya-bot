"""Пиво: картинка оказывается внутри бокала — видна сквозь стекло и пиво.

Конвейер:
1. геометрия бокала: для каждой строки — центр и полуширина жидкости (бокал не цилиндр, он сужается к талии);
2. «линза»: бокал с жидкостью увеличивает центр и сжимает края, плюс лёгкая рябь;
3. цвет по закону Бугера — Ламберта — Бера: свет гаснет в пиве, синий сильнее всего, отсюда янтарь;
4. пузырьки, которые поднимаются цепочками и растут к поверхности;
5. блики стекла и затемнение у стенок поверх всего, мягкий переход к пене.
"""

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..config import TEMPLATE_DIR
from ..imaging.template import blur, load_template, open_image, to_jpeg

TEMPLATE_FILE = TEMPLATE_DIR / "beer.jpg"



@dataclass(frozen=True)
class Palette:
    """Цвет пива. Значения по умолчанию подобраны программой под эталонную картинку (оттенок как у пива из
    template/beer.jpg до замены на зелёный, только светлее и золотистее): средний цвет зон верх / середина / низ / дно."""
    absorption: tuple[float, float, float] = (1.138, 1.874, 4.286)  # поглощение R, G, B: синий гаснет быстрее всего
    haze: tuple[float, float, float] = (0.95, 0.653, 0.15)  # цвет света, рассеянного мутным пивом
    haze_amount: float = 0.859
    brightness: float = 1.012  # сколько света проходит сквозь бокал сзади
    foam_shadow: float = 0.323  # насколько пена затемняет пиво под собой (0 — не затемняет)
    foot_thickness: float = 0.222  # толщина слоя в стеклянном дне относительно тела бокала: дно светлое, золотистое


PALETTE = Palette()
FOAM = np.array([0.94, 0.90, 0.80], dtype=np.float32)
LENS = 0.6  # сила «линзы»: 0 — нет искажения, 1 — сильное
SUPERSAMPLE = 3  # пузырьки рисуются в 3 раза крупнее и уменьшаются — так края гладкие


def glass_geometry(mask: np.ndarray, y0: int, y1: int) -> tuple[np.ndarray, np.ndarray, int]:
    """Для строк y0..y1: центр и полуширина жидкости; и строка, где кончается тело бокала (дальше — дно)."""
    m = mask[y0:y1] > 0.5
    filled = m.any(axis=1)
    # тело бокала — первая непрерывная полоса строк; дальше (после зазора) — толстое стеклянное дно
    gap = np.nonzero(~filled)[0]
    body_end = int(gap[0]) if len(gap) else len(filled)
    left = np.where(filled, m.argmax(axis=1), 0).astype(np.float32)
    right = np.where(filled, m.shape[1] - 1 - m[:, ::-1].argmax(axis=1), 0).astype(np.float32)
    width = right - left
    # редкие строки с обрывками маски (край у пены) берём у соседей, потом сглаживаем
    good = width > 0.3 * width.max()
    idx = np.arange(len(width))
    left = np.interp(idx, idx[good], left[good])
    right = np.interp(idx, idx[good], right[good])
    kernel = np.ones(9) / 9
    left = np.convolve(np.pad(left, 4, mode="edge"), kernel, mode="valid")
    right = np.convolve(np.pad(right, 4, mode="edge"), kernel, mode="valid")
    return (left + right) / 2, (right - left) / 2, body_end


def bilinear(img: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Значения картинки в дробных координатах (как Image.resize, но для любых точек)."""
    h, w = img.shape[:2]
    xs, ys = np.clip(xs, 0, w - 1.001), np.clip(ys, 0, h - 1.001)
    x0, y0 = xs.astype(int), ys.astype(int)
    fx, fy = (xs - x0)[..., None], (ys - y0)[..., None]
    top = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    bottom = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bottom * fy


def draw_bubbles(size: tuple[int, int], center: np.ndarray, radius: np.ndarray, body_end: int,
                 rng: np.random.Generator) -> np.ndarray:
    """Слой пузырьков RGBA 0..1: цепочки снизу вверх (пузырёк растёт к поверхности) и одиночные."""
    w, h = size
    k = SUPERSAMPLE
    layer = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def bubble(x: float, y: float, r: float) -> None:
        x, y, r = x * k, y * k, max(r * k, 1.5)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 236, 190, 40), outline=(255, 246, 215, 150), width=max(1, int(r / 3)))
        hr = r * 0.35  # блик в левом верхнем углу пузырька
        d.ellipse((x - r * 0.45 - hr, y - r * 0.45 - hr, x - r * 0.45 + hr, y - r * 0.45 + hr), fill=(255, 255, 255, 210))

    def place(y: float, n: float) -> float:
        row = int(np.clip(y, 0, len(center) - 1))
        return center[row] + n * radius[row]

    # цепочки: пузырьки поднимаются со дна из одной точки
    for _ in range(9):
        n = rng.uniform(-0.75, 0.75)
        y = body_end - rng.uniform(5, 40)
        while y > 8:
            t = y / body_end  # 1 — дно, 0 — поверхность
            bubble(place(y, n + rng.normal(0, 0.01)), y, 1.2 + 2.2 * (1 - t))
            y -= rng.uniform(9, 22) * (1.6 - t)
    # одиночные пузырьки по всему объёму
    for _ in range(70):
        y = rng.uniform(10, body_end - 5)
        bubble(place(y, rng.uniform(-0.9, 0.9)), y, rng.uniform(0.8, 2.6))
    # гуще всего — у самой пены
    for _ in range(60):
        y = rng.uniform(3, 22)
        bubble(place(y, rng.uniform(-0.95, 0.95)), y, rng.uniform(0.8, 2.0))
    small = layer.resize((w, h), Image.Resampling.LANCZOS)
    return np.asarray(small, dtype=np.float32) / 255


def fit_sheet(src: Image.Image, size: tuple[int, int]) -> np.ndarray:
    """Масштабирует картинку под «лист» RGBA 0..1. Масштаб — между «заполнить» и «вписать» (ближе ко второму):
    высокий бокал не отрезает у широких картинок почти всё, а пустое место остаётся чистым пивом."""
    w, h = size
    cover, contain = max(w / src.width, h / src.height), min(w / src.width, h / src.height)
    scale = cover ** 0.35 * contain ** 0.65
    img = src.resize((max(1, round(src.width * scale)), max(1, round(src.height * scale))), Image.Resampling.LANCZOS)
    # растягиваем контраст по цвету (прозрачность не трогаем): блёклые картинки в пиве иначе теряются
    rgb = ImageOps.autocontrast(img.convert("RGB"), cutoff=1)
    alpha = np.asarray(img.getchannel("A"), dtype=np.float32) / 255
    # края картинки растушёвываем (плавно в 0 на последних 15 %): она растворяется в пиве, а не стоит карточкой
    fy = np.clip(np.minimum(np.arange(img.height), np.arange(img.height)[::-1]) / (0.15 * img.height), 0, 1)
    fx = np.clip(np.minimum(np.arange(img.width), np.arange(img.width)[::-1]) / (0.15 * img.width), 0, 1)
    smooth = lambda t: t * t * (3 - 2 * t)
    feathered = alpha * smooth(fy)[:, None] * smooth(fx)[None, :]
    img = Image.merge("RGBA", (*rgb.split(), Image.fromarray((feathered * 255).astype(np.uint8))))
    sheet = Image.new("RGBA", size, (0, 0, 0, 0))
    # центр картинки чуть выше центра бокала (как у кофе: лица обычно сверху)
    sheet.paste(img, ((w - img.width) // 2, int((h - img.height) * 0.4)))
    return np.asarray(sheet, dtype=np.float32) / 255


def outline(alpha: np.ndarray, width: float) -> np.ndarray:
    """Кольцо вдоль края непрозрачной части (0..1): у стикеров по нему читается силуэт предмета в пиве."""
    a = Image.fromarray((alpha * 255).astype(np.uint8))
    size = max(3, int(width) | 1)  # нечётный размер окна
    grown = np.asarray(a.filter(ImageFilter.MaxFilter(size)), dtype=np.float32) / 255
    shrunk = np.asarray(a.filter(ImageFilter.MinFilter(size)), dtype=np.float32) / 255
    return blur(grown - shrunk, width / 3)


def make_beer(data: bytes, palette: Palette = PALETTE) -> bytes:
    """Принимает байты картинки, возвращает JPEG: картинка внутри бокала пива."""
    template, mask, (x0, y0, x1, y1) = load_template(TEMPLATE_FILE)
    w, h = x1 - x0, y1 - y0
    center, radius, body_end = glass_geometry(mask, y0, y1)
    center -= x0  # координаты внутри рамки
    rng = np.random.default_rng(7)

    # картинка — плоский «лист» в середине бокала размером с тело бокала
    sheet_w, sheet_h = int(radius[:body_end].max() * 2), body_end
    src = open_image(data, keep_alpha=True)
    sheet = fit_sheet(src, (sheet_w, sheet_h))
    alpha = sheet[..., 3:]
    gray = sheet[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    # цвет картинки наполовину уходит в яркость — иначе синие и зелёные картинки делают пиво мутно-оливковым
    # гамма 0.75 приподнимает тёмные тона: тёмный фон фото не превращается в сплошную бурую полосу
    tone = (0.5 * sheet[..., :3] + 0.5 * gray[..., None]) ** 0.75
    # предмет загораживает свет, идущий сквозь пиво сзади: он чуть темнее чистого пива вокруг
    image = alpha * (0.06 + 0.8 * tone) + (1 - alpha)
    if np.asarray(src.getchannel("A")).min() < 250:
        # у картинок с прозрачностью (стикеры) — тёмный контур, иначе светлый предмет сливается с пивом
        image *= 1 - 0.45 * outline(np.asarray(sheet[..., 3] > 0.5, dtype=np.float32), 5)[..., None]

    # ---- 1–2. геометрия и «линза» ----
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.maximum(radius, 1)[:, None]
    n = np.clip((xx - center[:, None]) / r, -1, 1)  # -1 левая стенка, 0 центр, 1 правая стенка
    lens = (1 - LENS) * n + LENS * np.arcsin(n) / (np.pi / 2)  # центр увеличивается, края сжимаются
    ripple = blur(rng.random((h, w)), 6) - 0.5  # мягкий шум для неровной ряби
    src_x = sheet_w / 2 + lens * r + 1.5 * np.sin(yy / 23 + 1.3) + 14 * ripple
    src_y = yy + 1.0 * np.sin(xx / 17) + 8 * ripple
    color = bilinear(image, src_x, np.minimum(src_y, body_end - 1))
    color[body_end:] = 1  # в стеклянном дне картинки нет — только пиво

    # ---- 3. закон Бугера — Ламберта — Бера: T = e^(−k·d) ----
    depth = np.sqrt(1 - n ** 2)  # путь света через бокал длиннее в центре
    down = np.clip(yy / body_end, 0, 1)  # и пиво темнее книзу
    thickness = (0.45 + 0.75 * depth) * (0.75 + 0.6 * down)
    thickness[body_end:] *= palette.foot_thickness  # толстое стекло дна
    transmit = np.exp(-np.array(palette.absorption, dtype=np.float32) * thickness[..., None])
    light = 0.12 + 0.88 * color  # даже за тёмным предметом немного света: пиво мутное и светится само
    beer = palette.brightness * light * transmit
    # свечение мутного пива: за предметом его меньше (предмет загораживает часть света) — так предмет контрастнее,
    # а чистое пиво (light = 1) не меняется
    glow = (1 - transmit.mean(axis=2, keepdims=True)) * palette.haze_amount * (0.35 + 0.65 * light)
    beer += np.array(palette.haze, dtype=np.float32) * glow
    # тень от шапки пены: сразу под ней пиво темнее, к середине бокала тень сходит на нет
    beer *= (1 - palette.foam_shadow * np.exp(-yy / (0.3 * body_end)) * (yy < body_end))[..., None]

    # ---- 4. пузырьки ----
    bubbles = draw_bubbles((w, h), center, radius, body_end, rng)
    beer = beer * (1 - bubbles[..., 3:]) + bubbles[..., :3] * bubbles[..., 3:]

    # ---- 5. стекло: затемнение у стенок, блики, переход к пене ----
    edge = np.clip((np.abs(n) - 0.8) / 0.2, 0, 1) ** 2
    beer *= (1 - 0.35 * edge)[..., None]
    highlight = 0.38 * np.exp(-((n + 0.68) / 0.07) ** 2) + 0.22 * np.exp(-((n - 0.62) / 0.03) ** 2)
    highlight *= 0.75 + 0.25 * np.sin(yy / 40)  # блик не идеально ровный по высоте
    beer = 1 - (1 - beer) * (1 - highlight[..., None])  # режим «экран»: только осветляет
    foam_edge = np.clip(1 - yy / 14 + (blur(rng.random((h, w)), 2) - 0.5) * 1.5, 0, 1)[..., None]
    beer = beer * (1 - foam_edge) + FOAM * foam_edge

    out = template.copy()
    area = mask[y0:y1, x0:x1, None]
    out[y0:y1, x0:x1] = template[y0:y1, x0:x1] * (1 - area) + beer * 255 * area
    return to_jpeg(out)
