"""Анимации: кадры из GIF / анимированных PNG и WebP / видео, и сборка результата в GIF под лимит размера файла."""

import io
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from .template import MAX_PIXELS, flatten

MAX_SECONDS = 6.0  # из анимации или видео берём только начало
MAX_FPS = 15  # больше кадров в секунду не нужно: GIF всё равно не показывает плавнее, а обработка дольше
FRAME_SIDE = 720  # кадры сразу уменьшаем до этого размера: в бокал или чашку они попадут ещё меньше
ANIMATION_SIDE = 480  # большая сторона результата-анимации (шаблон уменьшается до неё)
MIN_SIDE = 160  # меньше этого анимацию не уменьшаем — лучше сократить кадры


@dataclass
class Media:
    frames: list[Image.Image]  # кадры (RGB или RGBA, см. keep_alpha)
    durations: list[int]  # длительность каждого кадра, мс
    source: str  # "картинка", "GIF", "видео" — для логов
    total_ms: int  # длительность исходника, мс (может быть больше взятого)

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1


def read_media(data: bytes, keep_alpha: bool) -> Media:
    """Читает картинку, анимацию или видео. Из анимации и видео берутся первые MAX_SECONDS секунд, не чаще MAX_FPS."""
    try:
        img = Image.open(io.BytesIO(data))
    except UnidentifiedImageError:
        return _read_video(data, keep_alpha)  # не картинка — пробуем как видео (mp4, webm, mov)
    except (Image.DecompressionBombError, OSError) as e:
        raise ValueError("Не удалось открыть картинку") from e
    if img.width * img.height > MAX_PIXELS:
        raise ValueError("Картинка слишком большая")
    try:
        n = getattr(img, "n_frames", 1)
        if n == 1:
            img.load()
            frame = flatten(ImageOps.exif_transpose(img), keep_alpha)
            return Media([frame], [0], "картинка", 0)
        timed = []
        for i in range(n):
            img.seek(i)
            # convert() даёт полный кадр: у GIF Pillow сам накладывает кадр на предыдущие
            timed.append((_shrink(flatten(img.convert("RGBA"), keep_alpha)), img.info.get("duration") or 100))
    except (OSError, EOFError) as e:
        raise ValueError("Не удалось прочитать анимацию") from e
    return _resample(timed, img.format or "анимация")


def _read_video(data: bytes, keep_alpha: bool) -> Media:
    import av  # импорт здесь: библиотека тяжёлая, а для картинок не нужна

    timed = []
    try:
        with av.open(io.BytesIO(data)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            prev = None
            for frame in container.decode(stream):
                t = float(frame.time or 0)
                if prev is not None:
                    timed[-1] = (timed[-1][0], round((t - prev) * 1000))
                if t >= MAX_SECONDS:
                    break
                timed.append((_shrink(flatten(frame.to_image(), keep_alpha)), round(1000 / float(stream.average_rate or 25))))
                prev = t
    except (av.FFmpegError, IndexError) as e:
        raise ValueError("Это не картинка и не видео") from e
    if not timed:
        raise ValueError("В видео нет кадров")
    return _resample(timed, "видео")


def _shrink(img: Image.Image) -> Image.Image:
    if max(img.size) > FRAME_SIDE:
        img = img.copy()
        img.thumbnail((FRAME_SIDE, FRAME_SIDE), Image.Resampling.LANCZOS)
    return img


def _resample(timed: list[tuple[Image.Image, int]], source: str) -> Media:
    """Обрезает до MAX_SECONDS и прореживает до MAX_FPS: кадр берётся, только если с прошлого прошло >= 1/MAX_FPS с."""
    total = sum(d for _, d in timed)
    frames, durations = [], []
    t, next_t = 0, 0.0
    for frame, d in timed:
        if t >= MAX_SECONDS * 1000:
            break
        if t >= next_t:
            frames.append(frame)
            durations.append(0)
            next_t = t + 1000 / MAX_FPS
        durations[-1] += d  # пропущенные кадры добавляют время к последнему взятому
        t += d
    return Media(frames, [max(20, d) for d in durations], source, total)


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
