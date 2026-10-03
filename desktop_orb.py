"""Minimal JARVIS desktop orb prototype.

Run with: py desktop_orb.py
This is only the visual shell; voice and PC control are integrated in later stages.
"""
import math
import sys

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QApplication, QMenu, QWidget


class JarvisOrb(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("JARVIS")
        self.setFixedSize(220, 220)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.phase = 0.0
        self.drag_offset = None

        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 36, screen.bottom() - self.height() - 70)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.animate)
        self.timer.start(33)

    def animate(self):
        self.phase = (self.phase + 1.6) % 360
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        pulse = 2.5 * math.sin(math.radians(self.phase * 2))
        radius = 69 + pulse

        # Soft outer glow
        glow = QRadialGradient(cx, cy, 100)
        glow.setColorAt(0.0, QColor(0, 180, 255, 55))
        glow.setColorAt(0.62, QColor(0, 120, 255, 20))
        glow.setColorAt(1.0, QColor(0, 70, 180, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(glow)
        p.drawEllipse(QPoint(int(cx), int(cy)), 99, 99)

        # Dark glass core
        core = QRadialGradient(cx - 18, cy - 22, radius * 1.5)
        core.setColorAt(0.0, QColor(18, 70, 110, 245))
        core.setColorAt(0.58, QColor(4, 22, 48, 250))
        core.setColorAt(1.0, QColor(1, 7, 20, 250))
        p.setBrush(core)
        p.setPen(QPen(QColor(60, 210, 255, 185), 1.5))
        p.drawEllipse(QPoint(int(cx), int(cy)), int(radius), int(radius))

        # Rotating segmented energy ring
        ring = QConicalGradient(cx, cy, self.phase)
        ring.setColorAt(0.0, QColor(80, 240, 255, 20))
        ring.setColorAt(0.18, QColor(70, 220, 255, 240))
        ring.setColorAt(0.42, QColor(20, 110, 255, 45))
        ring.setColorAt(0.7, QColor(0, 240, 255, 220))
        ring.setColorAt(1.0, QColor(80, 240, 255, 20))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(ring, 3.0))
        p.drawEllipse(QPoint(int(cx), int(cy)), int(radius + 7), int(radius + 7))

        # Inner waveform-like arcs
        p.setPen(QPen(QColor(80, 220, 255, 150), 1.2))
        for i in range(3):
            r = 28 + i * 12 + 2 * math.sin(math.radians(self.phase + i * 55))
            p.drawArc(int(cx-r), int(cy-r), int(r*2), int(r*2), int(self.phase*16 + i*120), 105*16)

        # Central light
        center = QRadialGradient(cx, cy, 19)
        center.setColorAt(0.0, QColor(190, 255, 255, 240))
        center.setColorAt(0.35, QColor(40, 200, 255, 170))
        center.setColorAt(1.0, QColor(0, 120, 255, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(center)
        p.drawEllipse(QPoint(int(cx), int(cy)), 19, 19)
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
            # Placeholder for the future voice/listening panel.
            self.setToolTip("JARVIS: fitur suara akan dihubungkan pada tahap berikutnya.")
            self.setToolTipDuration(2500)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("JARVIS")
    orb = JarvisOrb()
    orb.show()
    sys.exit(app.exec())
