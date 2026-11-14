# ClipForge Local

Local-first desktop software for turning long-form video into short clips. This repository currently contains **Phases 1–7: foundation, import, transcription, smart clip detection, visual editing, captioned exports, and optional AI metadata**.

It ships a React/Vite/TypeScript desktop UI, a FastAPI + SQLite service, FFmpeg/FFprobe detection, Docker support for the API, a Tauri v2 shell configuration, and fully local project/video importing.

## Included in Phase 2

- Create, rename, open, and delete video projects.
- Import MP4, MOV, MKV, and WebM source videos.
- Inspect duration, resolution, frame rate, and size with FFprobe.
- Persist projects and media metadata in SQLite; store source videos under `data/projects/`.
- View recent projects and local storage usage from the dashboard.

## Included in Phase 3

- Queue background transcription jobs with actual persisted progress, cancellation, and retry states.
- Extract mono 16 kHz audio with FFmpeg.
- Transcribe locally with faster-whisper, word-level timestamps, English/Hindi/Hinglish support, and automatic language detection.
- Use faster-whisper's Silero VAD filter to remove non-speech gaps.
- Save transcript records in SQLite and readable JSON files under `data/projects/<id>/transcripts/`.

Install the optional local AI runtime before generating transcripts:

```powershell
pip install -r services/api/requirements-ai.txt
```

The selected Whisper model downloads once on first use and is cached by the worker afterward. NVIDIA CUDA is selected automatically when the installed CTranslate2 runtime supports it; CPU is always available.

## Included in Phase 4

- Turn timestamped transcripts into candidate short clips in a configurable duration range.
- Score hooks, questions, contrast/punchline language, speaking rate, silence ratio, and real FFmpeg audio energy.
- Rank suggestions from 0–100 with a visible explanation and metrics for every pick.
- Remove duplicate text and heavily overlapping clips before saving the top recommendations.
- Run analysis in the background with genuine progress, cancellation, retries, and SQLite persistence.

## Included in Phase 5

- Open any smart suggestion as a saved clip edit.
- Preview the original source alongside a visual selected-clip preview.
- Decode a local waveform from the source audio and trim with start/end timeline sliders.
- Follow the transcript while the video plays; click a line to seek both previews.
- Choose 9:16, 1:1, or 16:9 framing and center, blurred-background, split-screen, or manual crop positioning.
- Automatically persist every timeline and framing change to SQLite.

## Included in Phase 6

- Edit saved word- and line-level caption text without losing its timestamps.
- Choose Bold Yellow, White Minimal, or Neon Creator animated karaoke-caption presets.
- Write Unicode-safe ASS subtitles from timestamped words, with mobile-safe caption margins.
- Render low-resolution previews or H.264/AAC MP4 exports through a real local FFmpeg pipeline.
- Apply the selected ratio and crop mode during export; default vertical exports are 1080 × 1920.
- Track real render progress, cancellation, failures, and retries; download completed files from the workspace.

## Included in Phase 7

- Generate and edit hooks, titles, descriptions, explanations, and hashtags for suggested clips.
- Use a deterministic local fallback without an LLM, or opt into local Ollama or Gemini metadata generation.
- Configure provider, model, Whisper preferences, GPU/CPU mode, and theme from the local Settings panel.
- Store settings and generated metadata in SQLite. Gemini keys are write-only in the API and are never returned to the desktop UI.
- Send only the selected clip transcript to Gemini when you explicitly generate metadata; source video and normal processing remain local.

## Quick start

See [Windows setup](docs/WINDOWS_SETUP.md). The everyday development commands are:

```powershell
copy .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r services/api/requirements.txt
npm install
npm run dev
```

In another terminal:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --app-dir services/api --reload --host 127.0.0.1 --port 8000
```

Open `http://localhost:5173`. The dashboard will call FastAPI and show database and FFmpeg readiness.

## Structure

```text
apps/desktop/      React + Vite UI and Tauri shell
services/api/      FastAPI application, SQLite models, API tests
docs/              Setup and architecture notes
```
