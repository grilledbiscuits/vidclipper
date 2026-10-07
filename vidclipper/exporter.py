"""Segment export: one ffmpeg process per segment, run off the GUI thread."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from PySide6.QtCore import QThread, Signal

from .media import find_binaries, output_extension
from .model import Segment
from .naming import format_name, unique_path

# ffmpeg exit code when it is asked to stop.
_TERMINATED_CODES = (255, -15, -9, -2)


@dataclass
class ExportSettings:
    """Everything needed to turn segments into files on disk."""

    source_path: str
    out_dir: str
    pattern: str
    precise: bool = False
    source_ext: str = ".mp4"
    source_bytes: int = 0
    source_duration: float = 0.0
    has_audio: bool = True
    crf: int = 18
    preset: str = "veryfast"
    audio_bitrate: str = "192k"
    overwrite: bool = False

    @property
    def out_ext(self) -> str:
        return output_extension(self.source_ext, self.precise)


@dataclass
class ExportResult:
    written: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


class ExportError(RuntimeError):
    """Raised for problems detected before ffmpeg is launched."""


def plan_outputs(segments: list[Segment],
                 settings: ExportSettings) -> list[tuple[Segment, str]]:
    """Resolve every segment to a full output path.

    Raises ExportError for a bad pattern, a missing/unwritable folder or
    (when not overwriting) colliding filenames.
    """
    if not segments:
        raise ExportError(
            "There is nothing to export.\n\n"
            "Add at least one split point, or use Quick Split."
        )
    out_dir = settings.out_dir
    if not out_dir:
        raise ExportError("Choose an output folder first.")
    if not os.path.isdir(out_dir):
        raise ExportError(f"The output folder does not exist:\n{out_dir}")
    if not os.access(out_dir, os.W_OK):
        raise ExportError(f"The output folder is not writable:\n{out_dir}")

    base = os.path.splitext(os.path.basename(settings.source_path))[0]
    ext = settings.out_ext
    plan: list[tuple[Segment, str]] = []
    seen: set[str] = set()

    for seg in segments:
        filename = format_name(
            settings.pattern, name=base, n=seg.index, ext=ext,
            start=seg.start, duration=seg.duration,
        )
        path = os.path.join(out_dir, filename)
        if os.path.abspath(path) == os.path.abspath(settings.source_path):
            raise ExportError(
                "That filename pattern would overwrite the video you are "
                "splitting.\n\nChange the pattern (keep {name} and {n}) or "
                "pick a different output folder."
            )
        if path in seen:
            raise ExportError(
                "The filename pattern produces the same name for several "
                "segments.\nInclude {n} so each segment gets its own file."
            )
        if os.path.exists(path):
            if settings.overwrite:
                pass
            else:
                path = unique_path(out_dir, filename)
        seen.add(path)
        plan.append((seg, path))
    return plan


def estimate_output_bytes(segments: list[Segment],
                          settings: ExportSettings) -> int:
    """Rough total output size, used for the free-space check."""
    if settings.source_duration <= 0 or settings.source_bytes <= 0:
        return 0
    rate = settings.source_bytes / settings.source_duration
    total = sum(seg.duration for seg in segments) * rate
    # Each output repeats the container headers; add a little slack.
    return int(total * 1.05) + 1_000_000 * len(segments)


def check_free_space(segments: list[Segment], settings: ExportSettings) -> None:
    """Raise ExportError when the destination clearly cannot hold the output."""
    needed = estimate_output_bytes(segments, settings)
    if needed <= 0:
        return
    try:
        free = shutil.disk_usage(settings.out_dir).free
    except OSError:
        return
    if free < needed:
        from .timecode import human_size

        raise ExportError(
            "Not enough free disk space in the output folder.\n\n"
            f"Estimated output: {human_size(needed)}\n"
            f"Free space:       {human_size(free)}"
        )


def build_command(ffmpeg: str, seg: Segment, out_path: str,
                  settings: ExportSettings) -> list[str]:
    """ffmpeg command for one segment (precise mode, or a lone copy).

    Multi-segment fast exports go through build_segment_command instead,
    which cuts the whole file in one pass.
    """
    ext = os.path.splitext(out_path)[1].lower()
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-nostdin", "-y",
        "-progress", "pipe:1", "-nostats",
        # Input seeking: fast in copy mode, still frame-accurate when
        # re-encoding because ffmpeg decodes and discards up to -ss.
        "-ss", f"{seg.start:.6f}",
        "-i", settings.source_path,
        "-t", f"{seg.duration:.6f}",
        "-map", "0:v:0", "-map", "0:a?",
    ]
    if ext == ".mkv":
        cmd += ["-map", "0:s?"]

    if settings.precise:
        cmd += [
            "-c:v", "libx264",
            "-preset", settings.preset,
            "-crf", str(settings.crf),
            "-pix_fmt", "yuv420p",
        ]
        cmd += ["-c:a", "aac", "-b:a", settings.audio_bitrate] if settings.has_audio else []
    else:
        cmd += ["-c", "copy", "-avoid_negative_ts", "make_zero"]

    if ext in (".mp4", ".m4v", ".mov"):
        cmd += ["-movflags", "+faststart"]
    cmd.append(out_path)
    return cmd


# Nudge each cut slightly earlier so ffmpeg lands on the keyframe we
# picked rather than the following one when the times are equal.
_CUT_EPSILON = 0.04


def segment_cut_times(plan) -> list[float]:
    """Cut points (seconds) to hand to ffmpeg's segment muxer."""
    times: list[float] = []
    for index in range(1, len(plan)):
        start = plan[index][0].start
        previous = plan[index - 1][0].start
        epsilon = min(_CUT_EPSILON, max(0.0, (start - previous) / 4.0))
        times.append(max(0.0, start - epsilon))
    return times


def tiles_whole_file(plan, settings: ExportSettings) -> bool:
    """True when the segments cover the source end to end, back to back.

    That is always the case for VidClipper's own split points, and it is
    what makes the one-pass segment muxer safe to use.
    """
    if len(plan) < 2:
        return False
    if plan[0][0].start > 0.05:
        return False
    for (previous, _), (current, _) in zip(plan, plan[1:]):
        if abs(current.start - previous.end) > 0.05:
            return False
    if settings.source_duration > 0:
        if abs(plan[-1][0].end - settings.source_duration) > 0.75:
            return False
    return True


def build_segment_command(ffmpeg: str, plan, template: str,
                          settings: ExportSettings) -> list[str]:
    """One-pass lossless split using ffmpeg's segment muxer.

    Cutting every segment in a single pass is both faster than seeking
    per segment and exact: the muxer starts each new file on a keyframe
    with no overlap, which per-segment `-ss` cannot guarantee in
    Matroska/WebM (its seeks land on cluster boundaries).
    """
    ext = os.path.splitext(template)[1].lower()
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-nostdin", "-y",
        "-progress", "pipe:1", "-nostats",
        "-i", settings.source_path,
        "-map", "0:v:0", "-map", "0:a?",
    ]
    if ext == ".mkv":
        cmd += ["-map", "0:s?"]
    cmd += [
        "-c", "copy",
        "-f", "segment",
        "-segment_times", ",".join(f"{t:.6f}" for t in segment_cut_times(plan)),
        "-segment_start_number", "0",
        "-reset_timestamps", "1",
        "-avoid_negative_ts", "make_zero",
        template,
    ]
    return cmd


class ExportWorker(QThread):
    """Runs the export plan segment by segment, reporting progress."""

    progress = Signal(float, str)          # 0-100, status line
    segment_started = Signal(int, int)     # index (1-based), total
    segment_finished = Signal(int, str)    # index, output path
    finished_all = Signal(object)          # ExportResult

    def __init__(self, plan, settings: ExportSettings, parent=None) -> None:
        super().__init__(parent)
        self._plan = list(plan)
        self._settings = settings
        self._cancel = False
        self._proc: subprocess.Popen | None = None

    def cancel(self) -> None:
        """Ask the worker to stop after (or during) the current segment."""
        self._cancel = True
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def run(self) -> None:  # noqa: D102 - QThread entry point
        result = ExportResult()
        try:
            ffmpeg, _ = find_binaries()
        except RuntimeError as exc:
            result.errors.append(str(exc))
            self.finished_all.emit(result)
            return

        if not self._settings.precise and tiles_whole_file(self._plan,
                                                           self._settings):
            self._run_segment_pass(ffmpeg, result)
            self.finished_all.emit(result)
            return

        self._run_per_segment(ffmpeg, result)
        self.finished_all.emit(result)

    def _run_per_segment(self, ffmpeg: str, result: ExportResult) -> None:
        """One ffmpeg process per segment (precise mode, or a single clip)."""
        total_seconds = sum(seg.duration for seg, _ in self._plan) or 1.0
        done_seconds = 0.0
        total = len(self._plan)

        for position, (seg, out_path) in enumerate(self._plan, start=1):
            if self._cancel:
                result.cancelled = True
                break
            self.segment_started.emit(position, total)
            self.progress.emit(
                done_seconds / total_seconds * 100.0,
                f"Segment {position} of {total}: {os.path.basename(out_path)}",
            )
            cmd = build_command(ffmpeg, seg, out_path, self._settings)
            try:
                error = self._run_one(cmd, seg, done_seconds, total_seconds,
                                      position, total, out_path)
            except OSError as exc:
                error = f"Could not start ffmpeg: {exc}"

            if self._cancel:
                result.cancelled = True
                # A half-written file is useless; remove it.
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
                break
            if error:
                result.errors.append(
                    f"Segment {seg.index} ({os.path.basename(out_path)}): {error}"
                )
            elif not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
                result.errors.append(
                    f"Segment {seg.index}: ffmpeg produced no output file."
                )
                if os.path.exists(out_path):
                    try:
                        os.remove(out_path)
                    except OSError:
                        pass
            else:
                result.written.append(out_path)
                self.segment_finished.emit(seg.index, out_path)

            done_seconds += seg.duration
            self.progress.emit(
                min(100.0, done_seconds / total_seconds * 100.0),
                f"Finished {position} of {total}",
            )

        if not result.cancelled:
            self.progress.emit(100.0, "Done")

    # -- one-pass lossless split --------------------------------------
    def _run_segment_pass(self, ffmpeg: str, result: ExportResult) -> None:
        out_dir = self._settings.out_dir
        ext = os.path.splitext(self._plan[0][1])[1] or self._settings.out_ext
        token = f".vidclipper-{os.getpid()}-"
        template = os.path.join(out_dir, token + "%04d" + ext)
        total = len(self._plan)

        def temp_files() -> list[str]:
            return sorted(
                os.path.join(out_dir, name) for name in os.listdir(out_dir)
                if name.startswith(token)
            )

        def cleanup() -> None:
            for path in temp_files():
                try:
                    os.remove(path)
                except OSError:
                    pass

        cleanup()
        self.segment_started.emit(1, total)
        self.progress.emit(0.0, f"Splitting into {total} segments...")

        cmd = build_segment_command(ffmpeg, self._plan, template,
                                    self._settings)
        duration = self._settings.source_duration or sum(
            seg.duration for seg, _ in self._plan) or 1.0
        try:
            error = self._run_progress(cmd, duration,
                                       f"Splitting into {total} segments...")
        except OSError as exc:
            error = f"Could not start ffmpeg: {exc}"

        if self._cancel:
            result.cancelled = True
            cleanup()
            return
        if error:
            cleanup()
            result.errors.append(f"Lossless split failed: {error}")
            return

        produced = temp_files()
        if len(produced) != total:
            cleanup()
            result.errors.append(
                f"ffmpeg produced {len(produced)} file(s) but "
                f"{total} were expected. The cut points may be too close "
                "together for this file's keyframes - try Precise mode."
            )
            return

        for (seg, out_path), temp_path in zip(self._plan, produced):
            try:
                os.replace(temp_path, out_path)
            except OSError as exc:
                result.errors.append(
                    f"Segment {seg.index}: could not write "
                    f"{os.path.basename(out_path)} ({exc})")
                continue
            result.written.append(out_path)
            self.segment_finished.emit(seg.index, out_path)

        self.progress.emit(100.0, "Done")

    # -- internals ----------------------------------------------------
    def _run_one(self, cmd: list[str], seg: Segment, done_seconds: float,
                 total_seconds: float, position: int, total: int,
                 out_path: str) -> str | None:
        """Run ffmpeg for one segment; return an error string or None."""
        name = os.path.basename(out_path)
        return self._run_progress(
            cmd, total_seconds, f"Segment {position} of {total}: {name}",
            offset=done_seconds, span=seg.duration)

    def _run_progress(self, cmd: list[str], total_seconds: float, label: str,
                      offset: float = 0.0, span: float | None = None
                      ) -> str | None:
        """Run ffmpeg, translating its -progress output into percentages.

        `offset`/`span` place this process's progress inside a longer run
        (used when exporting segment by segment).
        """
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
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
                    # Both keys are microseconds in practice; ffmpeg's
                    # out_time_ms is a long-standing misnomer.
                    seconds = int(raw) / 1_000_000.0
                except ValueError:
                    continue
                seconds = max(0.0, seconds)
                if span is not None:
                    seconds = min(seconds, span)
                pct = (offset + seconds) / max(total_seconds, 1e-6) * 100.0
                self.progress.emit(min(100.0, pct), label)
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


def _friendly_ffmpeg_error(stderr: str, code: int) -> str:
    """Turn ffmpeg's stderr into something worth showing a human."""
    text = (stderr or "").strip()
    lowered = text.lower()
    if "no space left" in lowered:
        return "the disk ran out of space."
    if "permission denied" in lowered:
        return "permission denied writing the output file."
    if "invalid data found" in lowered:
        return "the source file is corrupt at this point."
    if "could not write header" in lowered or "muxer" in lowered:
        return ("the streams cannot be copied into this container - "
                "try Precise mode.")
    if not text:
        return f"ffmpeg exited with code {code}."
    last = [ln for ln in text.splitlines() if ln.strip()]
    return last[-1] if last else f"ffmpeg exited with code {code}."
