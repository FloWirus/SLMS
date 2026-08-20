from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage

METERS_PER_INCH = 0.0254
# Quality for JPEG output. 90 is visually indistinguishable from 100 at the
# sizes covers get resized to, at roughly half the bytes -- which matters on
# a device where the artwork ships inside every single track.
JPEG_QUALITY = 90


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
        image.save(buffer, fmt, quality=JPEG_QUALITY)
    else:
        image.save(buffer, fmt)
    buffer.close()
    return bytes(byte_array)


def read_image_info(data: bytes) -> tuple[int, int, int] | None:
    """Return (width, height, dpi) for the given image bytes, or None if the
    data can't be decoded as an image. dpi is 0 when the image carries no
    density info."""
    image = QImage()
    image.loadFromData(data)
    if image.isNull():
        return None
    dots_per_meter = image.dotsPerMeterX() or image.dotsPerMeterY()
    dpi = round(dots_per_meter * METERS_PER_INCH) if dots_per_meter else 0
    return image.width(), image.height(), dpi
