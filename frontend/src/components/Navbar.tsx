"use client";

import React, { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Building2,
  FileCheck2,
  Layers,
  FolderKanban,
  Plus,
  Compass,
  User,
  LogOut,
  ChevronDown,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { GenerateDialog } from "@/components/editor/GenerateDialog";
import { useAuth } from "@/context/AuthContext";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { toast } from "sonner";

export function Navbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, signOut } = useAuth();
  const [generateOpen, setGenerateOpen] = useState(false);

  const navItems = [
    { label: "Dashboard", href: "/", icon: Compass },
    { label: "Projects", href: "/projects", icon: FolderKanban },
    { label: "Compliance Audits", href: "/projects?tab=compliance", icon: FileCheck2 },
    { label: "Blueprint Studio", href: "/editor/new", icon: Layers },
  ];

  async function handleSignOut() {
    try {
      await signOut();
      toast.success("Signed out successfully");
      router.push("/login");
    } catch {
      toast.error("Failed to sign out");
    }
  }

  const userInitial = user?.email ? user.email[0].toUpperCase() : "U";

  return (
    <>
      <header className="sticky top-0 z-30 border-b border-edge bg-surface-1/90 backdrop-blur-md">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
          {/* Logo & Brand */}
          <div className="flex items-center gap-8">
            <Link href="/" className="flex items-center gap-3 group">
              <div className="w-9 h-9 rounded-lg bg-accent/20 border border-accent/40 flex items-center justify-center text-accent group-hover:bg-accent group-hover:text-white transition-all">
                <Building2 size={20} />
              </div>
              <div>
                <span className="font-semibold text-base tracking-tight text-ink flex items-center gap-1.5">
                  BuildwiseAI
                  <span className="text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-surface-2 text-ink-dim border border-edge">
                    v0.2
                  </span>
                </span>
                <p className="text-[11px] text-ink-dim leading-none">
                  AEC Generation + Compliance
                </p>
              </div>
            </Link>

            {/* Desktop Navigation */}
            <nav className="hidden md:flex items-center gap-1">
              {navItems.map((item) => {
                const Icon = item.icon;
                const active =
                  pathname === item.href ||
                  (item.href !== "/" && pathname.startsWith(item.href));
                return (
                  <Link
                    key={item.label}
                    href={item.href}
                    className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                      active
                        ? "bg-surface-2 text-ink border border-edge"
                        : "text-ink-dim hover:text-ink hover:bg-surface-2/60"
                    }`}
                  >
                    <Icon size={15} />
                    {item.label}
                  </Link>
                );
              })}
            </nav>
          </div>

          {/* Quick Action & User Profile */}
          <div className="flex items-center gap-3">
            <Button
              size="sm"
              onClick={() => setGenerateOpen(true)}
              className="bg-accent hover:bg-accent/90 text-white font-medium text-xs shadow-sm shadow-accent/20"
            >
              <Plus size={15} className="mr-1.5" />
              Generate Blueprint
            </Button>

            {user ? (
              <DropdownMenu>
                <DropdownMenuTrigger
                  render={
                    <button className="flex items-center gap-2 pl-2 pr-2.5 py-1 rounded-full bg-surface-2 hover:bg-surface-2/80 border border-edge text-ink text-xs font-medium transition-colors outline-none cursor-pointer">
                      <div className="w-6 h-6 rounded-full bg-accent/20 border border-accent/40 flex items-center justify-center text-accent text-[11px] font-semibold">
                        {userInitial}
                      </div>
                      <span className="hidden sm:inline-block max-w-[120px] truncate">
                        {user.email.split("@")[0]}
                      </span>
                      <ChevronDown size={13} className="text-ink-muted" />
                    </button>
                  }
                />
                <DropdownMenuContent align="end" className="w-56 p-1.5 bg-surface-1 border border-edge rounded-lg shadow-xl text-ink">
                  <DropdownMenuLabel className="px-2 py-1.5 text-xs">
                    <p className="font-semibold text-ink truncate">{user.email}</p>
                    <p className="text-[10px] text-ink-dim font-normal">Tenant Owner</p>
                  </DropdownMenuLabel>
                  <DropdownMenuSeparator className="my-1 bg-edge" />
                  <DropdownMenuItem
                    className="flex items-center gap-2 px-2 py-1.5 text-xs rounded hover:bg-surface-2 cursor-pointer"
                    onClick={() => router.push("/projects")}
                  >
                    <FolderKanban size={14} className="text-ink-dim" />
                    My Projects
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="flex items-center gap-2 px-2 py-1.5 text-xs rounded hover:bg-surface-2 cursor-pointer"
                    onClick={() => router.push("/projects?tab=compliance")}
                  >
                    <FileCheck2 size={14} className="text-ink-dim" />
                    Compliance Workspace
                  </DropdownMenuItem>
                  <DropdownMenuSeparator className="my-1 bg-edge" />
                  <DropdownMenuItem
                    className="flex items-center gap-2 px-2 py-1.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 rounded cursor-pointer transition-colors"
                    onClick={handleSignOut}
                  >
                    <LogOut size={14} />
                    Sign Out
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            ) : (
              <Button
                variant="outline"
                size="sm"
                onClick={() => router.push("/login")}
                className="text-xs border-edge hover:bg-surface-2 text-ink flex items-center gap-1.5"
              >
                <User size={14} />
                Sign In
              </Button>
            )}
          </div>
        </div>
      </header>

      <GenerateDialog open={generateOpen} onOpenChange={setGenerateOpen} />
    </>
  );
}
