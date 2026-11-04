"""Чтение исходника (картинка, анимация, видео) с уменьшением до разумного размера, и сборка результата в GIF
под лимит размера файла.

Файл может быть любого размера: до обработки эффектом всё сжимается — картинка до SOURCE_SIDE, кадры анимации
и видео до FRAME_SIDE, из анимации и видео берётся только начало. JPEG и видео уменьшаются уже при декодировании,
поэтому и огромное фото с телефона, и видео в 4K читаются быстро и без большого расхода памяти."""

import io
import warnings
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from .template import MAX_PIXELS, flatten

MAX_SECONDS = 6.0  # из анимации или видео берём только начало
MAX_FPS = 15  # больше кадров в секунду не нужно: GIF всё равно не показывает плавнее, а обработка дольше
SOURCE_SIDE = 2048  # картинку сразу уменьшаем до этого размера: эффектам больше не нужно
FRAME_SIDE = 720  # кадры сразу уменьшаем до этого размера: в бокал или чашку они попадут ещё меньше
ANIMATION_SIDE = 480  # большая сторона результата-анимации (шаблон уменьшается до неё)
MIN_SIDE = 160  # меньше этого анимацию не уменьшаем — лучше сократить кадры
TOO_BIG = ("Картинка слишком большая: больше {mp} мегапикселей даже не открыть без огромного расхода памяти. "
           "Пришли её в JPEG или уменьшенной.")

# Pillow сам не открывает картинки больше ~179 Мп (защита от «бомб»). Для JPEG это лишнее: он декодируется сразу
# уменьшенным в 8 раз по стороне, то есть в 64 раза по площади. Остальные форматы проверяет _check_pixels.
Image.MAX_IMAGE_PIXELS = MAX_PIXELS * 64
warnings.filterwarnings("ignore", category=Image.DecompressionBombWarning)


MAX_SOURCE_FRAMES = 3000  # сколько кадров исходника просматриваем максимум (кадры по 1 мс, видео без меток времени)

# кадр исходника: (начало, мс; конец, мс; функция, которая декодирует и готовит кадр — вызывается, только если он нужен)
SourceFrame = tuple[float, float, Callable[[], Image.Image]]


@dataclass
class Media:
    frames: list[Image.Image]  # кадры (RGB или RGBA, см. keep_alpha)
    durations: list[int]  # длительность каждого кадра, мс
    source: str  # "картинка", "GIF", "видео" — для логов
    truncated: bool = False  # исходник длиннее взятого: взято только начало

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1


def read_media(source: bytes | Path, keep_alpha: bool) -> Media:
    """Читает картинку, анимацию или видео (байты или файл) и сразу уменьшает: картинку до SOURCE_SIDE, кадры до
    FRAME_SIDE. Из анимации и видео берутся первые MAX_SECONDS секунд, не чаще MAX_FPS.
    ValueError с текстом для участника — если файл не читается или слишком велик даже для уменьшения."""
    opened = io.BytesIO(source) if isinstance(source, bytes) else source
    try:
        img = Image.open(opened)
    except UnidentifiedImageError:
        return _read_video(source, keep_alpha)  # не картинка — пробуем как видео (mp4, webm, mov)
    except Image.DecompressionBombError as e:
        raise ValueError(TOO_BIG.format(mp=MAX_PIXELS // 1_000_000)) from e
    except OSError as e:
        raise ValueError("Не удалось открыть картинку") from e
    with img:
        try:
            if not getattr(img, "is_animated", False):
                return Media([_read_still(img, keep_alpha)], [0], "картинка")
            _check_pixels(img)  # кадр анимации уменьшить при декодировании нельзя — только после
            return _sample(_animation_frames(img, keep_alpha), img.format or "анимация")
        except (OSError, EOFError, Image.DecompressionBombError) as e:
            raise ValueError("Не удалось прочитать картинку или анимацию") from e


def _check_pixels(img: Image.Image) -> None:
    if img.width * img.height > MAX_PIXELS:
        raise ValueError(TOO_BIG.format(mp=MAX_PIXELS // 1_000_000))


def _read_still(img: Image.Image, keep_alpha: bool) -> Image.Image:
    # JPEG декодируется сразу уменьшенным (в 2, 4 или 8 раз) — так открываются даже очень большие фото;
    # у остальных форматов draft ничего не делает и картинка декодируется целиком
    img.draft(img.mode, (SOURCE_SIDE, SOURCE_SIDE))
    _check_pixels(img)
    img.load()
    img = ImageOps.exif_transpose(img)
    if max(img.size) > SOURCE_SIDE:
        img.thumbnail((SOURCE_SIDE, SOURCE_SIDE), Image.Resampling.LANCZOS, reducing_gap=3.0)
    return flatten(img, keep_alpha)


def _animation_frames(img: Image.Image, keep_alpha: bool) -> Iterator[SourceFrame]:
    # кадры перебираются до конца файла, а не по n_frames: n_frames у GIF пролистывает весь файл,
    # а из длинной анимации нужно только начало
    t, i = 0, 0
    while True:
        try:
            img.seek(i)
        except EOFError:
            return
        d = img.info.get("duration") or 100
        # convert() даёт полный кадр: у GIF Pillow сам накладывает кадр на предыдущие (и для пропущенных тоже)
        yield t, t + d, lambda: _shrink(flatten(img.convert("RGBA"), keep_alpha))
        t += d
        i += 1


def _video_frame(frame, keep_alpha: bool) -> Image.Image:
    """Кадр видео, уменьшенный до FRAME_SIDE ещё в FFmpeg: кадр 4K не превращается в огромную картинку Pillow."""
    scale = min(1.0, FRAME_SIDE / max(frame.width, frame.height, 1))
    size = {"width": max(1, round(frame.width * scale)), "height": max(1, round(frame.height * scale))}
    return flatten(frame.to_image(**size) if scale < 1 else frame.to_image(), keep_alpha)


def _read_video(source: bytes | Path, keep_alpha: bool) -> Media:
    import av  # импорт здесь: библиотека тяжёлая, а для картинок не нужна

    def frames(container) -> Iterator[SourceFrame]:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        step = 1000 / float(stream.average_rate or 25)
        first = None
        for i, frame in enumerate(container.decode(stream)):
            # время кадра от начала видео; без меток времени — по номеру кадра и частоте
            t = frame.time * 1000 if frame.time is not None else i * step
            first = t if first is None else first
            yield t - first, t - first + step, lambda: _video_frame(frame, keep_alpha)

    try:
        with av.open(io.BytesIO(source) if isinstance(source, bytes) else str(source)) as container:
            return _sample(frames(container), "видео")
    except (av.FFmpegError, IndexError) as e:
        raise ValueError("Это не картинка и не видео") from e


def _shrink(img: Image.Image) -> Image.Image:
    if max(img.size) > FRAME_SIDE:
        img = img.copy()
        img.thumbnail((FRAME_SIDE, FRAME_SIDE), Image.Resampling.LANCZOS)
    return img


def _sample(source_frames: Iterable[SourceFrame], source: str) -> Media:
    """Берёт первые MAX_SECONDS и не чаще MAX_FPS: кадр берётся, только если с прошлого взятого прошло >= 1/MAX_FPS с.
    Пропущенные кадры не декодируются в картинку, а их время добавляется к последнему взятому.
    Дальше MAX_SECONDS исходник не читается — длинная гифка или видео не занимают память и время целиком."""
    frames, starts = [], []
    end, next_t, truncated = 0.0, 0.0, False
    for i, (start, stop, load) in enumerate(source_frames):
        if start >= MAX_SECONDS * 1000 or i >= MAX_SOURCE_FRAMES:
            truncated = True
            break
        if start >= next_t - 1:  # допуск 1 мс: у видео 60 fps каждый 4-й кадр приходится ровно на 1/15 с, но в дробях
            frames.append(load())
            starts.append(start)
            next_t = start + 1000 / MAX_FPS
        end = max(end, stop)
    if not frames:
        raise ValueError("В анимации нет кадров")
    durations = [max(20, round(b - a)) for a, b in zip(starts, starts[1:] + [end])]
    return Media(frames, durations, source, truncated)


def shared_palette(frames: list[Image.Image]) -> Image.Image:
    """Одна палитра (256 цветов) на всю анимацию, подобранная по нескольким кадрам.
    С общей палитрой неподвижный фон одинаков во всех кадрах и хранится в GIF один раз — файл в разы меньше,
    а раскладывать кадры по готовой палитре быстрее, чем подбирать палитру каждому кадру."""
    pick = frames[:: max(1, len(frames) // 6)][:6]
    strip = Image.new("RGB", (pick[0].width, pick[0].height * len(pick)))
    for i, f in enumerate(pick):
        strip.paste(f, (0, i * f.height))
    return strip.quantize(256, Image.Quantize.MEDIANCUT)


def encode_gif(frames: list[Image.Image], durations: list[int], limit: int) -> bytes:
    """Собирает GIF не больше limit байт. Если не влезает: сначала без дизеринга (вдвое меньше, но переливы
    становятся пятнистее), потом уменьшает кадры."""
    scale, dither = 1.0, Image.Dither.FLOYDSTEINBERG
    while True:
        size = (max(1, round(frames[0].width * scale)), max(1, round(frames[0].height * scale)))
        scaled = frames if scale == 1 else [f.resize(size, Image.Resampling.LANCZOS) for f in frames]
        palette = shared_palette(scaled)
        indexed = [f.quantize(palette=palette, dither=dither) for f in scaled]
        buf = io.BytesIO()
        indexed[0].save(buf, "GIF", save_all=True, append_images=indexed[1:], duration=durations, loop=0,
                        optimize=True, disposal=1)
        if buf.tell() <= limit:
            return buf.getvalue()
        if dither != Image.Dither.NONE:
            dither = Image.Dither.NONE
            continue
        if max(size) * 0.8 < MIN_SIDE:
            raise ValueError("Анимация получается слишком большой для Discord даже в маленьком размере")
        # GIF растёт примерно как площадь кадра: уменьшаем сторону с запасом
        scale *= max(0.5, min(0.85, (limit / buf.tell()) ** 0.5 * 0.95))
