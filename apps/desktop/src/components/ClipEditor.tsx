import { useEffect, useRef, useState } from "react";
import { Clapperboard, Crop, Film, SlidersHorizontal } from "lucide-react";
import { projectApi, type ClipEdit, type ClipSuggestion, type Project, type Transcript, type Waveform } from "../lib/api";

type Props = { project: Project; suggestions: ClipSuggestion[]; transcripts: Transcript[]; onError: (message: string) => void };

const ratios: ClipEdit["aspect_ratio"][] = ["9:16", "1:1", "16:9"];
const crops: ClipEdit["crop_mode"][] = ["center", "blurred", "split", "manual"];

export default function ClipEditor({ project, suggestions, transcripts, onError }: Props) {
  const [suggestion, setSuggestion] = useState<ClipSuggestion | null>(null);
  const [edit, setEdit] = useState<ClipEdit | null>(null);
  const [waveform, setWaveform] = useState<Waveform | null>(null);
  const [playhead, setPlayhead] = useState(0);
  const original = useRef<HTMLVideoElement>(null);
  const selected = useRef<HTMLVideoElement>(null);
  const asset = project.assets.find((item) => item.id === suggestion?.asset_id);
  const transcript = transcripts.find((item) => item.asset_id === asset?.id);

  useEffect(() => { setSuggestion(null); setEdit(null); setWaveform(null); }, [project.id]);
  useEffect(() => {
    if (!edit) return;
    const timer = window.setTimeout(async () => {
      try { setEdit(await projectApi.updateEdit(edit.id, { start_seconds: edit.start_seconds, end_seconds: edit.end_seconds, aspect_ratio: edit.aspect_ratio, crop_mode: edit.crop_mode, manual_x: edit.manual_x, manual_y: edit.manual_y })); }
      catch (error) { onError(error instanceof Error ? error.message : "Could not save clip edit."); }
    }, 650);
    return () => window.clearTimeout(timer);
  }, [edit?.start_seconds, edit?.end_seconds, edit?.aspect_ratio, edit?.crop_mode, edit?.manual_x, edit?.manual_y]);

  const open = async (next: ClipSuggestion) => {
    try {
      const [savedEdit, savedWaveform] = await Promise.all([projectApi.createEdit(project.id, next.asset_id, next.id), projectApi.waveform(next.asset_id)]);
      setSuggestion(next); setEdit(savedEdit); setWaveform(savedWaveform); setPlayhead(savedEdit.start_seconds);
      window.setTimeout(() => { if (original.current) original.current.currentTime = savedEdit.start_seconds; if (selected.current) selected.current.currentTime = savedEdit.start_seconds; }, 20);
    } catch (error) { onError(error instanceof Error ? error.message : "Could not open clip editor."); }
  };
  const update = (patch: Partial<ClipEdit>) => setEdit((current) => current ? { ...current, ...patch } : current);
  const sync = (time: number) => {
    setPlayhead(time);
    if (edit && time > edit.end_seconds && selected.current) { selected.current.pause(); selected.current.currentTime = edit.start_seconds; }
  };
  if (!suggestions.length) return null;
  return <section className="clip-editor"><header><div><p className="eyebrow">CLIP WORKSPACE</p><h2>Shape the selected moment</h2></div>{edit && <small>Saving edits locally</small>}</header><div className="editor-grid"><aside className="editor-suggestions"><p className="editor-label"><Clapperboard size={15} /> Suggestions</p>{suggestions.map((item) => <button key={item.id} onClick={() => void open(item)} className={suggestion?.id === item.id ? "editor-suggestion active" : "editor-suggestion"}><b>{item.score}</b><span><strong>{formatTime(item.start_seconds)}–{formatTime(item.end_seconds)}</strong><small>{item.title}</small></span></button>)}</aside><section className="editor-stage">{edit && asset ? <><div className={`video-pair ratio-${edit.aspect_ratio.replace(":", "-")} crop-${edit.crop_mode}`}><div><span>Original</span><video ref={original} src={projectApi.mediaStreamUrl(asset.id)} controls onTimeUpdate={(event) => sync(event.currentTarget.currentTime)} /></div><div className="clip-preview"><span>Selected clip</span><video ref={selected} src={projectApi.mediaStreamUrl(asset.id)} controls onTimeUpdate={(event) => sync(event.currentTarget.currentTime)} style={{ objectPosition: `${edit.manual_x}% ${edit.manual_y}%` }} /></div></div><div className="waveform"><div className="wave-bars">{waveform?.peaks.map((peak, index) => <i key={index} style={{ height: `${Math.max(8, peak * 100)}%`, opacity: index / (waveform.peaks.length || 1) * (waveform.duration_seconds || 1) >= edit.start_seconds && index / (waveform.peaks.length || 1) * (waveform.duration_seconds || 1) <= edit.end_seconds ? 1 : .25 }} />)}</div><div className="trim-range"><input aria-label="Clip start" type="range" min="0" max={asset.duration_seconds ?? edit.end_seconds} step="0.1" value={edit.start_seconds} onChange={(event) => update({ start_seconds: Math.min(Number(event.target.value), edit.end_seconds - .1) })} /><input aria-label="Clip end" type="range" min="0" max={asset.duration_seconds ?? edit.end_seconds} step="0.1" value={edit.end_seconds} onChange={(event) => update({ end_seconds: Math.max(Number(event.target.value), edit.start_seconds + .1) })} /></div><p>{formatTime(edit.start_seconds)} <span>→</span> {formatTime(edit.end_seconds)} <em>{(edit.end_seconds - edit.start_seconds).toFixed(1)} sec</em></p></div>{transcript && <div className="sync-transcript">{transcript.segments.filter((segment) => segment.end >= edit.start_seconds && segment.start <= edit.end_seconds).map((segment, index) => <button key={index} className={playhead >= segment.start && playhead <= segment.end ? "spoken" : ""} onClick={() => { if (original.current) original.current.currentTime = segment.start; if (selected.current) selected.current.currentTime = segment.start; setPlayhead(segment.start); }}><time>{formatTime(segment.start)}</time>{segment.text}</button>)}</div>}</> : <div className="editor-empty"><Film size={28} /><p>Select a smart suggestion to open its visual editor.</p></div>}</section><aside className="editor-controls">{edit ? <><p className="editor-label"><SlidersHorizontal size={15} /> Clip settings</p><label>Aspect ratio<div className="choice-row">{ratios.map((ratio) => <button className={edit.aspect_ratio === ratio ? "picked" : ""} key={ratio} onClick={() => update({ aspect_ratio: ratio })}>{ratio}</button>)}</div></label><label><Crop size={14} /> Crop mode<div className="choice-stack">{crops.map((crop) => <button className={edit.crop_mode === crop ? "picked" : ""} key={crop} onClick={() => update({ crop_mode: crop })}>{crop === "blurred" ? "Blurred background" : crop === "split" ? "Split-screen" : crop === "manual" ? "Manual position" : "Center crop"}</button>)}</div></label>{edit.crop_mode === "manual" && <><label>Horizontal position<input type="range" min="0" max="100" value={edit.manual_x} onChange={(event) => update({ manual_x: Number(event.target.value) })} /></label><label>Vertical position<input type="range" min="0" max="100" value={edit.manual_y} onChange={(event) => update({ manual_y: Number(event.target.value) })} /></label></>}<p className="editor-hint">Edits save automatically. Export comes in Phase 6.</p></> : <div className="editor-empty"><SlidersHorizontal size={25} /><p>Timeline and crop controls appear here.</p></div>}</aside></div></section>;
}

function formatTime(seconds: number) { const total = Math.max(0, Math.floor(seconds)); return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`; }
