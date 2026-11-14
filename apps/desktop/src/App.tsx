import { ChangeEvent, useEffect, useRef, useState } from "react";
import { Database, FileVideo, FolderOpen, Film, HardDrive, Mic, Plus, RefreshCw, RotateCcw, Square, Trash2, Upload, Wrench } from "lucide-react";
import { getHealth, projectApi, type Dashboard, type HealthResponse, type Project, type Transcript, type TranscriptionJob } from "./lib/api";

function ReadinessCard({ label, ready, detail, icon }: { label: string; ready: boolean; detail: string; icon: React.ReactNode }) {
  return <article className="card"><div className="card-header">{icon}<span className={ready ? "dot good" : "dot bad"} /></div><h2>{label}</h2><p>{detail}</p><strong className={ready ? "good-text" : "bad-text"}>{ready ? "Ready" : "Needs setup"}</strong></article>;
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [projectName, setProjectName] = useState("");
  const [selectedProject, setSelectedProject] = useState<Project | null>(null);
  const [activeJob, setActiveJob] = useState<TranscriptionJob | null>(null);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  const [busy, setBusy] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const refresh = async () => {
    setLoading(true); setError(null);
    try { const [healthResponse, dashboardResponse] = await Promise.all([getHealth(), projectApi.dashboard()]); setHealth(healthResponse); setDashboard(dashboardResponse); }
    catch (err) { setError(err instanceof Error ? err.message : "Unable to contact the local API"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  useEffect(() => {
    if (!activeJob || !["queued", "processing", "cancel_requested"].includes(activeJob.status)) return;
    const poll = window.setTimeout(async () => {
      try {
        const job = await projectApi.job(activeJob.id);
        setActiveJob(job);
        if (job.status === "completed" && selectedProject) setTranscripts(await projectApi.transcripts(selectedProject.id));
      } catch (err) { setError(err instanceof Error ? err.message : "Could not read transcription status."); }
    }, 1200);
    return () => window.clearTimeout(poll);
  }, [activeJob, selectedProject]);
  const allToolsReady = health && Object.values(health.tools).every((tool) => tool.available);
  const createProject = async () => {
    if (!projectName.trim()) return setError("Give the project a name first.");
    setBusy(true); setError(null);
    try { const project = await projectApi.create(projectName); setProjectName(""); setSelectedProject(project); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Project could not be created."); }
    finally { setBusy(false); }
  };
  const openProject = async (id: number) => {
    setBusy(true); setError(null);
    try { const [project, savedTranscripts] = await Promise.all([projectApi.get(id), projectApi.transcripts(id)]); setSelectedProject(project); setTranscripts(savedTranscripts); setActiveJob(null); }
    catch (err) { setError(err instanceof Error ? err.message : "Project could not be opened."); }
    finally { setBusy(false); }
  };
  const chooseVideo = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file && selectedProject) void importVideo(file);
  };
  const importVideo = async (file: File) => {
    if (!selectedProject) return;
    setBusy(true); setError(null);
    try { const project = await projectApi.importVideo(selectedProject.id, file); setSelectedProject(project); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Video import failed."); }
    finally { setBusy(false); }
  };
  const deleteProject = async (id: number) => {
    if (!window.confirm("Delete this project and its imported videos?")) return;
    setBusy(true); setError(null);
    try { await projectApi.remove(id); if (selectedProject?.id === id) setSelectedProject(null); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Project could not be deleted."); }
    finally { setBusy(false); }
  };
  const renameProject = async () => {
    if (!selectedProject) return;
    const name = window.prompt("Project name", selectedProject.name)?.trim();
    if (!name || name === selectedProject.name) return;
    setBusy(true); setError(null);
    try { const project = await projectApi.rename(selectedProject.id, name); setSelectedProject(project); await refresh(); }
    catch (err) { setError(err instanceof Error ? err.message : "Project could not be renamed."); }
    finally { setBusy(false); }
  };
  const startTranscript = async (assetId: number) => {
    if (!selectedProject) return;
    setBusy(true); setError(null);
    try { setActiveJob(await projectApi.startTranscript(selectedProject.id, assetId)); }
    catch (err) { setError(err instanceof Error ? err.message : "Transcription could not be started."); }
    finally { setBusy(false); }
  };
  const cancelTranscript = async () => {
    if (!activeJob) return;
    try { setActiveJob(await projectApi.cancelJob(activeJob.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not cancel transcription."); }
  };
  const retryTranscript = async () => {
    if (!activeJob) return;
    try { setActiveJob(await projectApi.retryJob(activeJob.id)); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not retry transcription."); }
  };
  return <main><nav><div className="brand"><Film size={25} /> ClipForge <em>LOCAL</em></div><span>Transcription · Phase 3</span></nav><section className="hero compact"><p className="eyebrow">LOCAL VIDEO PROJECTS</p><h1>Import the source. Find the moments next.</h1><p className="intro">Create a project, add an MP4, MOV, MKV, or WebM file, then generate a timestamped local transcript.</p></section><section className="create-panel"><div><p className="eyebrow">NEW PROJECT</p><h2>Start a clipping workspace</h2></div><div className="create-form"><input value={projectName} onChange={(event) => setProjectName(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void createProject(); }} placeholder="e.g. Podcast Episode 01" maxLength={120} /><button onClick={() => void createProject()} disabled={busy}><Plus size={17} /> Create</button></div></section><section className="stats"><ReadinessCard label="Local projects" ready={Boolean(dashboard)} detail={dashboard ? `${dashboard.project_count} saved project${dashboard.project_count === 1 ? "" : "s"}` : "Loading local database…"} icon={<FolderOpen size={22} />} /><ReadinessCard label="Imported videos" ready={Boolean(dashboard)} detail={dashboard ? `${dashboard.imported_video_count} source file${dashboard.imported_video_count === 1 ? "" : "s"}` : "Reading storage…"} icon={<FileVideo size={22} />} /><ReadinessCard label="Storage used" ready={Boolean(dashboard)} detail={dashboard ? formatBytes(dashboard.storage_bytes) : "Calculating local storage…"} icon={<HardDrive size={22} />} /></section>{error && <p className="error-message">{error}</p>}<section className="project-layout"><div className="project-list"><div className="section-title"><div><p className="eyebrow">RECENT PROJECTS</p><h2>Your local workspaces</h2></div><button className="icon-button" onClick={() => void refresh()} title="Refresh projects" disabled={loading}><RefreshCw size={17} /></button></div>{dashboard?.recent_projects.length ? dashboard.recent_projects.map((project) => <article className={`project-row ${selectedProject?.id === project.id ? "selected" : ""}`} key={project.id}><button className="project-open" onClick={() => void openProject(project.id)}><span className="project-icon"><Film size={18} /></span><span><strong>{project.name}</strong><small>{project.assets.length ? `${project.assets.length} video${project.assets.length === 1 ? "" : "s"}` : "No video imported"} · Updated {dateLabel(project.updated_at)}</small></span></button><button className="delete-button" onClick={() => void deleteProject(project.id)} title={`Delete ${project.name}`}><Trash2 size={16} /></button></article>) : <div className="empty-state"><FolderOpen size={26} /><p>No projects yet. Create one to import your first video.</p></div>}</div><div className="details-panel">{selectedProject ? <><div className="section-title"><div><p className="eyebrow">PROJECT DETAILS</p><h2>{selectedProject.name}</h2></div><button className="text-button" onClick={() => void renameProject()} disabled={busy}>Rename</button></div>{selectedProject.assets.length ? <div className="asset-list">{selectedProject.assets.map((asset) => <article className="asset-card" key={asset.id}><FileVideo size={24} /><div><strong>{asset.media_type.toUpperCase()} source video</strong><p>{asset.width}×{asset.height} · {asset.fps?.toFixed(2)} FPS · {formatDuration(asset.duration_seconds)} · {formatBytes(asset.size_bytes ?? 0)}</p><small>{asset.original_path}</small><button className="mini-action" disabled={busy || Boolean(activeJob && ["queued", "processing", "cancel_requested"].includes(activeJob.status))} onClick={() => void startTranscript(asset.id)}><Mic size={14} /> Generate transcript</button></div></article>)}</div> : <div className="empty-state"><Upload size={26} /><p>This project has no source video yet.</p></div>}{activeJob && <section className="job-card"><div><p className="eyebrow">TRANSCRIPTION JOB</p><strong>{activeJob.status.replace("_", " ")} · {activeJob.progress}%</strong>{activeJob.error_message && <p className="job-error">{activeJob.error_message}</p>}</div><div>{["queued", "processing", "cancel_requested"].includes(activeJob.status) ? <button className="text-button" onClick={() => void cancelTranscript()}><Square size={13} /> Cancel</button> : activeJob.status !== "completed" ? <button className="text-button" onClick={() => void retryTranscript()}><RotateCcw size={13} /> Retry</button> : null}</div><div className="progress-track"><span style={{ width: `${activeJob.progress}%` }} /></div></section>}{transcripts[0] && <section className="transcript-summary"><p className="eyebrow">LATEST TRANSCRIPT</p><strong>{transcripts[0].language?.toUpperCase() ?? "AUTO"} · {transcripts[0].segments.length} segments</strong><p>{transcripts[0].segments.slice(0, 2).map((segment) => segment.text).join(" ") || "No speech detected."}</p></section>}<input ref={uploadInput} className="visually-hidden" type="file" accept=".mp4,.mov,.mkv,.webm,video/mp4,video/quicktime,video/x-matroska,video/webm" onChange={chooseVideo} /><button className="upload-button" onClick={() => uploadInput.current?.click()} disabled={busy}><Upload size={17} /> {busy ? "Working…" : "Import video"}</button><p className="hint">MP4, MOV, MKV, or WebM · Transcript uses faster-whisper + Silero VAD locally.</p></> : <div className="details-empty"><Film size={34} /><h2>Select a project</h2><p>Choose a recent project to import video and generate a transcript.</p></div>}</div></section><section className="foundation-status"><Database size={16} /> <span>{error ? "The API needs attention." : health?.database === "connected" ? "SQLite persistence connected" : "Checking local API"}</span><Wrench size={16} /> <span>{allToolsReady ? "FFmpeg tools ready" : "FFmpeg needs setup"}</span></section><p className="footer">All processing stays on your computer.</p></main>;
}

function formatBytes(bytes: number) { if (!bytes) return "0 B"; const units = ["B", "KB", "MB", "GB", "TB"]; const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1); return `${(bytes / 1024 ** index).toFixed(index ? 1 : 0)} ${units[index]}`; }
function formatDuration(seconds: number | null) { if (!seconds) return "0:00"; const total = Math.round(seconds); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; }
function dateLabel(value: string) { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value)); }
