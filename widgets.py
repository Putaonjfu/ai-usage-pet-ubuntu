"""Native Qt scene and compositor-aware dragging; no external artwork required."""
import math
from PySide6.QtCore import QObject, QEvent, Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget


class WindowDrag(QObject):
    """Let Wayland/X11 own the gesture; use manual movement only off Wayland."""
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.offset = None
        self.native = False

    def attach(self, widget):
        widget.installEventFilter(self)
        widget.setCursor(Qt.CursorShape.OpenHandCursor)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            self.window.toggle()
            return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            handle = self.window.windowHandle()
            # Must run during the press event: Wayland needs its input serial.
            self.native = bool(handle and handle.startSystemMove())
            return True
        if kind == QEvent.Type.MouseMove and event.buttons() & Qt.MouseButton.LeftButton:
            if self.offset is not None and not self.native and not QApplication.platformName().startswith('wayland'):
                self.window.move(event.globalPosition().toPoint() - self.offset)
            return self.offset is not None
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self.offset = None
            self.native = False
            return True
        return super().eventFilter(watched, event)


class PetScene(QWidget):
    """Shared vector AI-core icon, with an outer quota/status ring."""
    def __init__(self):
        super().__init__()
        from pathlib import Path
        from PySide6.QtSvg import QSvgRenderer
        self.renderer = QSvgRenderer(str(Path(__file__).resolve().parent / 'assets' / 'pet.svg'), self)
        self.setFixedHeight(84)
        self.tick = 0
        self.busy = False
        self.remaining = None
        self.setToolTip('按住拖动 · 双击展开 / 收起')
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(120)

    def animate(self):
        self.tick += 1
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPen
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        p.translate((self.width() - side) / 2, (self.height() - side) / 2)
        p.scale(side / 88, side / 88)
        p.setPen(Qt.PenStyle.NoPen)
        p.setOpacity(0.95 + 0.05 * math.sin(self.tick / 10))
        self.renderer.render(p, QRectF(6, 6, 76, 76))
        p.setOpacity(1)
        ring = QRectF(3, 3, 82, 82)
        p.setBrush(Qt.BrushStyle.NoBrush)
        if self.remaining is not None:
            p.setPen(QPen(QColor('#d4dce5'), 2.5))
            p.drawEllipse(ring)
            p.setPen(QPen(QColor('#c58b9c' if self.remaining <= 10 else '#54bcd4'), 3,
                          Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, 90 * 16, -round(self.remaining / 100 * 360 * 16))
        if self.busy:
            p.setPen(QPen(QColor('#83d6ed'), 2.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(ring, -self.tick * 12 * 16, 50 * 16)
        p.end()
