"use client";

// Owns the lifecycle of a verification run: start → SSE events → done/error.
// The subscription is tied to component lifetime (unsubscribed on unmount and
// when a new run starts), so no EventSource leaks and no setState after
// unmount.

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "./api";
import type { RunEvent } from "./types";

export interface VerificationRun {
  events: RunEvent[];
  running: boolean;
  runError: string | null;
  start: () => Promise<void>;
}

export function useVerificationRun(
  projectId: string,
  onFinished: () => void,
): VerificationRun {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);

  const unsubscribeRef = useRef<(() => void) | null>(null);
  const mountedRef = useRef(true);
  const onFinishedRef = useRef(onFinished);
  onFinishedRef.current = onFinished;

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      unsubscribeRef.current?.();
    };
  }, []);

  const start = useCallback(async () => {
    unsubscribeRef.current?.(); // a new run supersedes any previous one
    setEvents([]);
    setRunError(null);
    setRunning(true);
    try {
      const { run_id } = await api.startVerification(projectId);
      if (!mountedRef.current) return;
      unsubscribeRef.current = api.subscribeToRun(run_id, {
        onEvent: (event) => {
          if (mountedRef.current) setEvents((prev) => [...prev, event]);
        },
        onDone: () => {
          if (!mountedRef.current) return;
          setRunning(false);
          onFinishedRef.current();
        },
        onError: (message) => {
          if (!mountedRef.current) return;
          setRunning(false);
          setRunError(message);
          onFinishedRef.current(); // partial results may still exist
        },
      });
    } catch (err) {
      if (!mountedRef.current) return;
      setRunning(false);
      setRunError(err instanceof Error ? err.message : String(err));
    }
  }, [projectId]);

  return { events, running, runError, start };
}
