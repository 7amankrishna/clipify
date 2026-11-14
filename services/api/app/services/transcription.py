"""Local background transcription runner using FFmpeg and faster-whisper."""
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import subprocess
import threading
import time

from app.core.config import settings
from app.core.media_tools import inspect_tool
from app.db.session import SessionLocal
from app.models import MediaAsset, ProcessingJob, Transcript

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-transcription")
_model_cache: dict[tuple[str, str], object] = {}
_model_lock = threading.Lock()
_futures: dict[int, Future] = {}


class TranscriptionError(RuntimeError):
    pass


def serialize_job(job: ProcessingJob) -> dict:
    return {
        "id": job.id,
        "project_id": job.project_id,
        "job_type": job.job_type,
        "status": job.status,
        "progress": job.progress,
        "error_message": job.error_message,
        "metadata_json": job.metadata_json,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def serialize_transcript(transcript: Transcript) -> dict:
    return {
        "id": transcript.id,
        "project_id": transcript.project_id,
        "asset_id": transcript.asset_id,
        "job_id": transcript.job_id,
        "language": transcript.language,
        "language_probability": transcript.language_probability,
        "segments": transcript.segments_json,
        "created_at": transcript.created_at,
    }


def submit(job_id: int) -> None:
    """Queue a durable job. Worker state lives in SQLite, not memory."""
    future = _executor.submit(run_transcription, job_id)
    _futures[job_id] = future


def request_cancel(job_id: int) -> None:
    database = SessionLocal()
    try:
        job = database.get(ProcessingJob, job_id)
        if job and job.status in {"queued", "processing", "cancel_requested"}:
            metadata = dict(job.metadata_json or {})
            metadata["cancel_requested"] = True
            job.metadata_json = metadata
            job.status = "cancelled" if job.status == "queued" else "cancel_requested"
            database.commit()
    finally:
        database.close()


def _is_cancelled(job: ProcessingJob) -> bool:
    return job.status == "cancel_requested" or bool((job.metadata_json or {}).get("cancel_requested"))


def _set_state(database, job: ProcessingJob, state: str, progress: int | None = None, error: str | None = None) -> None:
    job.status = state
    if progress is not None:
        job.progress = max(0, min(progress, 100))
    job.error_message = error
    database.commit()


def _load_model(name: str, device_preference: str):
    try:
        import ctranslate2
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionError("faster-whisper is not installed. Run: pip install -r services/api/requirements-ai.txt") from exc
    device = device_preference
    if device == "auto":
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    if device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
        raise TranscriptionError("CUDA was selected but no compatible NVIDIA CUDA runtime was found.")
    key = (name, device)
    with _model_lock:
        if key not in _model_cache:
            compute_type = "float16" if device == "cuda" else "int8"
            _model_cache[key] = WhisperModel(name, device=device, compute_type=compute_type)
    return _model_cache[key], device


def _extract_audio(video_path: Path, audio_path: Path, job_id: int) -> None:
    tool = inspect_tool("ffmpeg", settings.ffmpeg_path)
    if not tool.available or not tool.path:
        raise TranscriptionError("FFmpeg was not found. Install FFmpeg or configure CLIPFORGE_FFMPEG_PATH.")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [tool.path, "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    while process.poll() is None:
        database = SessionLocal()
        try:
            job = database.get(ProcessingJob, job_id)
            if job and _is_cancelled(job):
                process.terminate()
                process.wait(timeout=5)
                raise TranscriptionError("Cancellation requested.")
        finally:
            database.close()
        time.sleep(0.25)
    if process.returncode:
        details = (process.stderr.read() if process.stderr else "").strip()
        raise TranscriptionError(f"Audio extraction failed: {details[-300:] or 'FFmpeg returned an error.'}")


def run_transcription(job_id: int) -> None:
    database = SessionLocal()
    audio_path: Path | None = None
    try:
        job = database.get(ProcessingJob, job_id)
        if not job or _is_cancelled(job):
            if job:
                _set_state(database, job, "cancelled", 0)
            return
        metadata = dict(job.metadata_json or {})
        asset = database.get(MediaAsset, metadata.get("asset_id"))
        if not asset:
            raise TranscriptionError("Source video for this job no longer exists.")
        _set_state(database, job, "processing", 2)
        audio_path = settings.storage_dir / "projects" / str(job.project_id) / "audio" / f"job-{job.id}.wav"
        _extract_audio(Path(asset.original_path), audio_path, job.id)
        database.refresh(job)
        if _is_cancelled(job):
            _set_state(database, job, "cancelled", job.progress)
            return
        _set_state(database, job, "processing", 22)
        model, actual_device = _load_model(metadata.get("model", "base"), metadata.get("device", "auto"))
        metadata["actual_device"] = actual_device
        metadata["vad"] = "Silero VAD (faster-whisper)"
        job.metadata_json = metadata
        database.commit()
        requested_language = metadata.get("language", "auto")
        language = None if requested_language in {"auto", "hinglish"} else requested_language
        segments_stream, info = model.transcribe(
            str(audio_path),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segments: list[dict] = []
        duration = asset.duration_seconds or 0
        for segment in segments_stream:
            database.refresh(job)
            if _is_cancelled(job):
                _set_state(database, job, "cancelled", job.progress)
                return
            words = [{"word": word.word.strip(), "start": word.start, "end": word.end, "probability": word.probability} for word in (segment.words or [])]
            segments.append({"start": segment.start, "end": segment.end, "text": segment.text.strip(), "words": words})
            progress = 35 + int(60 * min(segment.end / duration, 1)) if duration else min(94, 35 + len(segments))
            _set_state(database, job, "processing", progress)
        transcript_dir = settings.storage_dir / "projects" / str(job.project_id) / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = transcript_dir / f"job-{job.id}.json"
        content = {"language": info.language, "language_probability": info.language_probability, "segments": segments, "generated_at": datetime.utcnow().isoformat()}
        transcript_path.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        transcript = Transcript(project_id=job.project_id, asset_id=asset.id, job_id=job.id, language=info.language, language_probability=info.language_probability, segments_json=segments, transcript_path=str(transcript_path.resolve()))
        database.add(transcript)
        metadata["transcript_id"] = None
        job.metadata_json = metadata
        database.commit()
        database.refresh(transcript)
        metadata["transcript_id"] = transcript.id
        job.metadata_json = metadata
        _set_state(database, job, "completed", 100)
    except TranscriptionError as exc:
        if database.get(ProcessingJob, job_id):
            job = database.get(ProcessingJob, job_id)
            _set_state(database, job, "cancelled" if _is_cancelled(job) else "failed", job.progress, str(exc))
    except Exception as exc:  # preserve error in durable job state; API remains responsive
        job = database.get(ProcessingJob, job_id)
        if job:
            _set_state(database, job, "failed", job.progress, f"Transcription failed: {str(exc)[:400]}")
    finally:
        if audio_path:
            audio_path.unlink(missing_ok=True)
        database.close()
