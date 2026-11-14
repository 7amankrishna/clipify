import { afterEach, describe, expect, it, vi } from "vitest";
import { getHealth } from "./api";

describe("getHealth", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns the FastAPI readiness payload", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ status: "ok", service: "ClipForge", version: "0.1.0", database: "connected", tools: {} }),
    }));

    await expect(getHealth()).resolves.toMatchObject({ status: "ok", database: "connected" });
  });
});
