"""Local FFmpeg preview and final-render pipeline with ASS karaoke captions."""
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import os
import platform
import subprocess
import time

from sqlalchemy import select

from app.core.config import settings
from app.core.media_tools import inspect_tool
from app.db.session import SessionLocal
from app.models import CaptionTrack, ClipEdit, MediaAsset, ProcessingJob, VideoExport
from app.services.transcription import _is_cancelled, _set_state

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-render")
_futures: dict[int, Future] = {}

PRESETS = {
    "bold_yellow": {"font": "Nirmala UI" if platform.system() == "Windows" else "Noto Sans Devanagari", "primary": "&H0000FFFF", "secondary": "&H00303030", "outline": "&H00101010", "size": 64, "bold": -1},
    "white_minimal": {"font": "Nirmala UI" if platform.system() == "Windows" else "Noto Sans Devanagari", "primary": "&H00FFFFFF", "secondary": "&H00606060", "outline": "&H00101010", "size": 52, "bold": 0},
    "neon_creator": {"font": "Nirmala UI" if platform.system() == "Windows" else "Noto Sans Devanagari", "primary": "&H00FFFF00", "secondary": "&H00606060", "outline": "&H00800080", "size": 62, "bold": -1},
}


class RenderError(RuntimeError):
    pass


def submit(job_id: int) -> None:
    _futures[job_id] = _executor.submit(run_render, job_id)


def serialize_caption(track: CaptionTrack) -> dict:
    return {"id": track.id, "clip_edit_id": track.clip_edit_id, "preset": track.preset, "words": track.words_json, "updated_at": track.updated_at}


def serialize_export(export: VideoExport) -> dict:
    return {"id": export.id, "project_id": export.project_id, "clip_edit_id": export.clip_edit_id, "job_id": export.job_id, "is_preview": export.is_preview, "status": export.status, "output_path": export.output_path, "error_message": export.error_message, "created_at": export.created_at, "updated_at": export.updated_at}


def _ass_time(value: float) -> str:
    total = max(0, int(value * 100))
    hours, total = divmod(total, 360000)
    minutes, total = divmod(total, 6000)
    seconds, centiseconds = divmod(total, 100)
    return f"{hours}:{minutes:02}:{seconds:02}.{centiseconds:02}"


def _ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", "\\N")


def write_ass(track: CaptionTrack, edit: ClipEdit, path: Path, width: int, height: int) -> None:
    preset = PRESETS.get(track.preset, PRESETS["bold_yellow"])
    header = "[Script Info]\nScriptType: v4.00+\nPlayResX: {0}\nPlayResY: {1}\nWrapStyle: 2\n\n[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\nStyle: Default,{font},{size},{primary},{secondary},{outline},&H80000000,{bold},0,0,0,100,100,0,0,1,3,1,2,80,80,150,1\n\n[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n".format(width, height, **preset)
    grouped: dict[int, list[dict]] = {}
    for word in track.words_json:
        if word["end"] <= edit.start_seconds or word["start"] >= edit.end_seconds:
            continue
        grouped.setdefault(int(word.get("line", 0)), []).append(word)
    events: list[str] = []
    for words in grouped.values():
        words.sort(key=lambda item: item["start"])
        start = max(edit.start_seconds, words[0]["start"])
        end = min(edit.end_seconds, words[-1]["end"])
        karaoke = "".join("{{\\k{}}}{}".format(max(1, int((min(edit.end_seconds, word["end"]) - max(edit.start_seconds, word["start"])) * 100)), _ass_text(word["word"].strip())) for word in words)
        events.append(f"Dialogue: 0,{_ass_time(start - edit.start_seconds)},{_ass_time(end - edit.start_seconds)},Default,,0,0,0,,{karaoke}")
    path.write_text(header + "\n".join(events), encoding="utf-8")


def _dimensions(ratio: str, preview: bool) -> tuple[int, int]:
    base = 540 if preview else 1080
    if ratio == "1:1": return base, base
    if ratio == "16:9": return base * 16 // 9, base
    return base, base * 16 // 9


def _video_filter(edit: ClipEdit, ass_path: Path, width: int, height: int) -> str:
    subtitle_path = ass_path.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    subtitles = f"subtitles=filename='{subtitle_path}'"
    if edit.crop_mode == "blurred":
        return f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},boxblur=20:10[bg];[0:v]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,{subtitles}[v]"
    if edit.crop_mode == "split":
        half = width // 2
        return f"[0:v]split[a][b];[a]scale={half}:{height}:force_original_aspect_ratio=increase,crop={half}:{height}:0:0[left];[b]scale={half}:{height}:force_original_aspect_ratio=increase,crop={half}:{height}:iw-{half}:0[right];[left][right]hstack=inputs=2,{subtitles}[v]"
    x = edit.manual_x / 100 if edit.crop_mode == "manual" else 0.5
    y = edit.manual_y / 100 if edit.crop_mode == "manual" else 0.5
    return f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}:(iw-{width})*{x}:(ih-{height})*{y},{subtitles}[v]"


def run_render(job_id: int) -> None:
    database = SessionLocal()
    process: subprocess.Popen | None = None
    try:
        job = database.get(ProcessingJob, job_id)
        if not job or _is_cancelled(job):
            if job: _set_state(database, job, "cancelled", 0)
            return
        metadata = dict(job.metadata_json or {})
        export = database.get(VideoExport, metadata.get("export_id"))
        edit = database.get(ClipEdit, metadata.get("clip_edit_id"))
        if not export or not edit: raise RenderError("Export configuration no longer exists.")
        asset = database.get(MediaAsset, edit.asset_id)
        track = database.scalar(select(CaptionTrack).where(CaptionTrack.clip_edit_id == edit.id))
        if not asset or not track: raise RenderError("Caption track or source video is missing.")
        tool = inspect_tool("ffmpeg", settings.ffmpeg_path)
        if not tool.available or not tool.path: raise RenderError("FFmpeg was not found. Install FFmpeg before exporting.")
        _set_state(database, job, "processing", 3); export.status = "processing"; database.commit()
        width, height = _dimensions(edit.aspect_ratio, export.is_preview)
        export_dir = settings.storage_dir / "projects" / str(edit.project_id) / "exports"; export_dir.mkdir(parents=True, exist_ok=True)
        ass_path = export_dir / f"caption-{export.id}.ass"; write_ass(track, edit, ass_path, width, height)
        output_path = export_dir / (f"preview-{export.id}.mp4" if export.is_preview else f"clip-{export.id}.mp4")
        duration = max(.1, edit.end_seconds - edit.start_seconds)
        process = subprocess.Popen([tool.path, "-y", "-ss", str(edit.start_seconds), "-t", str(duration), "-i", asset.original_path, "-filter_complex", _video_filter(edit, ass_path, width, height), "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", str(output_path)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        while process.poll() is None:
            line = process.stdout.readline() if process.stdout else ""
            if line.startswith("out_time_ms="):
                seconds = int(line.split("=", 1)[1].strip()) / 1_000_000
                _set_state(database, job, "processing", min(98, 5 + int(seconds / duration * 93)))
            database.refresh(job)
            if _is_cancelled(job):
                process.terminate(); process.wait(timeout=10); raise RenderError("Cancellation requested.")
            time.sleep(.02)
        if process.returncode != 0 or not output_path.exists(): raise RenderError("FFmpeg could not render this clip. Check the selected crop and subtitles.")
        export.status = "completed"; export.output_path = str(output_path.resolve()); export.error_message = None; job.metadata_json = {**metadata, "output_path": export.output_path}; database.commit(); _set_state(database, job, "completed", 100)
    except RenderError as exc:
        job = database.get(ProcessingJob, job_id)
        if job:
            export = database.get(VideoExport, (job.metadata_json or {}).get("export_id"))
            if export: export.status = "cancelled" if _is_cancelled(job) else "failed"; export.error_message = str(exc); database.commit()
            _set_state(database, job, "cancelled" if _is_cancelled(job) else "failed", job.progress, str(exc))
    except Exception as exc:
        job = database.get(ProcessingJob, job_id)
        if job: _set_state(database, job, "failed", job.progress, f"Render failed: {str(exc)[:400]}")
    finally:
        database.close()
