export type HealthResponse = {
  status: "ok";
  service: string;
  version: string;
  database: string;
  tools: Record<string, { available: boolean; path: string | null; version: string | null }>;
};

const API_BASE = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000/api/v1";

export async function getHealth(): Promise<HealthResponse> {
  const response = await fetch(`${API_BASE}/health`);
  if (!response.ok) throw new Error(`API health check failed (${response.status})`);
  return response.json() as Promise<HealthResponse>;
}
