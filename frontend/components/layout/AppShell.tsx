"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { Sidebar } from "./Sidebar";
import { useAuth } from "@/lib/auth";

export function AppShell({ children }: { children: ReactNode }) {
  const router = useRouter();
  const { user, fetchMe } = useAuth();

  useEffect(() => {
    if (!user) {
      fetchMe().then(() => {
        if (!useAuth.getState().user) router.replace("/login");
      });
    }
  }, [user, fetchMe, router]);

  if (!user) {
    return (
      <div className="min-h-screen grid place-items-center text-muted text-sm">Loading…</div>
    );
  }

  return (
    <div className="min-h-screen flex">
      <Sidebar />
      <main className="flex-1 p-8 overflow-y-auto">{children}</main>
    </div>
  );
}
