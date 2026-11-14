import { useEffect, useState } from "react";
import { Database, Film, Server, Wrench } from "lucide-react";
import { getHealth, type HealthResponse } from "./lib/api";

function ReadinessCard({ label, ready, detail, icon }: { label: string; ready: boolean; detail: string; icon: React.ReactNode }) {
  return <article className="card"><div className="card-header">{icon}<span className={ready ? "dot good" : "dot bad"} /></div><h2>{label}</h2><p>{detail}</p><strong className={ready ? "good-text" : "bad-text"}>{ready ? "Ready" : "Needs setup"}</strong></article>;
}

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const refresh = async () => {
    setLoading(true); setError(null);
    try { setHealth(await getHealth()); } catch (err) { setError(err instanceof Error ? err.message : "Unable to contact the local API"); }
    finally { setLoading(false); }
  };
  useEffect(() => { void refresh(); }, []);
  const allToolsReady = health && Object.values(health.tools).every((tool) => tool.available);
  return <main><nav><div className="brand"><Film size={25} /> ClipForge <em>LOCAL</em></div><span>Foundation · Phase 1</span></nav><section className="hero"><p className="eyebrow">LOCAL VIDEO WORKSPACE</p><h1>Your clipping studio is ready to take shape.</h1><p className="intro">This foundation verifies the local services that will power uploads, transcription, smart clips, and exports.</p></section><section className="grid"><ReadinessCard label="FastAPI service" ready={health?.status === "ok"} detail={error ?? (health ? `${health.service} v${health.version}` : "Checking localhost:8000…")} icon={<Server size={22} />} /><ReadinessCard label="SQLite workspace" ready={health?.database === "connected"} detail={health?.database === "connected" ? "Persistent local database connected" : "Created automatically when API starts"} icon={<Database size={22} />} /><ReadinessCard label="Media tools" ready={Boolean(allToolsReady)} detail={health ? `FFmpeg ${health.tools.ffmpeg.available ? "found" : "missing"} · FFprobe ${health.tools.ffprobe.available ? "found" : "missing"}` : "Checking FFmpeg and FFprobe…"} icon={<Wrench size={22} />} /></section><section className="next"><div><p className="eyebrow">NEXT UP</p><h2>Video Upload & Project Management</h2><p>Phase 2 will create video projects, import files, and save metadata locally.</p></div><button onClick={() => void refresh()} disabled={loading}>{loading ? "Checking…" : "Check local services"}</button></section><p className="footer">{error ? "Start the FastAPI server, then check again." : "All processing stays on your computer."}</p></main>;
}
