"""Optional Ollama/Gemini metadata generation with a deterministic local fallback."""
import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import AppSetting, ClipMetadata, ClipSuggestion

DEFAULTS = {
    "ai_provider": "fallback", "ollama_url": settings.ollama_url, "ollama_model": settings.ollama_model,
    "gemini_model": settings.gemini_model, "whisper_model": "base", "processing_device": "auto", "theme": "dark",
}
_SAFE_MODEL = re.compile(r"^[A-Za-z0-9._:/-]{1,120}$")


class AIProviderError(RuntimeError):
    pass


def read_setting(database: Session, key: str) -> str:
    entry = database.get(AppSetting, key)
    if entry:
        return entry.value
    if key == "gemini_api_key":
        return settings.gemini_api_key or ""
    return DEFAULTS[key]


def write_setting(database: Session, key: str, value: str) -> None:
    entry = database.get(AppSetting, key)
    if entry:
        entry.value = value
    else:
        database.add(AppSetting(key=key, value=value))


def settings_payload(database: Session) -> dict:
    return {
        "ai_provider": read_setting(database, "ai_provider"), "ollama_url": read_setting(database, "ollama_url"),
        "ollama_model": read_setting(database, "ollama_model"), "gemini_model": read_setting(database, "gemini_model"),
        "has_gemini_api_key": bool(read_setting(database, "gemini_api_key")), "whisper_model": read_setting(database, "whisper_model"),
        "processing_device": read_setting(database, "processing_device"), "theme": read_setting(database, "theme"),
        "storage_dir": str(settings.storage_dir.resolve()), "ffmpeg_path": settings.ffmpeg_path,
    }


def serialize_metadata(item: ClipMetadata) -> dict:
    return {"id": item.id, "project_id": item.project_id, "suggestion_id": item.suggestion_id, "provider": item.provider, "title": item.title, "hook": item.hook, "description": item.description, "hashtags": item.hashtags_json, "explanation": item.explanation, "generation_note": item.generation_note, "created_at": item.created_at, "updated_at": item.updated_at}


def _fallback(suggestion: ClipSuggestion) -> dict:
    text = " ".join(suggestion.transcript_text.split())
    first_sentence = re.split(r"(?<=[.!?])\s+", text)[0] or suggestion.title
    words = [word.lower() for word in re.findall(r"[A-Za-z]{4,}", text)]
    ignored = {"this", "that", "with", "from", "have", "your", "they", "what", "when", "will", "about", "into", "here"}
    tags = []
    for word in words:
        tag = f"#{word.title()}"
        if word not in ignored and tag not in tags:
            tags.append(tag)
        if len(tags) == 4:
            break
    return {"title": suggestion.title[:160], "hook": first_sentence[:240], "description": f"{text[:420]}\n\nWatch the full moment and save this clip for later."[:1000], "hashtags": (tags + ["#Shorts", "#Clip"])[:8], "explanation": "; ".join(suggestion.reasons_json)[:1000] or "Selected from the transcript using local clip signals."}


def _request_json(url: str, payload: dict, headers: dict[str, str]) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urlopen(request, timeout=50) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise AIProviderError(f"AI provider returned {exc.code}: {detail}") from exc
    except URLError as exc:
        raise AIProviderError(f"Could not reach the AI provider: {exc.reason}") from exc


def _parse_reply(value: str, fallback: dict) -> dict:
    value = value.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    match = re.search(r"\{.*\}", value, re.DOTALL)
    if not match:
        raise AIProviderError("AI returned no JSON metadata.")
    try:
        data = json.loads(match.group())
    except json.JSONDecodeError as exc:
        raise AIProviderError("AI returned invalid JSON metadata.") from exc
    result = dict(fallback)
    for field, limit in (("title", 160), ("hook", 240), ("description", 1000), ("explanation", 1000)):
        if isinstance(data.get(field), str) and data[field].strip():
            result[field] = data[field].strip()[:limit]
    if isinstance(data.get("hashtags"), list):
        tags = [f"#{str(tag).strip().lstrip('#').replace(' ', '')}" for tag in data["hashtags"] if str(tag).strip()]
        result["hashtags"] = tags[:12] or fallback["hashtags"]
    return result


def _prompt(suggestion: ClipSuggestion) -> str:
    return "Create compelling, accurate short-video metadata. Return ONLY JSON with title, hook, description, hashtags, explanation. Do not invent facts. Keep title under 70 chars, hook under 120 chars, description under 350 chars, and 3-8 hashtags.\n\nTranscript:\n" + suggestion.transcript_text[:6000] + "\n\nSelection reasons:\n" + "; ".join(suggestion.reasons_json)


def _generate_with_provider(database: Session, suggestion: ClipSuggestion, provider: str, fallback: dict) -> dict:
    prompt = _prompt(suggestion)
    if provider == "ollama":
        url = read_setting(database, "ollama_url").rstrip("/")
        if not url.startswith(("http://127.0.0.1", "http://localhost")):
            raise AIProviderError("Ollama URL must point to localhost for local-first operation.")
        response = _request_json(f"{url}/api/generate", {"model": read_setting(database, "ollama_model"), "prompt": prompt, "stream": False, "format": "json"}, {})
        return _parse_reply(str(response.get("response", "")), fallback)
    if provider == "gemini":
        api_key = read_setting(database, "gemini_api_key")
        model = read_setting(database, "gemini_model")
        if not api_key:
            raise AIProviderError("Add a Gemini API key in Settings before choosing Gemini.")
        if not _SAFE_MODEL.fullmatch(model):
            raise AIProviderError("Gemini model name contains unsupported characters.")
        response = _request_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json", "temperature": 0.6}}, {"x-goog-api-key": api_key})
        try:
            text = response["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("Gemini returned no usable text response.") from exc
        return _parse_reply(text, fallback)
    return fallback


def generate_metadata(database: Session, suggestion: ClipSuggestion, requested_provider: str | None = None) -> ClipMetadata:
    fallback = _fallback(suggestion)
    provider = requested_provider or read_setting(database, "ai_provider")
    note = None
    try:
        content = _generate_with_provider(database, suggestion, provider, fallback)
    except AIProviderError as exc:
        content, provider, note = fallback, "fallback", str(exc)
    item = database.scalar(select(ClipMetadata).where(ClipMetadata.suggestion_id == suggestion.id))
    if not item:
        item = ClipMetadata(project_id=suggestion.project_id, suggestion_id=suggestion.id, title=content["title"], hook=content["hook"], description=content["description"], hashtags_json=content["hashtags"], explanation=content["explanation"], provider=provider, generation_note=note)
        database.add(item)
    else:
        item.provider, item.generation_note = provider, note
        for key, value in content.items():
            setattr(item, "hashtags_json" if key == "hashtags" else key, value)
    database.commit(); database.refresh(item)
    return item
