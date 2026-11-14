# ClipForge Local

Local-first desktop software for turning long-form video into short clips. This repository currently contains **Phase 1: Project Foundation & Local Environment**.

It ships a React/Vite/TypeScript desktop UI, a FastAPI + SQLite service, FFmpeg/FFprobe health detection, Docker support for the API, and a Tauri v2 shell configuration.

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
