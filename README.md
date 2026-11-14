# ClipForge Local

Local-first desktop software for turning long-form video into short clips. This repository currently contains **Phase 1–2: local foundation and project/video import management**.

It ships a React/Vite/TypeScript desktop UI, a FastAPI + SQLite service, FFmpeg/FFprobe detection, Docker support for the API, a Tauri v2 shell configuration, and fully local project/video importing.

## Included in Phase 2

- Create, rename, open, and delete video projects.
- Import MP4, MOV, MKV, and WebM source videos.
- Inspect duration, resolution, frame rate, and size with FFprobe.
- Persist projects and media metadata in SQLite; store source videos under `data/projects/`.
- View recent projects and local storage usage from the dashboard.

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
