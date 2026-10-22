"use client";

import React, { useState, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Building2, Lock, Mail, ArrowRight, Loader2, Sparkles, AlertCircle } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { toast } from "sonner";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectPath = searchParams.get("redirect") || "/";
  const isExpired = searchParams.get("expired") === "1";

  const { signIn } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError("Please enter both email and password.");
      return;
    }

    setError(null);
    setLoading(true);

    try {
      await signIn(email.trim(), password);
      toast.success("Welcome back!");
      router.replace(redirectPath);
    } catch (err: any) {
      const msg = err.message || "Failed to sign in. Please verify your credentials.";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  async function handleQuickDemo() {
    setEmail("architect@buildwise.ai");
    setPassword("DemoPassword123!");
    setError(null);
    setLoading(true);
    try {
      await signIn("architect@buildwise.ai", "DemoPassword123!");
      toast.success("Welcome, Demo Architect!");
      router.replace(redirectPath);
    } catch (err: any) {
      const msg = err.message || "Failed to sign in with demo account.";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }



  return (
    <div className="min-h-screen w-full bg-surface-0 flex flex-col justify-center items-center px-4 sm:px-6 lg:px-8 relative overflow-hidden">
      {/* Background radial gradient glow */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-accent/10 rounded-full blur-[140px] pointer-events-none" />

      <div className="w-full max-w-md relative z-10">
        {/* Brand header */}
        <div className="text-center mb-8">
          <Link href="/" className="inline-flex items-center gap-3 group mb-4">
            <div className="w-12 h-12 rounded-xl bg-accent/20 border border-accent/40 flex items-center justify-center text-accent group-hover:bg-accent group-hover:text-white transition-all shadow-lg shadow-accent/10">
              <Building2 size={26} />
            </div>
            <div className="text-left">
              <span className="font-semibold text-xl tracking-tight text-ink flex items-center gap-1.5">
                BuildwiseAI
                <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-surface-2 text-ink-dim border border-edge">
                  v0.2
                </span>
              </span>
              <p className="text-xs text-ink-dim">AEC Generation + Compliance Platform</p>
            </div>
          </Link>
          <h1 className="text-2xl font-bold tracking-tight text-ink">Sign in to your account</h1>
          <p className="text-xs text-ink-dim mt-1.5">
            Manage projects, generate blueprints, and run automated compliance audits.
          </p>
        </div>

        {/* Expired session banner */}
        {isExpired && (
          <div className="mb-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/20 text-amber-300 text-xs flex items-center gap-2">
            <AlertCircle size={15} className="shrink-0" />
            <span>Your session has expired. Please sign in again to continue.</span>
          </div>
        )}

        {/* Error banner */}
        {error && (
          <div className="mb-4 p-3 rounded-lg bg-red-500/10 border border-red-500/20 text-red-300 text-xs flex items-center gap-2">
            <AlertCircle size={15} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Form Card */}
        <Card className="p-6 bg-surface-1/80 border-edge backdrop-blur-xl shadow-2xl">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <Label htmlFor="email" className="text-xs font-medium text-ink">
                Email address
              </Label>
              <div className="relative mt-1.5">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" size={16} />
                <Input
                  id="email"
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="name@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="pl-9 bg-surface-2 border-edge text-ink text-sm focus:border-accent"
                />
              </div>
            </div>

            <div>
              <div className="flex items-center justify-between">
                <Label htmlFor="password" className="text-xs font-medium text-ink">
                  Password
                </Label>
              </div>
              <div className="relative mt-1.5">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" size={16} />
                <Input
                  id="password"
                  type="password"
                  required
                  autoComplete="current-password"
                  placeholder="••••••••"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pl-9 bg-surface-2 border-edge text-ink text-sm focus:border-accent"
                />
              </div>
            </div>

            <Button
              type="submit"
              disabled={loading}
              className="w-full bg-accent hover:bg-accent/90 text-white font-medium text-sm h-10 shadow-sm shadow-accent/25 transition-all mt-2"
            >
              {loading ? (
                <>
                  <Loader2 size={16} className="animate-spin mr-2" />
                  Signing in...
                </>
              ) : (
                <>
                  Sign In
                  <ArrowRight size={16} className="ml-2" />
                </>
              )}
            </Button>
          </form>

          {/* Quick Demo Helper */}
          <div className="mt-5 pt-4 border-t border-edge flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-ink">Demo Account</span>
              <p className="text-[10px] text-ink-dim">architect@buildwise.ai</p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleQuickDemo}
              disabled={loading}
              className="text-xs h-7 border-edge hover:bg-surface-2 text-ink flex items-center gap-1.5 cursor-pointer"
            >
              <Sparkles size={13} className="text-accent" />
              1-Click Demo Sign In
            </Button>
          </div>
        </Card>

        {/* Sign up prompt */}
        <p className="text-center text-xs text-ink-dim mt-6">
          Don&apos;t have an account yet?{" "}
          <Link
            href={redirectPath !== "/" ? `/signup?redirect=${encodeURIComponent(redirectPath)}` : "/signup"}
            className="text-accent hover:underline font-medium"
          >
            Create an account
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen w-full bg-surface-0 flex items-center justify-center">
          <Loader2 size={24} className="animate-spin text-accent" />
        </div>
      }
    >
      <LoginForm />
    </Suspense>
  );
}
