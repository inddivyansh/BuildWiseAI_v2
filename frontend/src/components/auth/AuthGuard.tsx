"use client";

import React, { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/context/AuthContext";
import { Building2, Loader2 } from "lucide-react";

const PUBLIC_ROUTES = ["/login", "/signup"];

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth();
  const pathname = usePathname();
  const router = useRouter();

  const isPublicRoute = PUBLIC_ROUTES.includes(pathname);

  useEffect(() => {
    if (loading) return;

    if (!user && !isPublicRoute) {
      const redirectUrl = pathname !== "/" ? `/login?redirect=${encodeURIComponent(pathname)}` : "/login";
      router.replace(redirectUrl);
    } else if (user && isPublicRoute) {
      const target =
        (typeof window !== "undefined"
          ? new URLSearchParams(window.location.search).get("redirect")
          : null) || "/";
      router.replace(target);
    }
  }, [user, loading, isPublicRoute, pathname, router]);

  if (loading) {
    return (
      <div className="min-h-screen w-full bg-surface-0 flex flex-col items-center justify-center gap-4 text-ink">
        <div className="w-12 h-12 rounded-xl bg-accent/20 border border-accent/40 flex items-center justify-center text-accent animate-pulse">
          <Building2 size={28} />
        </div>
        <div className="flex items-center gap-2 text-ink-dim text-sm">
          <Loader2 size={16} className="animate-spin text-accent" />
          <span>Restoring session...</span>
        </div>
      </div>
    );
  }

  // If unauthenticated and on a protected route, avoid flashing protected content while redirecting
  if (!user && !isPublicRoute) {
    return (
      <div className="min-h-screen w-full bg-surface-0 flex items-center justify-center">
        <Loader2 size={24} className="animate-spin text-accent" />
      </div>
    );
  }

  return <>{children}</>;
}
