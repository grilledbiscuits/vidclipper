"""Background keyframe scan.

Fast (stream copy) cuts can only land on keyframes, so the UI scans the
packet index once per file and uses it to show where cuts will really be.
"""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from .media import scan_keyframes


class KeyframeScanner(QThread):
    """Runs ffprobe's packet scan without blocking the UI."""

    ready = Signal(str, list)     # path, keyframe times
    failed = Signal(str, str)     # path, message

    def __init__(self, path: str, parent=None) -> None:
        super().__init__(parent)
        self._path = path
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def run(self) -> None:  # noqa: D102 - QThread entry point
        try:
            times = scan_keyframes(self._path, cancel=lambda: self._stop)
        except Exception as exc:  # ffprobe missing / unreadable file
            if not self._stop:
                self.failed.emit(self._path, str(exc))
            return
        if not self._stop:
            self.ready.emit(self._path, times)
