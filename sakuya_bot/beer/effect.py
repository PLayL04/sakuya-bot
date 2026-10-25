"""Пиво: картинка оказывается внутри бокала — видна сквозь стекло и пиво.

Конвейер:
1. геометрия бокала: для каждой строки — центр и полуширина жидкости (бокал не цилиндр, он сужается к талии);
2. «линза»: бокал с жидкостью увеличивает центр и сжимает края, плюс лёгкая рябь;
3. цвет по закону Бугера — Ламберта — Бера: свет гаснет в пиве, синий сильнее всего, отсюда янтарь;
4. пузырьки, которые поднимаются цепочками и растут к поверхности;
5. блики стекла и затемнение у стенок поверх всего, мягкий переход к пене;
6. вставка в шаблон — с переносом освещения шаблона, если он включён в его настройках (общий шаг, imaging/template.py).

Настройки шаблона (JSON рядом с ним, раздел "beer"), по умолчанию — для плоской зелёной заливки:
  "walls": "shaped" — стенки фигурные, ширина по каждой строке; "straight" — прямые, ширина одна на весь бокал;
  "bubbles": true/false — рисовать свои пузырьки (у фото-шаблона обычно уже есть капли и пузырьки);
  "glass": true/false — рисовать свои блики и затемнение у стенок (у фото-шаблона они уже есть в освещении).
Все размеры (рябь, пузырьки, переход к пене) — в долях ширины бокала, так эффект одинаков на любом шаблоне.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from ..config import TEMPLATE_DIR
from ..imaging.effect import ImageEffect
from ..imaging.template import Template, blur, load_template, open_image, to_jpeg

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
REFERENCE_WIDTH = 266  # ширина бокала в template/beer.jpg, под которую подбирались размеры в пикселях
FOAM = np.array([0.94, 0.90, 0.80], dtype=np.float32)
LENS = 0.6  # сила «линзы»: 0 — нет искажения, 1 — сильное
SUPERSAMPLE = 3  # пузырьки рисуются в 3 раза крупнее и уменьшаются — так края гладкие


def glass_geometry(mask: np.ndarray, y0: int, y1: int, walls: str = "shaped") -> tuple[np.ndarray, np.ndarray, int]:
    """Для строк y0..y1: центр и полуширина жидкости; и строка, где кончается тело бокала (дальше — дно).

    walls="straight": края берутся одни на весь бокал (медиана по строкам) — капли, стекающая пена и тёмные участки
    у стенок не сдвигают картинку от строки к строке."""
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
    if walls == "straight":
        left = np.full_like(left, np.median(left[:body_end]))
        right = np.full_like(right, np.median(right[:body_end]))
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
                 rng: np.random.Generator, scale: float = 1.0) -> np.ndarray:
    """Слой пузырьков RGBA 0..1: цепочки снизу вверх (пузырёк растёт к поверхности) и одиночные.
    scale — во сколько раз бокал шире эталонного: размеры и расстояния пузырьков растут вместе с ним."""
    w, h = size
    k = SUPERSAMPLE
    layer = Image.new("RGBA", (w * k, h * k), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def bubble(x: float, y: float, r: float) -> None:
        x, y, r = x * k, y * k, max(r * scale * k, 1.5)
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 236, 190, 40), outline=(255, 246, 215, 150), width=max(1, int(r / 3)))
        hr = r * 0.35  # блик в левом верхнем углу пузырька
        d.ellipse((x - r * 0.45 - hr, y - r * 0.45 - hr, x - r * 0.45 + hr, y - r * 0.45 + hr), fill=(255, 255, 255, 210))

    def place(y: float, n: float) -> float:
        row = int(np.clip(y, 0, len(center) - 1))
        return center[row] + n * radius[row]

    # цепочки: пузырьки поднимаются со дна из одной точки
    for _ in range(9):
        n = rng.uniform(-0.75, 0.75)
        y = body_end - rng.uniform(5, 40) * scale
        while y > 8 * scale:
            t = y / body_end  # 1 — дно, 0 — поверхность
            bubble(place(y, n + rng.normal(0, 0.01)), y, 1.2 + 2.2 * (1 - t))
            y -= rng.uniform(9, 22) * (1.6 - t) * scale
    # одиночные пузырьки по всему объёму
    for _ in range(70):
        y = rng.uniform(10 * scale, body_end - 5 * scale)
        bubble(place(y, rng.uniform(-0.9, 0.9)), y, rng.uniform(0.8, 2.6))
    # гуще всего — у самой пены
    for _ in range(60):
        y = rng.uniform(3, 22) * scale
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


@dataclass(frozen=True, eq=False)
class Prepared:
    """Всё, что зависит только от шаблона и цвета пива, а не от картинки: считается один раз на шаблон
    (для анимации — один раз на все кадры)."""
    k: float  # во сколько раз бокал шире эталонного
    body_end: int
    sheet_size: tuple[int, int]
    src_x: np.ndarray  # откуда в «листе» с картинкой брать каждый пиксель бокала (линза + рябь)
    src_y: np.ndarray
    transmit: np.ndarray  # доля света, прошедшего сквозь пиво (закон Бера)
    glow_base: np.ndarray  # свечение мутного пива без учёта картинки
    foam_shadow: np.ndarray  # множитель тени под пеной
    bubbles: np.ndarray | None  # слой пузырьков RGBA
    edge: np.ndarray | None  # множитель затемнения у стенок
    highlight: np.ndarray | None  # блики стекла
    foam_edge: np.ndarray  # переход к пене


@lru_cache(maxsize=8)
def prepare(template: Template, palette: Palette = PALETTE) -> Prepared:
    opts = template.settings.get("beer", {})
    x0, y0, x1, y1 = template.bbox
    w, h = x1 - x0, y1 - y0
    center, radius, body_end = glass_geometry(template.mask, y0, y1, opts.get("walls", "shaped"))
    center -= x0  # координаты внутри рамки
    # случайные числа берутся всегда в одном порядке (рябь, пузырьки, пена) — результат повторяется от раза к разу
    rng = np.random.default_rng(7)
    k = float(radius[:body_end].max()) * 2 / REFERENCE_WIDTH  # все размеры в пикселях — относительно ширины бокала
    sheet_w, sheet_h = int(radius[:body_end].max() * 2), body_end

    # ---- 1–2. геометрия и «линза» ----
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    r = np.maximum(radius, 1)[:, None]
    n = np.clip((xx - center[:, None]) / r, -1, 1)  # -1 левая стенка, 0 центр, 1 правая стенка
    lens = (1 - LENS) * n + LENS * np.arcsin(n) / (np.pi / 2)  # центр увеличивается, края сжимаются
    ripple = blur(rng.random((h, w)), 6 * k) - 0.5  # мягкий шум для неровной ряби
    src_x = sheet_w / 2 + lens * r + k * (1.5 * np.sin(yy / (23 * k) + 1.3) + 14 * ripple)
    src_y = yy + k * (1.0 * np.sin(xx / (17 * k)) + 8 * ripple)

    # ---- 3. закон Бугера — Ламберта — Бера: T = e^(−k·d) ----
    depth = np.sqrt(1 - n ** 2)  # путь света через бокал длиннее в центре
    down = np.clip(yy / body_end, 0, 1)  # и пиво темнее книзу
    thickness = (0.45 + 0.75 * depth) * (0.75 + 0.6 * down)
    thickness[body_end:] *= palette.foot_thickness  # толстое стекло дна
    transmit = np.exp(-np.array(palette.absorption, dtype=np.float32) * thickness[..., None])
    glow_base = (1 - transmit.mean(axis=2, keepdims=True)) * palette.haze_amount
    # тень от шапки пены: сразу под ней пиво темнее, к середине бокала тень сходит на нет
    foam_shadow = (1 - palette.foam_shadow * np.exp(-yy / (0.3 * body_end)) * (yy < body_end))[..., None]

    # ---- 4–5. пузырьки, стекло, переход к пене ----
    bubbles = draw_bubbles((w, h), center, radius, body_end, rng, k) if opts.get("bubbles", True) else None
    edge = highlight = None
    if opts.get("glass", True):
        edge = (1 - 0.35 * np.clip((np.abs(n) - 0.8) / 0.2, 0, 1) ** 2)[..., None]
        highlight = 0.38 * np.exp(-((n + 0.68) / 0.07) ** 2) + 0.22 * np.exp(-((n - 0.62) / 0.03) ** 2)
        highlight = (highlight * (0.75 + 0.25 * np.sin(yy / (40 * k))))[..., None]  # блик не идеально ровный по высоте
    foam_edge = np.clip(1 - yy / (14 * k) + (blur(rng.random((h, w)), 2 * k) - 0.5) * 1.5, 0, 1)[..., None]
    return Prepared(k, body_end, (sheet_w, sheet_h), src_x, src_y, transmit, glow_base, foam_shadow,
                    bubbles, edge, highlight, foam_edge)


def render(src: Image.Image, template: Template, palette: Palette = PALETTE) -> np.ndarray:
    """Один кадр: картинка (RGBA) внутри бокала пива. Возвращает RGB 0..255 размером с шаблон."""
    p = prepare(template, palette)
    x0, y0, x1, y1 = template.bbox

    # картинка — плоский «лист» в середине бокала размером с тело бокала
    sheet = fit_sheet(src, p.sheet_size)
    alpha = sheet[..., 3:]
    gray = sheet[..., :3] @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    # цвет картинки наполовину уходит в яркость — иначе синие и зелёные картинки делают пиво мутно-оливковым
    # гамма 0.75 приподнимает тёмные тона: тёмный фон фото не превращается в сплошную бурую полосу
    tone = (0.5 * sheet[..., :3] + 0.5 * gray[..., None]) ** 0.75
    # предмет загораживает свет, идущий сквозь пиво сзади: он чуть темнее чистого пива вокруг
    image = alpha * (0.06 + 0.8 * tone) + (1 - alpha)
    if np.asarray(src.getchannel("A")).min() < 250:
        # у картинок с прозрачностью (стикеры) — тёмный контур, иначе светлый предмет сливается с пивом
        image *= 1 - 0.45 * outline(np.asarray(sheet[..., 3] > 0.5, dtype=np.float32), 5 * p.k)[..., None]

    color = bilinear(image, p.src_x, np.minimum(p.src_y, p.body_end - 1))
    color[p.body_end:] = 1  # в стеклянном дне картинки нет — только пиво

    light = 0.12 + 0.88 * color  # даже за тёмным предметом немного света: пиво мутное и светится само
    beer = palette.brightness * light * p.transmit
    # свечение мутного пива: за предметом его меньше (предмет загораживает часть света) — так предмет контрастнее,
    # а чистое пиво (light = 1) не меняется
    glow = p.glow_base * (0.35 + 0.65 * light)
    beer += np.array(palette.haze, dtype=np.float32) * glow
    beer *= p.foam_shadow
    if p.bubbles is not None:
        beer = beer * (1 - p.bubbles[..., 3:]) + p.bubbles[..., :3] * p.bubbles[..., 3:]
    if p.edge is not None:
        beer *= p.edge
        beer = 1 - (1 - beer) * (1 - p.highlight)  # режим «экран»: только осветляет
    beer = beer * (1 - p.foam_edge) + FOAM * p.foam_edge

    # ---- 6. вставка в шаблон (с освещением шаблона, если оно включено в его настройках) ----
    full = template.rgb.copy()
    full[y0:y1, x0:x1] = beer * 255
    return template.composite(full)


def make_beer(data: bytes, palette: Palette = PALETTE) -> bytes:
    """Принимает байты картинки, возвращает JPEG: картинка внутри бокала пива."""
    return to_jpeg(render(open_image(data, keep_alpha=True), load_template(TEMPLATE_FILE), palette))


# описание эффекта для команды: картинка -> JPEG, анимация и видео -> GIF (imaging/effect.py)
EFFECT = ImageEffect("пиво", TEMPLATE_FILE, render, keep_alpha=True, encode_still=to_jpeg, still_name="beer.jpg")
