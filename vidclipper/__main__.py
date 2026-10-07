"""Entry point: preflight checks, then the main window.

Run with:  python -m vidclipper [video-file]
"""

from __future__ import annotations

import os
import sys

from . import APP_NAME, __version__

FFMPEG_HELP = (
    "VidClipper needs the ffmpeg and ffprobe programs to read and cut "
    "video, and could not find them.\n\n"
    "Install them with:\n\n"
    "    sudo apt install ffmpeg\n\n"
    "then start VidClipper again."
)


def _fatal(title: str, message: str) -> int:
    """Report a startup failure through a dialog when possible."""
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance() or QApplication(sys.argv)
        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle(title)
        box.setText(message)
        box.exec()
        del app
    except Exception:                      # no display / no Qt: fall back
        pass
    print(f"{title}: {message}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    if "--version" in argv:
        print(f"{APP_NAME} {__version__}")
        return 0

    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError as exc:
        return _fatal(
            "PySide6 is missing",
            "The Qt libraries could not be imported:\n\n"
            f"{exc}\n\n"
            "Run ./run.sh once from a terminal to rebuild the virtual "
            "environment.")

    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName("vidclipper")
    QApplication.setDesktopFileName("vidclipper")
    app = QApplication(argv)

    icon_path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "assets", "vidclipper.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # ffmpeg/ffprobe are required for everything; check before building UI.
    from .media import FFmpegMissingError, find_binaries

    try:
        find_binaries()
    except FFmpegMissingError:
        from PySide6.QtWidgets import QMessageBox

        box = QMessageBox()
        box.setIcon(QMessageBox.Critical)
        box.setWindowTitle("ffmpeg not found")
        box.setText(FFMPEG_HELP)
        box.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box.exec()
        print(FFMPEG_HELP, file=sys.stderr)
        return 2

    from .app import MainWindow

    window = MainWindow()
    window.show()

    # Any trailing path argument is opened straight away.
    for arg in argv[1:]:
        if not arg.startswith("-") and os.path.isfile(arg):
            window.load_file(arg)
            break

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
