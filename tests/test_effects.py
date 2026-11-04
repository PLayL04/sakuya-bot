"""Команды с картинками: !кофе, !пиво, !музей, !explode, !implode. Картинки, анимации и видео генерируются в тесте.

Проверяется, что эффекты не падают, результат открывается, укладывается в лимит файла, а из длинных анимаций и видео
берётся только начало. Как выглядит результат — смотреть глазами: тесты этого не оценят."""

import io

import numpy as np
import pytest
from PIL import Image, ImageDraw

import sakuya_bot.imaging.media as media
from sakuya_bot.beer.effect import EFFECT as BEER
from sakuya_bot.coffee.latte import EFFECT as COFFEE
from sakuya_bot.distort.effect import EXPLODE_EFFECT, IMPLODE_EFFECT
from sakuya_bot.imaging.effect import STILL_SIDE, run_effect
from sakuya_bot.museum.effect import EFFECT as MUSEUM

EFFECTS = [COFFEE, BEER, MUSEUM, EXPLODE_EFFECT, IMPLODE_EFFECT]
LIMIT = 8 * 2**20


def png(size=(320, 200), mode="RGB") -> bytes:
    img = Image.new(mode, size, (200, 120, 40, 255) if mode == "RGBA" else (200, 120, 40))
    ImageDraw.Draw(img).ellipse((20, 20, size[0] - 20, size[1] - 20), fill=(30, 90, 200, 128) if mode == "RGBA" else (30, 90, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def gif(frames=12, duration=100, size=(120, 90)) -> bytes:
    images = []
    for i in range(frames):
        im = Image.new("RGB", size, (i * 7 % 255, 80, 160))
        ImageDraw.Draw(im).ellipse((i % size[0], 10, i % size[0] + 30, 40), fill="yellow")
        images.append(im)
    buf = io.BytesIO()
    images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=duration, loop=0)
    return buf.getvalue()


def video(seconds=2, fps=30, size=(96, 64)) -> bytes:
    import av
    buf = io.BytesIO()
    with av.open(buf, "w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=fps)  # встроенный в FFmpeg кодек — есть в любой сборке PyAV
        stream.width, stream.height, stream.pix_fmt = size[0], size[1], "yuv420p"
        for i in range(seconds * fps):
            frame = np.zeros((size[1], size[0], 3), np.uint8)
            frame[:, :, 0] = i * 3 % 255
            frame[20:40, i % size[0]:i % size[0] + 10] = 255
            for packet in stream.encode(av.VideoFrame.from_ndarray(frame, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buf.getvalue()


def open_result(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


@pytest.mark.parametrize("effect", EFFECTS, ids=lambda e: e.name)
@pytest.mark.parametrize("mode", ["RGB", "RGBA"])
def test_still_image(effect, mode):
    result = run_effect(effect, png(mode=mode), LIMIT)
    img = open_result(result.data)
    assert result.filename == effect.still_name and result.info == "картинка"
    assert img.width > 0 and len(result.data) <= LIMIT


@pytest.mark.parametrize("effect", [EXPLODE_EFFECT, IMPLODE_EFFECT], ids=lambda e: e.name)
def test_large_still_is_reduced(effect):
    img = open_result(run_effect(effect, png((2400, 1600)), LIMIT).data)
    assert max(img.size) == STILL_SIDE


@pytest.mark.parametrize("effect", EFFECTS, ids=lambda e: e.name)
def test_animated_gif(effect):
    result = run_effect(effect, gif(frames=12), LIMIT)
    out = open_result(result.data)
    assert result.filename.endswith(".gif") and out.n_frames == 12 and "GIF: 12 кадров" in result.info


def test_gif_is_shrunk_to_fit_limit():
    source = gif(frames=20, size=(300, 200))
    full = run_effect(COFFEE, source, LIMIT)
    limit = len(full.data) // 4
    small = run_effect(COFFEE, source, limit)  # не влезает — без дизеринга, потом кадры меньше
    assert len(small.data) <= limit
    assert max(open_result(small.data).size) < max(open_result(full.data).size)


def test_gif_too_big_even_when_small():
    with pytest.raises(ValueError, match="слишком большой"):
        run_effect(COFFEE, gif(frames=20, size=(300, 200)), 5_000)


@pytest.mark.slow
def test_long_fast_gif_takes_only_the_beginning():
    m = media.read_media(gif(frames=600, duration=20), keep_alpha=False)  # 12 с по 50 кадров в секунду
    assert m.truncated
    assert len(m.frames) <= media.MAX_SECONDS * media.MAX_FPS + 1
    assert sum(m.durations) <= media.MAX_SECONDS * 1000 + 100


@pytest.mark.slow
def test_video():
    result = run_effect(EXPLODE_EFFECT, video(seconds=2), LIMIT)
    assert result.filename == "explode.gif" and "видео" in result.info
    assert 25 <= open_result(result.data).n_frames <= 31  # 2 с по 15 кадров в секунду


@pytest.mark.slow
def test_long_60fps_video_is_sampled():
    m = media.read_media(video(seconds=8, fps=60), keep_alpha=False)
    assert m.truncated and m.source == "видео"
    assert len(m.frames) == media.MAX_SECONDS * media.MAX_FPS  # 6 с по 15 кадров
    assert all(d >= 20 for d in m.durations)


@pytest.mark.parametrize("data", [b"not an image at all", b"\x89PNG\r\n\x1a\n broken"])
def test_garbage_is_rejected(data):
    with pytest.raises(ValueError):
        run_effect(COFFEE, data, LIMIT)


def test_huge_picture_is_rejected(monkeypatch):
    monkeypatch.setattr(media, "MAX_PIXELS", 1000)
    with pytest.raises(ValueError, match="слишком большая"):
        media.read_media(png(), keep_alpha=False)


# ---------- уменьшение исходника перед обработкой ----------

def jpeg(size) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (90, 140, 200)).save(buf, "JPEG")
    return buf.getvalue()


def test_large_picture_is_shrunk_before_effect():
    m = media.read_media(png((3000, 1000)), keep_alpha=False)
    assert m.frames[0].size == (media.SOURCE_SIDE, round(media.SOURCE_SIDE / 3))


def test_huge_jpeg_is_decoded_shrunk(monkeypatch):
    """JPEG больше лимита открывается: он уменьшается уже при декодировании. PNG того же размера — нет."""
    monkeypatch.setattr(media, "MAX_PIXELS", 100_000)
    monkeypatch.setattr(media, "SOURCE_SIDE", 150)
    m = media.read_media(jpeg((1200, 800)), keep_alpha=False)
    assert max(m.frames[0].size) == 150
    with pytest.raises(ValueError, match="слишком большая"):
        media.read_media(png((1200, 800)), keep_alpha=False)


def test_media_is_read_from_file(tmp_path):
    path = tmp_path / "anim.tmp"
    path.write_bytes(gif(frames=5))
    assert len(media.read_media(path, keep_alpha=False).frames) == 5
    path.unlink()  # файл закрыт после чтения — на Windows иначе не удалить


@pytest.mark.slow
def test_large_video_frames_are_shrunk(tmp_path):
    path = tmp_path / "video.tmp"
    path.write_bytes(video(seconds=1, size=(1600, 900)))
    m = media.read_media(path, keep_alpha=False)
    assert m.source == "видео" and m.frames[0].size == (media.FRAME_SIDE, 405)
    result = run_effect(EXPLODE_EFFECT, path, LIMIT)
    assert result.filename == "explode.gif"
