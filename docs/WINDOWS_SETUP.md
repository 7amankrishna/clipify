# Windows setup (Phases 1–2)

## Prerequisites

Install these once:

1. **Node.js 20 LTS or later** — verify with `node --version`.
2. **Python 3.11 or 3.12** — verify with `py --version`.
3. **FFmpeg** — install a Windows build, add its `bin` directory to `PATH`, then verify both `ffmpeg -version` and `ffprobe -version`.
4. For the desktop shell later: **Rust stable** and Microsoft **C++ Build Tools**. Phase 1 can run in a browser without them.

## Run locally

From the repository root in PowerShell:

```powershell
copy .env.example .env
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r services\api\requirements.txt
npm install
```

Start the API in one PowerShell window:

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --app-dir services\api --reload --host 127.0.0.1 --port 8000
```

Start the user interface in another:

```powershell
npm run dev
```

Open `http://127.0.0.1:5173`. The three readiness cards confirm the real connection to FastAPI, SQLite, and FFmpeg tools.

Create a project from the dashboard, then import an MP4, MOV, MKV, or WebM video. ClipForge copies it into its local `data/projects` storage and uses FFprobe to show its metadata.

## Verification

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/v1/health
pytest services/api/tests -q
npm run build
```

## Docker API option

```powershell
docker compose up --build
```

The container is explicitly bound to `127.0.0.1:8000` on the host. Desktop packaging and Python worker lifecycle will be completed in Phase 8.
