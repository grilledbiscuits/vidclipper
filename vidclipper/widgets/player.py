"""Video preview: QtMultimedia player plus transport controls."""

from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QSlider, QVBoxLayout, QWidget)

from ..timecode import fmt_tc


class PlayerWidget(QWidget):
    """Preview player with play/pause, seek bar and frame stepping."""

    position_changed = Signal(float)     # seconds
    duration_changed = Signal(float)     # seconds
    error_occurred = Signal(str)
    playing_changed = Signal(bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fps = 25.0
        self._duration = 0.0
        self._user_seeking = False
        self._first_frame_pending = False

        self._video = QVideoWidget(self)
        self._video.setMinimumHeight(240)
        self._video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video.setStyleSheet("background:#000;")

        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self._player = QMediaPlayer(self)
        self._player.setVideoOutput(self._video)
        self._player.setAudioOutput(self._audio)

        self._player.durationChanged.connect(self._on_duration)
        self._player.positionChanged.connect(self._on_position)
        self._player.errorOccurred.connect(self._on_error)
        self._player.playbackStateChanged.connect(self._on_state)

        # QMediaPlayer only reports position every ~100 ms; poll for a
        # playhead that tracks smoothly during playback.
        self._ticker = QTimer(self)
        self._ticker.setInterval(33)
        self._ticker.timeout.connect(self._tick)

        self._build_controls()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._video, 1)
        layout.addLayout(self._controls)

    # -- ui -----------------------------------------------------------
    def _build_controls(self) -> None:
        self.play_btn = QPushButton("▶  Play")
        self.play_btn.setToolTip("Play / Pause (Space)")
        self.play_btn.setFixedWidth(96)
        self.play_btn.clicked.connect(self.toggle_play)

        self.back_btn = QPushButton("◀|")
        self.back_btn.setToolTip("Previous frame (Left arrow)")
        self.back_btn.setFixedWidth(44)
        self.back_btn.clicked.connect(lambda: self.step_frames(-1))

        self.fwd_btn = QPushButton("|▶")
        self.fwd_btn.setToolTip("Next frame (Right arrow)")
        self.fwd_btn.setFixedWidth(44)
        self.fwd_btn.clicked.connect(lambda: self.step_frames(1))

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setTracking(True)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)

        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self.time_label.setObjectName("timecode")
        self.time_label.setMinimumWidth(210)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(80)
        self.volume.setFixedWidth(90)
        self.volume.setToolTip("Volume")
        self.volume.valueChanged.connect(
            lambda v: self._audio.setVolume(v / 100.0))

        self._controls = QHBoxLayout()
        self._controls.setSpacing(6)
        for widget in (self.play_btn, self.back_btn, self.fwd_btn):
            self._controls.addWidget(widget)
        self._controls.addWidget(self.slider, 1)
        self._controls.addWidget(self.time_label)
        self._controls.addWidget(QLabel("\U0001f50a"))
        self._controls.addWidget(self.volume)
        self.set_enabled(False)

    def set_enabled(self, enabled: bool) -> None:
        for widget in (self.play_btn, self.back_btn, self.fwd_btn, self.slider):
            widget.setEnabled(enabled)

    # -- playback -----------------------------------------------------
    def load(self, path: str, fps: float = 25.0) -> None:
        self._fps = fps if fps > 0 else 25.0
        self._first_frame_pending = True
        self._video.videoSink().videoFrameChanged.connect(self._on_first_frame)
        self._player.setSource(QUrl.fromLocalFile(path))
        self.set_enabled(True)
        # Start playing so a frame is decoded, then pause on the first one
        # that arrives - that leaves a real preview image on screen instead
        # of a black rectangle.
        self._player.play()
        QTimer.singleShot(2000, self._pause_first_frame_fallback)

    def _on_first_frame(self, frame) -> None:
        if not self._first_frame_pending or not frame.isValid():
            return
        self._first_frame_pending = False
        self._player.pause()
        self._player.setPosition(0)
        try:
            self._video.videoSink().videoFrameChanged.disconnect(
                self._on_first_frame)
        except (RuntimeError, TypeError):
            pass

    def _pause_first_frame_fallback(self) -> None:
        """Stop playback even if no frame ever reached the sink."""
        if self._first_frame_pending:
            self._first_frame_pending = False
            self._player.pause()
            self._player.setPosition(0)

    def clear(self) -> None:
        self._player.stop()
        self._player.setSource(QUrl())
        self._duration = 0.0
        self.set_enabled(False)
        self.slider.setRange(0, 0)
        self.time_label.setText("00:00:00.000 / 00:00:00.000")

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlayingState

    def toggle_play(self) -> None:
        if not self._player.source().isEmpty():
            self._player.pause() if self.is_playing() else self._player.play()

    def pause(self) -> None:
        if self.is_playing():
            self._player.pause()

    def position(self) -> float:
        return self._player.position() / 1000.0

    def set_position(self, seconds: float) -> None:
        seconds = min(max(0.0, float(seconds)), self._duration or float("inf"))
        self._player.setPosition(int(round(seconds * 1000)))
        self._emit_position(seconds)

    def step_frames(self, frames: int) -> None:
        """Nudge the playhead by whole frames (pauses playback first)."""
        self.pause()
        delta = frames / (self._fps if self._fps > 0 else 25.0)
        self.set_position(self.position() + delta)

    def nudge(self, seconds: float) -> None:
        self.pause()
        self.set_position(self.position() + seconds)

    # -- signals ------------------------------------------------------
    def _on_duration(self, ms: int) -> None:
        self._duration = ms / 1000.0
        self.slider.setRange(0, max(0, ms))
        self.duration_changed.emit(self._duration)
        self._update_label(self.position())

    def _on_position(self, ms: int) -> None:
        if not self._user_seeking:
            self._emit_position(ms / 1000.0)

    def _tick(self) -> None:
        if not self._user_seeking:
            self._emit_position(self.position())

    def _emit_position(self, seconds: float) -> None:
        if not self._user_seeking:
            self.slider.setValue(int(round(seconds * 1000)))
        self._update_label(seconds)
        self.position_changed.emit(seconds)

    def _update_label(self, seconds: float) -> None:
        self.time_label.setText(
            f"{fmt_tc(seconds)} / {fmt_tc(self._duration)}")

    def _on_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        self.play_btn.setText("⏸  Pause" if playing else "▶  Play")
        self._ticker.start() if playing else self._ticker.stop()
        self.playing_changed.emit(playing)

    def _on_error(self, error, message: str = "") -> None:
        if error == QMediaPlayer.NoError:
            return
        self.error_occurred.emit(
            message or "The preview player could not open this file."
        )

    # -- slider -------------------------------------------------------
    def _on_slider_pressed(self) -> None:
        self._user_seeking = True

    def _on_slider_moved(self, value: int) -> None:
        self._update_label(value / 1000.0)
        self.position_changed.emit(value / 1000.0)
        self._player.setPosition(value)

    def _on_slider_released(self) -> None:
        self._user_seeking = False
        self._player.setPosition(self.slider.value())
        self._emit_position(self.slider.value() / 1000.0)
