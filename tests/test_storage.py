"""Уборка картинок в img/ (storage.py)."""

from pathlib import Path

import sakuya_bot.storage as storage
from sakuya_bot.storage import load_json, remove_unused_images, save_json


def test_busy_file_stays_in_uploads(img_dir, monkeypatch):
    for name in ("a.png", "b.png", "keep.png"):
        (img_dir / name).write_bytes(b"x")
    save_json(storage.UPLOADS_FILE, ["a.png", "b.png", "keep.png"])
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == "b.png":
            raise PermissionError("открыт в просмотрщике")
        real_unlink(self, missing_ok=missing_ok)
    monkeypatch.setattr(Path, "unlink", unlink)
    assert remove_unused_images({"keep.png"}) == ["a.png"]
    assert load_json(storage.UPLOADS_FILE) == ["b.png", "keep.png"]  # b.png удалится в следующий раз
    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert remove_unused_images({"keep.png"}) == ["b.png"] and not (img_dir / "b.png").exists()
