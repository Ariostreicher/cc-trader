"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";

export default function Home() {
  const router = useRouter();
  const { user, fetchMe } = useAuth();

  useEffect(() => {
    if (user) {
      router.replace("/dashboard");
    } else {
      fetchMe().then(() => {
        router.replace(useAuth.getState().user ? "/dashboard" : "/login");
      });
    }
  }, [user, fetchMe, router]);

  return (
    <div className="min-h-screen grid place-items-center text-muted text-sm">Loading…</div>
  );
}
