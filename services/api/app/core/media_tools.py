import shutil
import subprocess
from dataclasses import dataclass

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
