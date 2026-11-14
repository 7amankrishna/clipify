"""Transparent local heuristic clip detection without an LLM."""
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
import re
import subprocess

from sqlalchemy import select

from app.core.config import settings
from app.core.media_tools import inspect_tool
from app.db.session import SessionLocal
from app.models import ClipSuggestion, MediaAsset, ProcessingJob, Transcript
from app.services.transcription import _is_cancelled, _set_state

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="clipforge-clips")
_futures: dict[int, Future] = {}
HOOK_WORDS = {"how", "why", "secret", "mistake", "truth", "nobody", "stop", "never", "best", "worst", "imagine", "actually", "listen", "watch", "important", "finally"}
QUESTION_WORDS = {"how", "why", "what", "when", "where", "who", "which", "can", "should", "is", "are", "will"}
PUNCH_WORDS = {"but", "however", "instead", "surprise", "crazy", "unbelievable", "exactly", "remember", "simple", "powerful"}


class ClipAnalysisError(RuntimeError):
    pass


def submit(job_id: int) -> None:
    _futures[job_id] = _executor.submit(run_analysis, job_id)


def serialize_suggestion(suggestion: ClipSuggestion) -> dict:
    return {
        "id": suggestion.id,
        "project_id": suggestion.project_id,
        "asset_id": suggestion.asset_id,
        "analysis_job_id": suggestion.analysis_job_id,
        "start_seconds": suggestion.start_seconds,
        "end_seconds": suggestion.end_seconds,
        "score": suggestion.score,
        "transcript_text": suggestion.transcript_text,
        "title": suggestion.title,
        "reasons": suggestion.reasons_json,
        "metrics": suggestion.metrics_json,
        "created_at": suggestion.created_at,
    }


def _words(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z']+", text.lower())


def _overlap_duration(first: dict, second: dict) -> float:
    return max(0.0, min(first["end"], second["end"]) - max(first["start"], second["start"]))


def _is_duplicate(candidate: dict, selected: list[dict]) -> bool:
    candidate_words = set(_words(candidate["text"]))
    for other in selected:
        overlap = _overlap_duration(candidate, other)
        if overlap / min(candidate["duration"], other["duration"]) > 0.55:
            return True
        other_words = set(_words(other["text"]))
        union = candidate_words | other_words
        if union and len(candidate_words & other_words) / len(union) > 0.78:
            return True
    return False


def transcript_candidates(segments: list[dict], minimum: int, maximum: int) -> list[dict]:
    candidates: list[dict] = []
    for start_index, start_segment in enumerate(segments):
        parts: list[str] = []
        for end_index in range(start_index, len(segments)):
            segment = segments[end_index]
            end = float(segment["end"])
            start = float(start_segment["start"])
            duration = end - start
            if duration > maximum:
                break
            parts.append(str(segment.get("text", "")).strip())
            if duration >= minimum:
                text = " ".join(part for part in parts if part)
                if len(_words(text)) >= 12:
                    candidates.append({"start": start, "end": end, "duration": duration, "text": text})
                if duration >= minimum + 15 or end_index > start_index + 8:
                    break
    return candidates


def silence_intervals(video_path: Path) -> list[tuple[float, float]]:
    tool = inspect_tool("ffmpeg", settings.ffmpeg_path)
    if not tool.available or not tool.path:
        raise ClipAnalysisError("FFmpeg was not found. Install FFmpeg before clip analysis.")
    result = subprocess.run([tool.path, "-i", str(video_path), "-af", "silencedetect=noise=-35dB:d=0.4", "-f", "null", "-"], capture_output=True, text=True, check=False, timeout=180)
    starts = [float(value) for value in re.findall(r"silence_start: ([0-9.]+)", result.stderr)]
    ends = [float(value) for value in re.findall(r"silence_end: ([0-9.]+)", result.stderr)]
    return list(zip(starts, ends))


def window_energy(video_path: Path, start: float, duration: float) -> float | None:
    tool = inspect_tool("ffmpeg", settings.ffmpeg_path)
    if not tool.available or not tool.path:
        return None
    result = subprocess.run([tool.path, "-ss", str(start), "-t", str(duration), "-i", str(video_path), "-vn", "-af", "volumedetect", "-f", "null", "-"], capture_output=True, text=True, check=False, timeout=60)
    found = re.search(r"mean_volume: (-?[0-9.]+) dB", result.stderr)
    return float(found.group(1)) if found else None


def audio_profile(video_path: Path, candidates: list[dict]) -> tuple[list[tuple[float, float]], dict[int, float | None]]:
    silences = silence_intervals(video_path)
    return silences, {index: window_energy(video_path, candidate["start"], candidate["duration"]) for index, candidate in enumerate(candidates[:30])}


def score_candidate(candidate: dict, all_speaking_rate: float, silences: list[tuple[float, float]], mean_volume: float | None) -> dict:
    words = _words(candidate["text"])
    word_set = set(words)
    first_words = set(words[:12])
    speech_rate = len(words) / max(candidate["duration"] / 60, 0.1)
    silence_seconds = sum(max(0, min(candidate["end"], silence_end) - max(candidate["start"], silence_start)) for silence_start, silence_end in silences)
    silence_ratio = silence_seconds / candidate["duration"]
    reasons: list[str] = []
    score = 0
    if first_words & HOOK_WORDS:
        score += 22; reasons.append("Hook language appears early")
    if "?" in candidate["text"] or first_words & QUESTION_WORDS:
        score += 12; reasons.append("Question-driven opening")
    if word_set & PUNCH_WORDS or "!" in candidate["text"]:
        score += 10; reasons.append("Contrast or punchline language")
    if 115 <= speech_rate <= 190:
        score += 16; reasons.append("Clear, short-form speaking pace")
    elif abs(speech_rate - all_speaking_rate) >= 35:
        score += 9; reasons.append("Noticeable speaking-rate change")
    if silence_ratio <= 0.08:
        score += 16; reasons.append("Low silence ratio")
    elif silence_ratio <= 0.18:
        score += 8; reasons.append("Manageable pauses")
    energy_score = 0
    if mean_volume is not None:
        energy_score = max(0, min(15, int((mean_volume + 45) / 2.4)))
        if energy_score >= 9:
            reasons.append("Strong audio energy")
    score += energy_score
    if len(words) >= 45:
        score += 8; reasons.append("Enough spoken context for a complete clip")
    if not reasons:
        reasons.append("Coherent timestamped speech segment")
    candidate["score"] = min(100, score)
    candidate["reasons"] = reasons
    candidate["metrics"] = {"duration_seconds": round(candidate["duration"], 2), "word_count": len(words), "speaking_rate_wpm": round(speech_rate, 1), "silence_ratio": round(silence_ratio, 3), "mean_volume_db": mean_volume, "heuristic": "local-v1"}
    candidate["title"] = candidate["text"].replace("\n", " ")[:100].strip() or "Suggested moment"
    return candidate


def run_analysis(job_id: int) -> None:
    database = SessionLocal()
    try:
        job = database.get(ProcessingJob, job_id)
        if not job or _is_cancelled(job):
            if job:
                _set_state(database, job, "cancelled", 0)
            return
        metadata = dict(job.metadata_json or {})
        asset = database.get(MediaAsset, metadata.get("asset_id"))
        transcript = database.scalar(select(Transcript).where(Transcript.asset_id == metadata.get("asset_id")).order_by(Transcript.created_at.desc()))
        if not asset or not transcript:
            raise ClipAnalysisError("Generate a transcript for this source video before analyzing clips.")
        _set_state(database, job, "processing", 5)
        candidates = transcript_candidates(transcript.segments_json, int(metadata["min_duration_seconds"]), int(metadata["max_duration_seconds"]))
        if not candidates:
            raise ClipAnalysisError("No transcript windows match the selected clip duration range.")
        candidates = candidates[:30]
        _set_state(database, job, "processing", 22)
        silences, energy_map = audio_profile(Path(asset.original_path), candidates)
        all_words = sum(len(_words(segment.get("text", ""))) for segment in transcript.segments_json)
        total_duration = max(asset.duration_seconds or 1, 1)
        average_rate = all_words / (total_duration / 60)
        scored: list[dict] = []
        for index, candidate in enumerate(candidates):
            database.refresh(job)
            if _is_cancelled(job):
                _set_state(database, job, "cancelled", job.progress)
                return
            scored.append(score_candidate(candidate, average_rate, silences, energy_map.get(index)))
            _set_state(database, job, "processing", 45 + int(42 * (index + 1) / len(candidates)))
        selected: list[dict] = []
        for candidate in sorted(scored, key=lambda item: item["score"], reverse=True):
            if not _is_duplicate(candidate, selected):
                selected.append(candidate)
            if len(selected) == 10:
                break
        database.query(ClipSuggestion).filter(ClipSuggestion.asset_id == asset.id).delete()
        for candidate in selected:
            database.add(ClipSuggestion(project_id=job.project_id, asset_id=asset.id, analysis_job_id=job.id, start_seconds=candidate["start"], end_seconds=candidate["end"], score=candidate["score"], transcript_text=candidate["text"], title=candidate["title"], reasons_json=candidate["reasons"], metrics_json=candidate["metrics"]))
        metadata.update({"candidate_count": len(candidates), "suggestion_count": len(selected), "engine": "transparent-local-heuristics-v1"})
        job.metadata_json = metadata
        _set_state(database, job, "completed", 100)
    except ClipAnalysisError as exc:
        job = database.get(ProcessingJob, job_id)
        if job:
            _set_state(database, job, "cancelled" if _is_cancelled(job) else "failed", job.progress, str(exc))
    except Exception as exc:
        job = database.get(ProcessingJob, job_id)
        if job:
            _set_state(database, job, "failed", job.progress, f"Clip analysis failed: {str(exc)[:400]}")
    finally:
        database.close()
