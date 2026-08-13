from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

METERS_PER_INCH = 0.0254


def resize_cover_bytes(data: bytes, mime: str, max_size: int, dpi: int) -> bytes:
    """Scale the image down so its longer side equals max_size (aspect ratio
    kept) and stamp the given DPI into the output. A non-positive max_size or
    dpi skips that step. Never upscales — if the image is already at or below
    max_size, its dimensions are left untouched."""
    image = QImage()
    image.loadFromData(data)
    if image.isNull():
        return data

    if max_size and max_size > 0 and max(image.width(), image.height()) > max_size:
        image = image.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    if dpi and dpi > 0:
        dots_per_meter = round(dpi / METERS_PER_INCH)
        image.setDotsPerMeterX(dots_per_meter)
        image.setDotsPerMeterY(dots_per_meter)

    fmt = "PNG" if mime == "image/png" else "JPEG"
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    buffer.open(QIODevice.WriteOnly)
    if fmt == "JPEG":
        image.save(buffer, fmt, quality=90)
    else:
        image.save(buffer, fmt)
    buffer.close()
    return bytes(byte_array)
