"""The timeline strip: ruler, segment bands, split markers and playhead."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QAction, QBrush, QColor, QFont, QLinearGradient,
                           QPainter, QPainterPath, QPen)
from PySide6.QtWidgets import QMenu, QSizePolicy, QWidget

from ..model import Segment, SplitModel
from ..timecode import fmt_short

RULER_H = 20
BAND_H = 52
HANDLE_H = 14
SIDE_PAD = 10

SEGMENT_COLORS = (
    QColor("#3b82f6"), QColor("#8b5cf6"), QColor("#06b6d4"),
    QColor("#10b981"), QColor("#f59e0b"), QColor("#ef4444"),
    QColor("#ec4899"), QColor("#84cc16"),
)
BG = QColor("#15171c")
RULER_BG = QColor("#1c1f26")
TEXT_DIM = QColor("#8b93a3")
TEXT = QColor("#e6e9ef")
MARKER = QColor("#ff4d4f")
MARKER_HOT = QColor("#ffd166")
PLAYHEAD = QColor("#ffffff")
KEYFRAME = QColor(255, 255, 255, 40)


class TimelineWidget(QWidget):
    """Click to seek, drag markers to move them, right-click to remove."""

    seek_requested = Signal(float)
    split_removed = Signal(int)
    segment_clicked = Signal(int)
    remove_all_requested = Signal()
    add_split_requested = Signal()

    def __init__(self, model: SplitModel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._model = model
        self._position = 0.0
        self._segments: list[Segment] = []
        self._keyframes: list[float] = []
        self._show_keyframes = True
        self._hover_marker: int | None = None
        self._drag_marker: int | None = None
        self._scrubbing = False
        self._selected_segment = 0

        self.setMinimumHeight(RULER_H + BAND_H + HANDLE_H + 12)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.DefaultContextMenu)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)

        model.changed.connect(self.refresh)

    # -- state --------------------------------------------------------
    def refresh(self) -> None:
        self.update()

    def set_segments(self, segments: list[Segment]) -> None:
        self._segments = segments
        self.update()

    def set_position(self, seconds: float) -> None:
        if abs(seconds - self._position) < 1e-4:
            return
        self._position = max(0.0, float(seconds))
        self.update()

    def set_keyframes(self, keyframes: list[float]) -> None:
        self._keyframes = keyframes
        self.update()

    def set_show_keyframes(self, show: bool) -> None:
        self._show_keyframes = show
        self.update()

    def set_selected_segment(self, index: int) -> None:
        self._selected_segment = index
        self.update()

    # -- geometry -----------------------------------------------------
    @property
    def _track_w(self) -> float:
        return max(1.0, self.width() - 2 * SIDE_PAD)

    def _x_for(self, seconds: float) -> float:
        duration = self._model.duration
        if duration <= 0:
            return SIDE_PAD
        return SIDE_PAD + (seconds / duration) * self._track_w

    def _time_for(self, x: float) -> float:
        duration = self._model.duration
        if duration <= 0:
            return 0.0
        ratio = (x - SIDE_PAD) / self._track_w
        return min(max(ratio, 0.0), 1.0) * duration

    def _band_rect(self) -> QRectF:
        return QRectF(SIDE_PAD, RULER_H + 6, self._track_w, BAND_H)

    def _marker_hit(self, pos: QPointF) -> int | None:
        """Index of the split marker under `pos`, if any."""
        if self._model.duration <= 0:
            return None
        best, best_dx = None, 7.0
        for i, value in enumerate(self._model.splits):
            dx = abs(self._x_for(value) - pos.x())
            if dx <= best_dx:
                best, best_dx = i, dx
        return best

    # -- events -------------------------------------------------------
    def mousePressEvent(self, event) -> None:
        if self._model.duration <= 0:
            return
        if event.button() == Qt.LeftButton:
            hit = self._marker_hit(event.position())
            if hit is not None:
                self._drag_marker = hit
                self.setCursor(Qt.SizeHorCursor)
            else:
                self._scrubbing = True
                seconds = self._time_for(event.position().x())
                self._select_segment_at(seconds)
                self.seek_requested.emit(seconds)
        event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._model.duration <= 0:
            return
        if self._drag_marker is not None:
            seconds = self._time_for(event.position().x())
            self._model.move_split(self._drag_marker, seconds,
                                   push_undo=not self._dragged)
            self._dragged = True
            self.seek_requested.emit(self._model.splits[self._drag_marker])
        elif self._scrubbing:
            self.seek_requested.emit(self._time_for(event.position().x()))
        else:
            hover = self._marker_hit(event.position())
            if hover != self._hover_marker:
                self._hover_marker = hover
                self.setCursor(Qt.SizeHorCursor if hover is not None
                               else Qt.PointingHandCursor)
                self.update()
        event.accept()

    _dragged = False

    def mouseReleaseEvent(self, event) -> None:
        self._drag_marker = None
        self._dragged = False
        self._scrubbing = False
        self.setCursor(Qt.PointingHandCursor)
        event.accept()

    def leaveEvent(self, event) -> None:
        if self._hover_marker is not None:
            self._hover_marker = None
            self.update()
        super().leaveEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._model.duration <= 0:
            return
        menu = QMenu(self)
        hit = self._marker_hit(QPointF(event.pos()))
        if hit is not None:
            action = QAction(f"Remove this split point", self)
            action.triggered.connect(lambda: self.split_removed.emit(hit))
            menu.addAction(action)
        add = QAction("Add split at playhead", self)
        add.triggered.connect(self.add_split_requested.emit)
        menu.addAction(add)
        if self._model.count():
            clear = QAction("Remove all split points", self)
            clear.triggered.connect(self.remove_all_requested.emit)
            menu.addAction(clear)
        menu.exec(event.globalPos())

    def _select_segment_at(self, seconds: float) -> None:
        for seg in self._segments:
            if seg.start <= seconds < seg.end:
                self.segment_clicked.emit(seg.index)
                return

    # -- painting -----------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillRect(self.rect(), BG)

        duration = self._model.duration
        band = self._band_rect()

        if duration <= 0:
            painter.setPen(TEXT_DIM)
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Open a video to start splitting")
            return

        self._paint_ruler(painter, duration)
        self._paint_segments(painter, band)
        self._paint_keyframes(painter, band)
        self._paint_markers(painter, band)
        self._paint_playhead(painter, band)

    def _paint_ruler(self, painter: QPainter, duration: float) -> None:
        painter.fillRect(QRectF(0, 0, self.width(), RULER_H), RULER_BG)
        step = _nice_step(duration, self._track_w)
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        painter.setFont(font)

        value = 0.0
        while value <= duration + 1e-6:
            x = self._x_for(value)
            painter.setPen(QPen(QColor("#3a4050"), 1))
            painter.drawLine(QPointF(x, RULER_H - 6), QPointF(x, RULER_H))
            painter.setPen(TEXT_DIM)
            # Keep the first and last labels inside the widget.
            left = min(max(x - 30, 0.0), self.width() - 60.0)
            painter.drawText(QRectF(left, 1, 60, RULER_H - 6),
                             Qt.AlignCenter, fmt_short(value))
            value += step

    def _paint_segments(self, painter: QPainter, band: QRectF) -> None:
        segments = self._segments or self._model.segments()
        for seg in segments:
            x0 = self._x_for(seg.start)
            x1 = self._x_for(seg.end)
            rect = QRectF(x0 + 1, band.top(), max(2.0, x1 - x0 - 2), band.height())
            color = SEGMENT_COLORS[(seg.index - 1) % len(SEGMENT_COLORS)]
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            selected = seg.index == self._selected_segment
            gradient.setColorAt(0.0, color.lighter(135 if selected else 115))
            gradient.setColorAt(1.0, color.darker(135 if not selected else 110))
            path = QPainterPath()
            path.addRoundedRect(rect, 5, 5)
            painter.fillPath(path, QBrush(gradient))
            if selected:
                painter.setPen(QPen(QColor("#ffffff"), 2))
                painter.drawPath(path)

            if rect.width() > 46:
                painter.setPen(QColor(255, 255, 255, 235))
                painter.drawText(rect, Qt.AlignCenter,
                                 f"{seg.index}\n{fmt_short(seg.duration)}")
            elif rect.width() > 16:
                painter.setPen(QColor(255, 255, 255, 235))
                painter.drawText(rect, Qt.AlignCenter, str(seg.index))

    def _paint_keyframes(self, painter: QPainter, band: QRectF) -> None:
        if not (self._show_keyframes and self._keyframes):
            return
        # Skip the overlay when keyframes are denser than the pixels available.
        if len(self._keyframes) > self._track_w / 3:
            return
        painter.setPen(QPen(KEYFRAME, 1))
        for value in self._keyframes:
            x = self._x_for(value)
            painter.drawLine(QPointF(x, band.bottom() - 8),
                             QPointF(x, band.bottom() - 2))

    def _paint_markers(self, painter: QPainter, band: QRectF) -> None:
        top = band.bottom() + 2
        for i, value in enumerate(self._model.splits):
            x = self._x_for(value)
            hot = i in (self._hover_marker, self._drag_marker)
            color = MARKER_HOT if hot else MARKER
            painter.setPen(QPen(color, 2 if hot else 1.5))
            painter.drawLine(QPointF(x, band.top() - 4), QPointF(x, top))
            path = QPainterPath()
            path.moveTo(x, top)
            path.lineTo(x - 6, top + HANDLE_H - 4)
            path.lineTo(x + 6, top + HANDLE_H - 4)
            path.closeSubpath()
            painter.fillPath(path, QBrush(color))

    def _paint_playhead(self, painter: QPainter, band: QRectF) -> None:
        x = self._x_for(self._position)
        painter.setPen(QPen(PLAYHEAD, 1.5))
        painter.drawLine(QPointF(x, 0), QPointF(x, band.bottom() + 2))
        head = QPainterPath()
        head.moveTo(x, band.top() - 6)
        head.lineTo(x - 5, band.top() - 14)
        head.lineTo(x + 5, band.top() - 14)
        head.closeSubpath()
        painter.fillPath(head, QBrush(PLAYHEAD))


def _nice_step(duration: float, width: float) -> float:
    """Pick a ruler interval that keeps labels ~70px apart."""
    target = max(1.0, duration / max(1.0, width / 70.0))
    for step in (0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900,
                 1800, 3600, 7200):
        if step >= target:
            return float(step)
    return 7200.0
