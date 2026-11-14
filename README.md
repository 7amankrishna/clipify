# ClipForge Local

Local-first desktop software for turning long-form video into short clips. This repository currently contains **Phases 1–3: foundation, project/video import, and local transcription**.

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
