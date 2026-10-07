"""The reorderable clip list used by the Merge tab."""

from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListWidget, QListWidgetItem

from ..media import SUPPORTED_EXTS, MediaInfo
from ..thumbnails import THUMB_HEIGHT, THUMB_WIDTH
from ..timecode import fmt_short, human_size

PATH_ROLE = Qt.UserRole
INFO_ROLE = Qt.UserRole + 1


def placeholder_icon() -> QIcon:
    """Neutral tile shown until the real thumbnail arrives."""
    pixmap = QPixmap(THUMB_WIDTH, THUMB_HEIGHT)
    pixmap.fill(QColor("#0d0f14"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#3a4050"))
    painter.drawRect(0, 0, THUMB_WIDTH - 1, THUMB_HEIGHT - 1)
    painter.setPen(QColor("#5b6172"))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "...")
    painter.end()
    return QIcon(pixmap)


class ClipListWidget(QListWidget):
    """Drag to reorder, drop files in from anywhere to add them.

    Row order is the merge order, so the list is the feature: internal
    drags move clips, external drops append them.
    """

    files_dropped = Signal(list)     # paths dropped from outside
    order_changed = Signal()
    preview_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setIconSize(QSize(THUMB_WIDTH, THUMB_HEIGHT))
        self.setSpacing(2)
        self.setAlternatingRowColors(True)
        self.setToolTip(
            "Drag clips to change the merge order.\n"
            "Drop video files here to add them.\n"
            "Double-click a clip to play it in your video player.")
        self.itemDoubleClicked.connect(self._on_double_click)

    # -- items --------------------------------------------------------
    def add_clip(self, info: MediaInfo) -> QListWidgetItem:
        item = QListWidgetItem(placeholder_icon(), "")
        item.setData(PATH_ROLE, info.path)
        item.setData(INFO_ROLE, info)
        item.setToolTip(info.path)
        self.addItem(item)
        return item

    def clips(self) -> list[MediaInfo]:
        """Clips in their current visual order."""
        return [self.item(row).data(INFO_ROLE) for row in range(self.count())]

    def paths(self) -> list[str]:
        return [self.item(row).data(PATH_ROLE) for row in range(self.count())]

    def renumber(self) -> None:
        """Refresh the position labels after any reorder."""
        for row in range(self.count()):
            item = self.item(row)
            info: MediaInfo = item.data(INFO_ROLE)
            audio = info.audio_codec if info.has_audio else "no audio"
            item.setText(
                f"{row + 1}.  {os.path.basename(info.path)}\n"
                f"      {fmt_short(info.duration)}   |   "
                f"{info.width}x{info.height}   |   "
                f"{info.video_codec} + {audio}   |   "
                f"{human_size(info.size_bytes)}"
            )

    def set_thumbnail(self, path: str, data: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "PNG"):
            return
        icon = QIcon(pixmap)
        for row in range(self.count()):
            item = self.item(row)
            if item.data(PATH_ROLE) == path:
                item.setIcon(icon)

    def remove_selected(self) -> int:
        rows = sorted((self.row(item) for item in self.selectedItems()),
                      reverse=True)
        for row in rows:
            self.takeItem(row)
        if rows:
            self.renumber()
            self.order_changed.emit()
        return len(rows)

    def move_selected(self, delta: int) -> None:
        """Nudge the selection up (-1) or down (+1) one position."""
        rows = sorted(self.row(item) for item in self.selectedItems())
        if not rows:
            return
        if delta < 0 and rows[0] == 0:
            return
        if delta > 0 and rows[-1] == self.count() - 1:
            return
        for row in (rows if delta < 0 else reversed(rows)):
            item = self.takeItem(row)
            self.insertItem(row + delta, item)
            item.setSelected(True)
        self.renumber()
        self.order_changed.emit()

    def sort_by_name(self) -> None:
        items = [self.takeItem(0) for _ in range(self.count())]
        items.sort(key=lambda i: os.path.basename(i.data(PATH_ROLE)).lower())
        for item in items:
            self.addItem(item)
        self.renumber()
        self.order_changed.emit()

    def reverse_order(self) -> None:
        items = [self.takeItem(0) for _ in range(self.count())]
        for item in reversed(items):
            self.addItem(item)
        self.renumber()
        self.order_changed.emit()

    # -- events -------------------------------------------------------
    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.remove_selected()
            event.accept()
            return
        if event.modifiers() & Qt.ControlModifier:
            if event.key() == Qt.Key_Up:
                self.move_selected(-1)
                event.accept()
                return
            if event.key() == Qt.Key_Down:
                self.move_selected(1)
                event.accept()
                return
        super().keyPressEvent(event)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        self.preview_requested.emit(item.data(PATH_ROLE))

    @staticmethod
    def _video_paths(mime) -> list[str]:
        paths = []
        for url in mime.urls():
            if not url.isLocalFile():
                continue
            path = url.toLocalFile()
            if os.path.splitext(path)[1].lower() in SUPPORTED_EXTS:
                paths.append(path)
        return paths

    def dragEnterEvent(self, event) -> None:
        if event.source() is self:
            super().dragEnterEvent(event)
        elif event.mimeData().hasUrls() and self._video_paths(event.mimeData()):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.source() is self:
            super().dragMoveEvent(event)
        elif event.mimeData().hasUrls() and self._video_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if event.source() is self:
            super().dropEvent(event)
            self.renumber()
            self.order_changed.emit()
            return
        paths = self._video_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
