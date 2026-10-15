import type { AppState } from "./types";

async function post(path: string, body: unknown): Promise<void> {
  await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

export const api = {
  async state(): Promise<AppState> {
    const response = await fetch("/api/state", { cache: "no-store" });
    if (!response.ok) throw new Error(`GET /api/state: ${response.status}`);
    return response.json();
  },
  setTier: (tier: string) => post("/api/tier", { tier }),
  pause: (paused: boolean) => post("/api/replay/pause", { paused }),
  seek: (seconds: number) => post("/api/replay/seek", { seconds }),
  speed: (speed: number) => post("/api/replay/speed", { speed }),
};
