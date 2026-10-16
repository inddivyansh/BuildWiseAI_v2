"use client";

import { useEffect, useRef } from "react";

export interface ConfirmRequest {
  title: string;
  body: string;
  confirmLabel?: string;
  onConfirm: () => void;
}

/** Minimal accessible confirmation modal: Escape/backdrop cancels, Cancel is
 * focused by default so Enter never destroys anything by accident. */
export default function ConfirmDialog({
  request,
  onClose,
}: {
  request: ConfirmRequest | null;
  onClose: () => void;
}) {
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!request) return;
    cancelRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request, onClose]);

  if (!request) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        className="w-full max-w-sm rounded-xl border border-edge bg-surface-1 p-6 shadow-2xl"
      >
        <h2 id="confirm-title" className="text-base font-semibold">
          {request.title}
        </h2>
        <p className="mt-2 text-ink-dim">{request.body}</p>
        <div className="mt-6 flex justify-end gap-2">
          <button
            ref={cancelRef}
            onClick={onClose}
            className="rounded-lg border border-edge bg-surface-2 px-3 py-1.5 hover:bg-edge focus:outline-2 focus:outline-accent"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              request.onConfirm();
              onClose();
            }}
            className="rounded-lg bg-fail/90 px-3 py-1.5 font-medium text-surface-0 hover:bg-fail focus:outline-2 focus:outline-accent"
          >
            {request.confirmLabel ?? "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}
