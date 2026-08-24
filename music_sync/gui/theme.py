from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QPolygonF
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

from .icons import GRAY, GREEN


class _CheckIndicatorStyle(QProxyStyle):
    """Draws checkbox indicators itself instead of letting Fusion do it.

    Fusion derives the indicator's outline from QPalette.Window darkened by
    140% -- on the dark palette below that is rgb(45,45,45) -> rgb(32,32,32),
    which sits within two levels of both the Base (30,30,30) the tree paints
    on and the Window (45,45,45) behind the bottom-bar checkboxes. The box
    effectively disappears and only the checkmark survives, so an unchecked
    checkbox reads as empty space.

    Drawing the box with an explicit colour fixes every checkbox at once --
    the tree views' per-track boxes and the plain QCheckBoxes on the bottom
    bar -- and matches the self-drawn glyphs the table view already uses (see
    icons.checkbox_icon), so both panes look the same.
    """

    # The design below is expressed against icons.checkbox_icon's 16x16 grid
    # and scaled to whatever rect the style is handed, so the tree's boxes
    # stay identical in shape to the table's no matter the indicator size.
    _GRID = 16.0

    def drawPrimitive(self, element, option, painter, widget=None):
        if element not in (
            QStyle.PrimitiveElement.PE_IndicatorCheckBox,
            QStyle.PrimitiveElement.PE_IndicatorItemViewItemCheck,
        ):
            super().drawPrimitive(element, option, painter, widget)
            return

        state = option.state
        enabled = bool(state & QStyle.StateFlag.State_Enabled)
        checked = bool(state & QStyle.StateFlag.State_On)
        partial = bool(state & QStyle.StateFlag.State_NoChange)

        if not enabled:
            color = option.palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text)
        elif checked or partial:
            color = QColor(GREEN)
        else:
            color = QColor(GRAY)
            if state & QStyle.StateFlag.State_MouseOver:
                color = color.lighter(125)

        rect = option.rect
        side = min(rect.width(), rect.height())
        scale = side / self._GRID
        origin = QPointF(
            rect.x() + (rect.width() - side) / 2.0,
            rect.y() + (rect.height() - side) / 2.0,
        )

        def point(x: float, y: float) -> QPointF:
            return QPointF(origin.x() + x * scale, origin.y() + y * scale)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        pen = QPen(color)
        pen.setWidthF(max(1.0, 1.4 * scale))
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(
            QRectF(point(2, 2), point(14, 14)), 3 * scale, 3 * scale
        )

        if checked:
            check_pen = QPen(QColor(GREEN) if enabled else color)
            check_pen.setWidthF(max(1.2, 2.0 * scale))
            check_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            check_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(check_pen)
            painter.drawPolyline(QPolygonF([point(4.5, 8.5), point(7, 11), point(12, 4.5)]))
        elif partial:
            # Tri-state: an artist/album whose tracks are only partly checked.
            # A filled bar rather than a checkmark, so "some" never reads as
            # "all" at a glance.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            painter.drawRoundedRect(
                QRectF(point(4.5, 7), point(11.5, 9)), 1 * scale, 1 * scale
            )

        painter.restore()


_default_style_name: str | None = None


def init(app: QApplication) -> None:
    global _default_style_name
    if _default_style_name is None:
        _default_style_name = app.style().objectName()


def system_is_dark() -> bool:
    app = QApplication.instance()
    if app is None:
        return False
    try:
        scheme = app.styleHints().colorScheme()
    except AttributeError:
        return False
    return scheme == Qt.ColorScheme.Dark


def _dark_palette() -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Base, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 45))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 80, 80))
    palette.setColor(QPalette.ColorRole.Link, QColor(100, 170, 255))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(127, 127, 127))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(127, 127, 127))
    return palette


def apply_theme(app: QApplication, theme: str) -> None:
    init(app)
    use_dark = theme == "dark" or (theme == "auto" and system_is_dark())

    if use_dark:
        # Fusion plus our own checkbox indicators -- see _CheckIndicatorStyle
        # for why Fusion's own are invisible against this palette. The light
        # branch keeps the platform style's native indicators, which have no
        # such contrast problem.
        app.setStyle(_CheckIndicatorStyle("Fusion"))
        app.setPalette(_dark_palette())
    else:
        style_name = _default_style_name or "Fusion"
        app.setStyle(style_name)
        app.setPalette(app.style().standardPalette())
