from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

_CACHE: dict[str, QIcon] = {}
_CHECKBOX_CACHE: dict[bool, QIcon] = {}

GREEN = "#2fa84f"
GRAY = "#9a9a9a"


def _check_icon(color: str) -> QIcon:
    if color not in _CACHE:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidthF(2.2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(QPolygonF([QPointF(3, 8.5), QPointF(6.5, 12), QPointF(13, 3.5)]))
        painter.end()
        _CACHE[color] = QIcon(pixmap)
    return _CACHE[color]


def full_presence_icon() -> QIcon:
    """All tracks in this row (or the row itself) are present on the other side."""
    return _check_icon(GREEN)


def partial_presence_icon() -> QIcon:
    """Some, but not all, tracks under this row are present on the other side."""
    return _check_icon(GRAY)


def checkbox_icon(checked: bool) -> QIcon:
    """Self-drawn checkbox glyph, toggled purely by our own click handling (no native
    Qt.ItemIsUserCheckable) so there is no double-toggle race with Qt's built-in delegate."""
    if checked not in _CHECKBOX_CACHE:
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        box_color = QColor(GREEN) if checked else QColor(GRAY)
        pen = QPen(box_color)
        pen.setWidthF(1.4)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(2, 2, 12, 12), 3, 3)
        if checked:
            check_pen = QPen(QColor(GREEN))
            check_pen.setWidthF(2.0)
            check_pen.setCapStyle(Qt.RoundCap)
            check_pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(check_pen)
            painter.drawPolyline(QPolygonF([QPointF(4.5, 8.5), QPointF(7, 11), QPointF(12, 4.5)]))
        painter.end()
        _CHECKBOX_CACHE[checked] = QIcon(pixmap)
    return _CHECKBOX_CACHE[checked]
