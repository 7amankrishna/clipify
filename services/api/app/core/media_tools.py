import shutil
import subprocess
import json
from dataclasses import dataclass
from pathlib import Path

from app.core.config import settings


@dataclass
class ToolStatus:
    available: bool
    path: str | None
    version: str | None


def inspect_tool(name: str, configured_path: str | None) -> ToolStatus:
    path = configured_path or shutil.which(name)
    if not path:
        return ToolStatus(available=False, path=None, version=None)
    try:
        result = subprocess.run([path, "-version"], capture_output=True, text=True, timeout=3, check=False)
        first_line = (result.stdout or result.stderr).splitlines()[0] if (result.stdout or result.stderr) else None
        return ToolStatus(available=result.returncode == 0, path=path, version=first_line)
    except (OSError, subprocess.SubprocessError):
        return ToolStatus(available=False, path=path, version=None)


def media_tools_status() -> dict[str, ToolStatus]:
    return {
        "ffmpeg": inspect_tool("ffmpeg", settings.ffmpeg_path),
        "ffprobe": inspect_tool("ffprobe", settings.ffprobe_path),
    }


class MediaProbeError(RuntimeError):
    """Raised when FFprobe cannot read an imported media file."""


def probe_video(path: Path) -> dict[str, float | int | None]:
    """Read video metadata from a local file using the detected FFprobe binary."""
    tool = inspect_tool("ffprobe", settings.ffprobe_path)
    if not tool.available or not tool.path:
        raise MediaProbeError("FFprobe was not found. Install FFmpeg or configure CLIPFORGE_FFPROBE_PATH.")
    try:
        result = subprocess.run(
            [tool.path, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MediaProbeError("FFprobe could not inspect the imported video.") from exc
    if result.returncode != 0:
        message = (result.stderr or "Unsupported or corrupt video file.").strip()
        raise MediaProbeError(message[:300])
    try:
        payload = json.loads(result.stdout)
        video = next(stream for stream in payload.get("streams", []) if stream.get("codec_type") == "video")
        duration = float(payload.get("format", {}).get("duration") or video.get("duration") or 0)
        rate = str(video.get("avg_frame_rate") or "0/1")
        numerator, denominator = rate.split("/", maxsplit=1)
        fps = float(numerator) / float(denominator) if float(denominator) else None
        return {"duration_seconds": duration, "width": video.get("width"), "height": video.get("height"), "fps": fps}
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaProbeError("The imported file does not contain a readable video stream.") from exc
