"use client";

import { useState } from "react";

import type { RunEvent } from "@/lib/types";

const LEVEL_TEXT: Record<RunEvent["level"], string> = {
  info: "text-ink-dim",
  warning: "text-review",
  error: "text-fail",
};

/** Live run feedback: latest message + progress bar, with the full log one
 * click away. */
export default function RunProgress({ events }: { events: RunEvent[] }) {
  const [expanded, setExpanded] = useState(false);
  if (events.length === 0) return null;
  const last = events[events.length - 1];

  return (
    <div className="border-b border-edge bg-surface-1 px-4 py-2.5">
      <div className="flex items-center gap-3">
        <p className={`min-w-0 flex-1 truncate font-mono text-xs ${LEVEL_TEXT[last.level]}`}>
          [{last.stage}] {last.message}
        </p>
        <button
          onClick={() => setExpanded((v) => !v)}
          className="shrink-0 text-xs text-ink-dim hover:text-ink"
        >
          {expanded ? "Hide log" : `Show log (${events.length})`}
        </button>
      </div>
      {expanded && (
        <div className="mt-2 max-h-48 overflow-y-auto rounded-lg bg-surface-0 p-3 font-mono text-xs">
          {/* index keys are safe: the log is append-only within a run */}
          {events.map((event, index) => (
            <div key={index} className={LEVEL_TEXT[event.level]}>
              [{event.stage}] {event.message}
            </div>
          ))}
        </div>
      )}
      {last.progress != null && last.stage !== "done" && (
        <div className="mt-2 h-1 rounded-full bg-surface-2">
          <div
            className="h-full rounded-full bg-accent transition-[width] duration-300"
            style={{ width: `${Math.round(last.progress * 100)}%` }}
          />
        </div>
      )}
    </div>
  );
}
