"""Merging: join several clips back into one file.

The mirror image of exporter.py. Fast mode concatenates the existing
streams with ffmpeg's concat demuxer (lossless, near-instant, but every
clip must share the same codec and format); Precise mode re-encodes
through the concat filter, normalising resolution, frame rate and audio
so clips from different sources can be joined.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from .media import MediaInfo, find_binaries, output_extension

# Everything is normalised to these when re-encoding.
DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNEL_LAYOUT = "stereo"


class MergeError(RuntimeError):
    """Raised for problems detected before ffmpeg is launched."""


@dataclass
class MergeSettings:
    """Where the joined file goes and how it is produced."""

    clips: list[MediaInfo]
    out_path: str
    precise: bool = False
    crf: int = 18
    preset: str = "veryfast"
    audio_bitrate: str = "192k"

    @property
    def total_duration(self) -> float:
        return sum(clip.duration for clip in self.clips)

    @property
    def total_bytes(self) -> int:
        return sum(clip.size_bytes for clip in self.clips)


@dataclass
class MergeResult:
    out_path: str = ""
    ok: bool = False
    error: str = ""
    cancelled: bool = False


# -- compatibility ----------------------------------------------------
def compatibility_problems(clips: list[MediaInfo]) -> list[str]:
    """Reasons these clips cannot be joined without re-encoding.

    An empty list means fast (lossless) merging will work.
    """
    if len(clips) < 2:
        return []
    problems: list[str] = []
    def differing(label: str, getter) -> None:
        values = {getter(clip) for clip in clips}
        if len(values) > 1:
            shown = ", ".join(str(v) if v not in ("", 0) else "none"
                              for v in sorted(values, key=str))
            problems.append(f"{label} differs between clips ({shown})")

    differing("Video codec", lambda c: c.video_codec)
    differing("Resolution", lambda c: f"{c.width}x{c.height}")
    differing("Pixel format", lambda c: c.pix_fmt)
    differing("Audio codec", lambda c: c.audio_codec)
    differing("Audio sample rate", lambda c: c.sample_rate)
    differing("Audio channel count", lambda c: c.channels)

    if any(c.has_audio for c in clips) and not all(c.has_audio for c in clips):
        problems.append("some clips have no audio track")
    return problems


def suggested_output_name(clips: list[MediaInfo], precise: bool) -> str:
    """A sensible default filename for the joined file."""
    if not clips:
        return "merged.mp4"
    base = clips[0].basename
    ext = merge_extension(clips, precise)
    return f"{base}_merged{ext}"


def merge_extension(clips: list[MediaInfo], precise: bool) -> str:
    """Container for the output file."""
    if not clips:
        return ".mp4"
    source_ext = clips[0].ext
    if not precise:
        # Copying streams: every clip shares a format, so the first
        # clip's container is always a valid home for them.
        return source_ext
    return output_extension(source_ext, precise=True)


def target_geometry(clips: list[MediaInfo]) -> tuple[int, int, float]:
    """Resolution and frame rate to normalise to when re-encoding.

    The largest frame among the clips is used, so nothing is upscaled
    beyond its source resolution more than necessary.
    """
    if not clips:
        return 1920, 1080, 30.0
    widest = max(clips, key=lambda c: c.width * c.height)
    width = max(2, widest.width - (widest.width % 2))
    height = max(2, widest.height - (widest.height % 2))
    fps = max((c.fps for c in clips if c.fps > 0), default=30.0)
    return width, height, min(fps, 120.0)


# -- planning ---------------------------------------------------------
def validate(settings: MergeSettings) -> None:
    """Raise MergeError if this merge cannot possibly work."""
    clips = settings.clips
    if len(clips) < 2:
        raise MergeError(
            "Add at least two clips to merge.\n\n"
            "Drag video files into the list, or use Add Files."
        )
    out_path = settings.out_path
    if not out_path:
        raise MergeError("Choose where to save the merged file.")

    out_dir = os.path.dirname(out_path)
    if not out_dir or not os.path.isdir(out_dir):
        raise MergeError(f"The output folder does not exist:\n{out_dir}")
    if not os.access(out_dir, os.W_OK):
        raise MergeError(f"The output folder is not writable:\n{out_dir}")

    for clip in clips:
        if os.path.abspath(clip.path) == os.path.abspath(out_path):
            raise MergeError(
                "The output file is one of the clips being merged.\n\n"
                "Choose a different name so the source is not overwritten."
            )
        if not os.path.isfile(clip.path):
            raise MergeError(f"This clip is no longer on disk:\n{clip.path}")

    if not settings.precise:
        problems = compatibility_problems(clips)
        if problems:
            raise MergeError(
                "These clips cannot be joined losslessly because "
                "their formats differ:\n\n  - "
                + "\n  - ".join(problems)
                + "\n\nSwitch to Precise mode to re-encode them into one "
                  "consistent file."
            )

    check_free_space(settings)


def check_free_space(settings: MergeSettings) -> None:
    needed = int(settings.total_bytes * 1.1) + 1_000_000
    try:
        free = shutil.disk_usage(os.path.dirname(settings.out_path)).free
    except OSError:
        return
    if free < needed:
        from .timecode import human_size

        raise MergeError(
            "Not enough free disk space for the merged file.\n\n"
            f"Estimated output: {human_size(needed)}\n"
            f"Free space:       {human_size(free)}"
        )


# -- command building -------------------------------------------------
def write_concat_list(clips: list[MediaInfo], directory: str) -> str:
    """Write the concat demuxer's playlist and return its path."""
    handle = tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", prefix="vidclipper-concat-",
        dir=directory, delete=False, encoding="utf-8")
    with handle:
        for clip in clips:
            # The demuxer quotes paths with ', so ' inside one is escaped.
            escaped = os.path.abspath(clip.path).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")
    return handle.name


def build_fast_command(ffmpeg: str, list_path: str,
                       settings: MergeSettings) -> list[str]:
    """Lossless join via the concat demuxer."""
    ext = os.path.splitext(settings.out_path)[1].lower()
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-nostdin", "-y",
        "-progress", "pipe:1", "-nostats",
        "-f", "concat", "-safe", "0",
        "-i", list_path,
        "-map", "0:v:0", "-map", "0:a?",
        "-c", "copy",
        "-avoid_negative_ts", "make_zero",
    ]
    if ext in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(settings.out_path)
    return cmd


def build_precise_command(ffmpeg: str, settings: MergeSettings) -> list[str]:
    """Re-encoding join via the concat filter.

    Every clip is scaled and padded to a common frame, resampled to a
    common audio format, and silence is generated for clips that have no
    audio - otherwise the concat filter refuses mismatched inputs.
    """
    clips = settings.clips
    width, height, fps = target_geometry(clips)
    any_audio = any(clip.has_audio for clip in clips)

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
           "-progress", "pipe:1", "-nostats"]
    for clip in clips:
        cmd += ["-i", clip.path]

    parts: list[str] = []
    labels: list[str] = []
    for index, clip in enumerate(clips):
        parts.append(
            f"[{index}:v]scale={width}:{height}:"
            "force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps:g},format=yuv420p[v{index}]"
        )
        labels.append(f"[v{index}]")
        if not any_audio:
            continue
        if clip.has_audio:
            parts.append(
                f"[{index}:a]aresample={DEFAULT_SAMPLE_RATE},"
                f"aformat=sample_fmts=fltp:channel_layouts="
                f"{DEFAULT_CHANNEL_LAYOUT}[a{index}]"
            )
        else:
            parts.append(
                f"anullsrc=channel_layout={DEFAULT_CHANNEL_LAYOUT}:"
                f"sample_rate={DEFAULT_SAMPLE_RATE}:"
                f"d={clip.duration:.6f}[a{index}]"
            )
        labels.append(f"[a{index}]")

    parts.append(
        "".join(labels)
        + f"concat=n={len(clips)}:v=1:a={1 if any_audio else 0}"
        + ("[outv][outa]" if any_audio else "[outv]")
    )

    cmd += ["-filter_complex", ";".join(parts), "-map", "[outv]"]
    if any_audio:
        cmd += ["-map", "[outa]", "-c:a", "aac", "-b:a", settings.audio_bitrate]
    cmd += [
        "-c:v", "libx264",
        "-preset", settings.preset,
        "-crf", str(settings.crf),
        "-pix_fmt", "yuv420p",
    ]
    if os.path.splitext(settings.out_path)[1].lower() in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(settings.out_path)
    return cmd


# -- worker -----------------------------------------------------------
class MergeWorker(QThread):
    """Runs the merge off the GUI thread, reporting progress."""

    progress = Signal(float, str)
    finished_merge = Signal(object)      # MergeResult

    def __init__(self, settings: MergeSettings, parent=None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._cancel = False
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        self._cancel = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self) -> None:  # noqa: D102 - QThread entry point
        settings = self._settings
        result = MergeResult(out_path=settings.out_path)
        try:
            ffmpeg, _ = find_binaries()
        except RuntimeError as exc:
            result.error = str(exc)
            self.finished_merge.emit(result)
            return

        list_path = ""
        try:
            if settings.precise:
                cmd = build_precise_command(ffmpeg, settings)
                label = f"Re-encoding {len(settings.clips)} clips into one..."
            else:
                list_path = write_concat_list(
                    settings.clips, os.path.dirname(settings.out_path))
                cmd = build_fast_command(ffmpeg, list_path, settings)
                label = f"Joining {len(settings.clips)} clips (lossless)..."

            self.progress.emit(0.0, label)
            error = self._run_progress(cmd, settings.total_duration, label)
        except OSError as exc:
            error = f"Could not start ffmpeg: {exc}"
        finally:
            if list_path and os.path.exists(list_path):
                try:
                    os.remove(list_path)
                except OSError:
                    pass

        if self._cancel:
            result.cancelled = True
            if os.path.exists(settings.out_path):
                try:
                    os.remove(settings.out_path)
                except OSError:
                    pass
            self.finished_merge.emit(result)
            return

        if error:
            result.error = error
            if os.path.exists(settings.out_path):
                try:
                    os.remove(settings.out_path)
                except OSError:
                    pass
        elif not os.path.exists(settings.out_path) or \
                os.path.getsize(settings.out_path) == 0:
            result.error = "ffmpeg produced no output file."
        else:
            result.ok = True
            self.progress.emit(100.0, "Done")

        self.finished_merge.emit(result)

    def _run_progress(self, cmd: list[str], total_seconds: float,
                      label: str) -> str | None:
        from .exporter import _friendly_ffmpeg_error, _TERMINATED_CODES

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, bufsize=1)
        self._proc = proc
        stderr = ""
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if self._cancel:
                    proc.terminate()
                    break
                line = line.strip()
                if not (line.startswith("out_time_us=")
                        or line.startswith("out_time_ms=")):
                    continue
                raw = line.split("=", 1)[1]
                if raw in ("N/A", ""):
                    continue
                try:
                    seconds = int(raw) / 1_000_000.0
                except ValueError:
                    continue
                pct = max(0.0, seconds) / max(total_seconds, 1e-6) * 100.0
                self.progress.emit(min(99.5, pct), label)
            stderr = proc.stderr.read() if proc.stderr else ""
            code = proc.wait()
        finally:
            self._proc = None

        if self._cancel:
            return None
        if code != 0:
            if code in _TERMINATED_CODES and not stderr.strip():
                return "ffmpeg was interrupted."
            return _friendly_ffmpeg_error(stderr, code)
        return None
