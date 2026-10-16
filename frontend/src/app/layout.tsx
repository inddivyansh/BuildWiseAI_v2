import type { Metadata } from "next";
import "./globals.css";
import { Providers } from "./providers";
import { Toaster } from "@/components/ui/sonner";

export const metadata: Metadata = {
  title: "BuildwiseAI — Unified AEC Floor Plan Generation & Compliance",
  description:
    "AI floor plan generation, visual spatial editing, and automated building code compliance verification.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body suppressHydrationWarning className="font-sans antialiased bg-background text-foreground">
        <Providers>
          {children}
        </Providers>
        <Toaster richColors position="bottom-right" />
      </body>
    </html>
  );
}
