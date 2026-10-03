"""JARVIS floating holographic core for Windows.

Run with: py desktop_orb.py
Visual prototype only; voice and PC control will be connected in later stages.
"""
import math
import sys

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QConicalGradient, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QWidget


class JarvisOrb(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JARVIS")
        self.setFixedSize(390, 390)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.phase = 0.0
        self.drag_offset = None

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 28, screen.bottom() - self.height() - 54)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(33)

    def animate(self):
        self.phase = (self.phase + 1.15) % 360
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2 - 8
        phase = self.phase
        pulse = 3.0 * math.sin(math.radians(phase * 2))
        core_r = 76 + pulse

        # Broad, transparent amber glow.
        glow = QRadialGradient(cx, cy, 166)
        glow.setColorAt(0.0, QColor(255, 111, 18, 48))
        glow.setColorAt(0.38, QColor(255, 91, 10, 22))
        glow.setColorAt(1.0, QColor(255, 80, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPoint(int(cx), int(cy)), 165, 165)

        # Fine circular telemetry grid.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for r, alpha, width in ((142, 42, 1), (151, 65, 1), (163, 28, 1)):
            p.setPen(QPen(QColor(60, 181, 255, alpha), width))
            p.drawEllipse(QPoint(int(cx), int(cy)), r, r)

        # Orbital ellipses: rotating-looking tilted arcs.
        p.save()
        p.translate(cx, cy)
        for i, (rx, ry, start, span) in enumerate([
            (128, 54, int(phase * 16), 238 * 16),
            (111, 43, int(-phase * 13 + 850), 265 * 16),
            (91, 36, int(phase * 19 + 1750), 205 * 16),
        ]):
            alpha = 170 if i == 0 else 105
            p.setPen(QPen(QColor(255, 153 + i * 18, 50, alpha), 1.5 if i == 0 else 1.0))
            p.save()
            p.rotate((phase * (0.24 if i % 2 == 0 else -0.31)) + i * 53)
            p.drawArc(-rx, -ry, rx * 2, ry * 2, start, span)
            p.restore()
        p.restore()

        # Central dark plasma sphere.
        core = QRadialGradient(cx - 22, cy - 28, core_r * 1.48)
        core.setColorAt(0.0, QColor(255, 197, 91, 250))
        core.setColorAt(0.20, QColor(255, 116, 22, 250))
        core.setColorAt(0.48, QColor(105, 35, 13, 248))
        core.setColorAt(0.82, QColor(20, 14, 21, 250))
        core.setColorAt(1.0, QColor(2, 8, 18, 248))
        p.setBrush(core)
        p.setPen(QPen(QColor(255, 171, 67, 220), 1.4))
        p.drawEllipse(QPoint(int(cx), int(cy)), int(core_r), int(core_r))

        # Fractured luminous rings around the core.
        ring = QConicalGradient(cx, cy, phase)
        ring.setColorAt(0.0, QColor(255, 225, 150, 245))
        ring.setColorAt(0.13, QColor(255, 111, 23, 30))
        ring.setColorAt(0.25, QColor(255, 169, 55, 235))
        ring.setColorAt(0.42, QColor(255, 87, 18, 40))
        ring.setColorAt(0.61, QColor(255, 218, 125, 245))
        ring.setColorAt(0.78, QColor(255, 106, 20, 45))
        ring.setColorAt(1.0, QColor(255, 225, 150, 245))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ring, 3.2))
        p.drawEllipse(QPoint(int(cx), int(cy)), int(core_r + 8), int(core_r + 8))

        # Segmented outer ticks.
        for i in range(64):
            angle = math.radians(i * 360 / 64 + phase * 0.45)
            inner = 151 if i % 4 else 145
            outer = 157 if i % 4 else 165
            x1, y1 = cx + math.cos(angle) * inner, cy + math.sin(angle) * inner
            x2, y2 = cx + math.cos(angle) * outer, cy + math.sin(angle) * outer
            color = QColor(255, 163, 72, 150 if i % 4 == 0 else 65)
            p.setPen(QPen(color, 1.4 if i % 4 == 0 else 0.8))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Blue/orange floating telemetry labels, deliberately small and discreet.
        p.setFont(QFont("Consolas", 7, QFont.Weight.DemiBold))
        p.setPen(QColor(112, 211, 255, 210))
        p.drawText(16, 48, "SYSTEM  /  ONLINE")
        p.drawText(246, 48, "CORE  /  STANDBY")
        p.setPen(QColor(255, 176, 90, 210))
        p.drawText(18, 345, "J.A.R.V.I.S  //  CORE")
        p.setPen(QColor(112, 211, 255, 190))
        p.drawText(246, 345, "VOICE LINK  READY")

        # Tiny decorative performance bars, no fake live system metrics.
        for i in range(12):
            h = 4 + (i % 4) * 3
            x = 20 + i * 6
            p.setPen(QPen(QColor(69, 188, 255, 115), 2))
            p.drawLine(x, 62, x, 62 + h)
            p.setPen(QPen(QColor(255, 152, 55, 105), 2))
            p.drawLine(x, 323, x, 323 - h)

        # Bright central plasma light.
        center = QRadialGradient(cx - 7, cy - 10, 35)
        center.setColorAt(0.0, QColor(255, 242, 190, 245))
        center.setColorAt(0.20, QColor(255, 177, 68, 210))
        center.setColorAt(0.65, QColor(255, 97, 16, 75))
        center.setColorAt(1.0, QColor(255, 80, 0, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(center)
        p.drawEllipse(QPoint(int(cx - 7), int(cy - 10)), 35, 35)
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton:
            menu = QMenu(self)
            hide_action = QAction("Sembunyikan JARVIS", self)
            quit_action = QAction("Keluar", self)
            hide_action.triggered.connect(self.hide)
            quit_action.triggered.connect(QApplication.quit)
            menu.addAction(hide_action)
            menu.addSeparator()
            menu.addAction(quit_action)
            menu.exec(event.globalPosition().toPoint())

    def mouseMoveEvent(self, event):
        if self.drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event):
        self.drag_offset = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setToolTip("JARVIS: voice interaction will be connected in the next stage.")
            self.setToolTipDuration(2500)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    orb = JarvisOrb()
    orb.show()
    sys.exit(app.exec())
