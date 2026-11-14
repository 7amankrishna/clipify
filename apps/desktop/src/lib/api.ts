export type HealthResponse = {
  status: "ok";
  service: string;
  version: string;
  database: string;
  tools: Record<string, { available: boolean; path: string | null; version: string | null }>;
};

export type MediaAsset = {
  id: number;
  original_path: string;
  media_type: string;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  size_bytes: number | null;
  created_at: string;
};

export type Project = {
  id: number;
  name: string;
  status: string;
  created_at: string;
  updated_at: string;
  assets: MediaAsset[];
};

export type Dashboard = {
  project_count: number;
  imported_video_count: number;
  storage_bytes: number;
  recent_projects: Project[];
};

export type TranscriptionJob = {
  id: number;
  project_id: number | null;
  job_type: string;
  status: string;
  progress: number;
  error_message: string | null;
  metadata_json: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};

export type Transcript = {
  id: number;
  project_id: number;
  asset_id: number;
  job_id: number;
  language: string | null;
  language_probability: number | null;
  segments: Array<{ start: number; end: number; text: string; words: Array<{ word: string; start: number; end: number; probability: number | null }> }>;
  created_at: string;
};

export type ClipSuggestion = {
  id: number;
  project_id: number;
  asset_id: number;
  analysis_job_id: number;
  start_seconds: number;
  end_seconds: number;
  score: number;
  transcript_text: string;
  title: string;
  reasons: string[];
  metrics: Record<string, number | string | null>;
  created_at: string;
};

export type ClipEdit = {
  id: number;
  project_id: number;
  asset_id: number;
  suggestion_id: number | null;
  start_seconds: number;
  end_seconds: number;
  aspect_ratio: "9:16" | "1:1" | "16:9";
  crop_mode: "center" | "blurred" | "split" | "manual";
  manual_x: number;
  manual_y: number;
  created_at: string;
  updated_at: string;
};

export type Waveform = { asset_id: number; duration_seconds: number | null; peaks: number[] };

export type CaptionWord = { word: string; start: number; end: number; line: number };
export type CaptionTrack = { id: number; clip_edit_id: number; preset: "bold_yellow" | "white_minimal" | "neon_creator"; words: CaptionWord[]; updated_at: string };
export type VideoExport = { id: number; project_id: number; clip_edit_id: number; job_id: number; is_preview: boolean; status: string; output_path: string | null; error_message: string | null; created_at: string; updated_at: string };

const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000/api/v1";

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw new Error(`API health check failed (${response.status})`);
  return response.json() as Promise<HealthResponse>;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, init);
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `Request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const projectApi = {
  dashboard: () => request<Dashboard>("/dashboard"),
  create: (name: string) => request<Project>("/projects", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }),
  get: (id: number) => request<Project>(`/projects/${id}`),
  rename: (id: number, name: string) => request<Project>(`/projects/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name }) }),
  remove: (id: number) => request<void>(`/projects/${id}`, { method: "DELETE" }),
  importVideo: (id: number, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Project>(`/projects/${id}/media`, { method: "POST", body: form });
  },
  startTranscript: (projectId: number, assetId: number) => request<TranscriptionJob>(`/projects/${projectId}/transcriptions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_id: assetId, model: "base", language: "auto", device: "auto" }) }),
  transcripts: (projectId: number) => request<Transcript[]>(`/projects/${projectId}/transcripts`),
  job: (jobId: number) => request<TranscriptionJob>(`/jobs/${jobId}`),
  cancelJob: (jobId: number) => request<TranscriptionJob>(`/jobs/${jobId}/cancel`, { method: "POST" }),
  retryJob: (jobId: number) => request<TranscriptionJob>(`/jobs/${jobId}/retry`, { method: "POST" }),
  startClipAnalysis: (projectId: number, assetId: number) => request<TranscriptionJob>(`/projects/${projectId}/clip-analysis`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_id: assetId, min_duration_seconds: 15, max_duration_seconds: 60 }) }),
  suggestions: (projectId: number, assetId?: number) => request<ClipSuggestion[]>(`/projects/${projectId}/suggestions${assetId ? `?asset_id=${assetId}` : ""}`),
  createEdit: (projectId: number, assetId: number, suggestionId: number) => request<ClipEdit>(`/projects/${projectId}/edits`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ asset_id: assetId, suggestion_id: suggestionId }) }),
  updateEdit: (editId: number, patch: Partial<Pick<ClipEdit, "start_seconds" | "end_seconds" | "aspect_ratio" | "crop_mode" | "manual_x" | "manual_y">>) => request<ClipEdit>(`/edits/${editId}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }),
  waveform: (assetId: number) => request<Waveform>(`/media/${assetId}/waveform?points=180`),
  mediaStreamUrl: (assetId: number) => `${API_BASE}/media/${assetId}/stream`,
  captions: (editId: number) => request<CaptionTrack>(`/edits/${editId}/captions`),
  updateCaptions: (editId: number, patch: Partial<Pick<CaptionTrack, "preset" | "words">>) => request<CaptionTrack>(`/edits/${editId}/captions`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(patch) }),
  createExport: (editId: number, preview: boolean) => request<VideoExport>(`/edits/${editId}/exports`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ preview }) }),
  exports: (projectId: number) => request<VideoExport[]>(`/projects/${projectId}/exports`),
  downloadExportUrl: (exportId: number) => `${API_BASE}/exports/${exportId}/download`,
  openExport: (exportId: number) => request<{ path: string }>(`/exports/${exportId}/open`, { method: "POST" }),
};
