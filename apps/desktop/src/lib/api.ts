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
};
