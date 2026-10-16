"use client";

import { useCallback, useRef, useState } from "react";

interface Toast {
  id: number;
  message: string;
}

export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);

  const push = useCallback((message: string) => {
    const id = nextId.current++;
    setToasts((prev) => [...prev, { id, message }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  return { toasts, push };
}

/** Bottom-right success notices. Errors don't go here — they get persistent
 * inline banners instead. */
export default function Toasts({ toasts }: { toasts: Toast[] }) {
  if (toasts.length === 0) return null;
  return (
    <div className="fixed right-4 bottom-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          role="status"
          className="rounded-lg border border-edge bg-surface-2 px-4 py-2.5 shadow-lg"
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
}
