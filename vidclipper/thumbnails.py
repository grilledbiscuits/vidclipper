"""Background thumbnail extraction for the merge list.

Seeing a frame from each clip is what makes drag-to-reorder usable, so
thumbnails are pulled with ffmpeg on a worker thread and piped straight
into memory - no temporary image files.
"""

from __future__ import annotations

import subprocess

from PySide6.QtCore import QThread, Signal

THUMB_WIDTH = 160
THUMB_HEIGHT = 90


class ThumbnailWorker(QThread):
    """Extracts one frame per clip and hands it back as PNG bytes."""

    ready = Signal(str, bytes)      # path, PNG data

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._queue: list[tuple[str, float]] = []
        self._done: set[str] = set()
        self._stop = False
        self._proc: subprocess.Popen | None = None

    def enqueue(self, path: str, duration: float) -> None:
        """Add a clip to the queue (each path is only rendered once)."""
        if path in self._done or any(path == p for p, _ in self._queue):
            return
        self._queue.append((path, duration))

    def stop(self) -> None:
        """Abandon the queue and kill any ffmpeg still running."""
        self._stop = True
        self._queue.clear()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass

    def run(self) -> None:  # noqa: D102 - QThread entry point
        from .media import find_binaries

        try:
            ffmpeg, _ = find_binaries()
        except RuntimeError:
            return

        while self._queue and not self._stop:
            path, duration = self._queue.pop(0)
            data = self._grab_frame(ffmpeg, path, duration)
            self._done.add(path)
            if data and not self._stop:
                self.ready.emit(path, data)

    def _grab_frame(self, ffmpeg: str, path: str, duration: float) -> bytes:
        """A frame from ~10% into the clip, scaled and letterboxed."""
        return _grab_frame(ffmpeg, path, duration, self)


def _grab_frame(ffmpeg: str, path: str, duration: float,
                worker: "ThumbnailWorker | None" = None) -> bytes:
    """Extract one PNG frame, tracking the process so it can be killed."""
    seek = max(0.0, min(duration * 0.1, max(0.0, duration - 0.2)))
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-ss", f"{seek:.3f}", "-i", path,
        "-frames:v", "1",
        "-vf", (f"scale={THUMB_WIDTH}:{THUMB_HEIGHT}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={THUMB_WIDTH}:{THUMB_HEIGHT}:(ow-iw)/2:(oh-ih)/2:"
                "color=#0d0f14"),
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError:
        return b""
    if worker is not None:
        worker._proc = proc
    try:
        data, _ = proc.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return b""
    finally:
        if worker is not None:
            worker._proc = None
    return data if proc.returncode == 0 else b""
