#!/usr/bin/env python3
"""Visual check on a real display: does the preview actually show frames?

Loads a video, seeks to a few positions and saves screenshots of the
whole window so the video widget's output can be inspected.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vidclipper.app import MainWindow  # noqa: E402


def main() -> int:
    sample = sys.argv[1]
    out_dir = sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication(sys.argv[:1])
    window = MainWindow()
    window.show()

    steps = []

    def shot(name: str) -> None:
        path = os.path.join(out_dir, name)
        window.grab().save(path)
        print("saved", path)

    def run_next() -> None:
        if steps:
            steps.pop(0)()

    def load() -> None:
        window.load_file(sample)
        QTimer.singleShot(2500, run_next)

    def paused_frame() -> None:
        window.player.set_position(4.0)
        QTimer.singleShot(1200, run_next)

    def capture_paused() -> None:
        shot("preview_paused_4s.png")
        window.player.toggle_play()
        QTimer.singleShot(1500, run_next)

    def capture_playing() -> None:
        shot("preview_playing.png")
        print("playing:", window.player.is_playing(),
              "position:", round(window.player.position(), 2))
        window.player.pause()
        window.player.set_position(12.0)
        window.add_split()
        window.player.set_position(22.0)
        window.add_split()
        QTimer.singleShot(1200, run_next)

    def capture_splits() -> None:
        shot("preview_with_splits.png")
        print("splits:", [round(s, 2) for s in window.model.splits])
        QTimer.singleShot(300, run_next)

    def finish() -> None:
        app.exit(0)

    steps.extend([paused_frame, capture_paused, capture_playing,
                  capture_splits, finish])
    QTimer.singleShot(500, load)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
