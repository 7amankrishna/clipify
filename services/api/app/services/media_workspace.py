"""Media helpers for the local visual clip workspace."""
from array import array
from pathlib import Path
import subprocess

from app.core.config import settings
from app.core.media_tools import inspect_tool


class WaveformError(RuntimeError):
    pass


def waveform_peaks(video_path: Path, points: int = 180) -> list[float]:
    tool = inspect_tool("ffmpeg", settings.ffmpeg_path)
    if not tool.available or not tool.path:
        raise WaveformError("FFmpeg was not found. Install FFmpeg before opening the waveform.")
    result = subprocess.run(
        [tool.path, "-v", "error", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "pipe:1"],
        capture_output=True,
        check=False,
        timeout=180,
    )
    if result.returncode != 0 or not result.stdout:
        raise WaveformError("Could not decode an audio track for this video.")
    samples = array("h")
    samples.frombytes(result.stdout[: len(result.stdout) - (len(result.stdout) % 2)])
    if not samples:
        raise WaveformError("This video has no readable audio samples.")
    block = max(1, len(samples) // points)
    peaks = [max(abs(value) for value in samples[index:index + block]) / 32768 for index in range(0, len(samples), block)]
    if len(peaks) > points:
        peaks = peaks[:points]
    return [round(peak, 4) for peak in peaks]
