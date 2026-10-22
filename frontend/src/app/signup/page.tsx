"use client";

import React, { useState, Suspense } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Building2, Lock, Mail, ArrowRight, Loader2, Sparkles, AlertCircle, CheckCircle2 } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card } from "@/components/ui/card";
import { toast } from "sonner";

function SignUpForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectPath = searchParams.get("redirect") || "/";

  const { signUp } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim() || !password) {
      setError("Please fill in all required fields.");
      return;
    }

    if (password.length < 6) {
      setError("Password must be at least 6 characters long.");
      return;
    }

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      return;
    }

    setError(null);
    setLoading(true);

    try {
      await signUp(email.trim(), password);
      toast.success("Account created successfully!");
      router.replace(redirectPath);
    } catch (err: any) {
      const msg = err.message || "Failed to create account.";
      if (msg.includes("Confirmation email sent")) {
        setSuccessMsg(msg);
      } else {
        setError(msg);
        toast.error(msg);
      }
    } finally {
      setLoading(false);
    }
  }

  async function handleQuickDemo() {
    const demoId = Math.floor(100 + Math.random() * 900);
    const demoEmail = `architect${demoId}@buildwise.ai`;
    setEmail(demoEmail);
    setPassword("DemoPassword123!");
    setConfirmPassword("DemoPassword123!");
    setError(null);
    setLoading(true);
    try {
      await signUp(demoEmail, "DemoPassword123!");
      toast.success("Demo account created!");
      router.replace(redirectPath);
    } catch (err: any) {
      const msg = err.message || "Failed to create demo account.";
      setError(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }



  return (
    <div className="min-h-screen w-full bg-surface-0 flex flex-col justify-center items-center px-4 sm:px-6 lg:px-8 relative overflow-hidden">
      {/* Background glow */}
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
          <h1 className="text-2xl font-bold tracking-tight text-ink">Create your account</h1>
          <p className="text-xs text-ink-dim mt-1.5">
            Get started with isolated project workspaces and intelligent floor plan verification.
          </p>
        </div>

        {/* Success / email verification banner */}
        {successMsg && (
          <div className="mb-4 p-4 rounded-lg bg-emerald-500/10 border border-emerald-500/20 text-emerald-300 text-xs flex items-start gap-2.5">
            <CheckCircle2 size={16} className="shrink-0 mt-0.5 text-emerald-400" />
            <div>
              <p className="font-medium">Verification required</p>
              <p className="mt-0.5 opacity-90">{successMsg}</p>
            </div>
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
              <Label htmlFor="password" className="text-xs font-medium text-ink">
                Password
              </Label>
              <div className="relative mt-1.5">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" size={16} />
                <Input
                  id="password"
                  type="password"
                  required
                  autoComplete="new-password"
                  placeholder="At least 6 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pl-9 bg-surface-2 border-edge text-ink text-sm focus:border-accent"
                />
              </div>
            </div>

            <div>
              <Label htmlFor="confirm-password" className="text-xs font-medium text-ink">
                Confirm password
              </Label>
              <div className="relative mt-1.5">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 text-ink-muted" size={16} />
                <Input
                  id="confirm-password"
                  type="password"
                  required
                  autoComplete="new-password"
                  placeholder="Confirm your password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
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
                  Creating account...
                </>
              ) : (
                <>
                  Create Account
                  <ArrowRight size={16} className="ml-2" />
                </>
              )}
            </Button>
          </form>

          {/* Quick Demo Helper */}
          <div className="mt-5 pt-4 border-t border-edge flex items-center justify-between">
            <div>
              <span className="text-[11px] font-medium text-ink">Testing Locally?</span>
              <p className="text-[10px] text-ink-dim">Instant test account</p>
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
              1-Click Demo Account
            </Button>
          </div>
        </Card>

        {/* Sign in prompt */}
        <p className="text-center text-xs text-ink-dim mt-6">
          Already have an account?{" "}
          <Link
            href={redirectPath !== "/" ? `/login?redirect=${encodeURIComponent(redirectPath)}` : "/login"}
            className="text-accent hover:underline font-medium"
          >
            Sign in
          </Link>
        </p>
      </div>
    </div>
  );
}

export default function SignUpPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen w-full bg-surface-0 flex items-center justify-center">
          <Loader2 size={24} className="animate-spin text-accent" />
        </div>
      }
    >
      <SignUpForm />
    </Suspense>
  );
}
