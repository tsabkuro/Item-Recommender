import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import type { AppState } from "./types";


export function useAppState(intervalMs = 500) {
  const [state, setState] = useState<AppState | null>(null);
  const [offline, setOffline] = useState(false);
  const lastRequest = useRef(0);

  const refresh = useCallback(async () => {
    const request = ++lastRequest.current;
    try {
      const next = await api.state();
      if (request === lastRequest.current) {
        setState(next);
        setOffline(false);
      }
      return next;
    } catch {
      if (request === lastRequest.current) setOffline(true);
      return null;
    }
  }, []);

  useEffect(() => {
    let timer: number | undefined;
    let stopped = false;
    async function poll() {
      const next = await refresh();
      if (!stopped) timer = window.setTimeout(poll, next && isCatchingUp(next) ? 150 : intervalMs);
    }
    poll();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [refresh, intervalMs]);

  return { state, offline, refresh };
}

export function isCatchingUp(state: AppState): boolean {
  if (state.status !== "ok" || !state.replay) return false;
  return Math.abs(state.game_time - state.replay.position) > 2 + 1.5 * state.replay.speed;
}
