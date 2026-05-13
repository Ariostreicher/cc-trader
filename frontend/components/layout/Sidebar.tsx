"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  TrendingUp,
  Target,
  ListTree,
  Bell,
  FileText,
  CreditCard,
  Shield,
  LogOut,
} from "lucide-react";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/cn";

const items = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/setups", label: "Live Setups", icon: Target },
  { href: "/equity", label: "Equity Model", icon: TrendingUp },
  { href: "/watchlists", label: "Watchlists", icon: ListTree },
  { href: "/alerts", label: "Alerts", icon: Bell },
  { href: "/documents", label: "Methodology", icon: FileText },
  { href: "/billing", label: "Billing", icon: CreditCard },
];

export function Sidebar() {
  const path = usePathname();
  const { user, logout } = useAuth();

  return (
    <aside className="w-60 shrink-0 border-r border-border bg-panel/40 p-4 flex flex-col">
      <div className="flex items-center gap-2 mb-8">
        <div className="h-8 w-8 rounded bg-accent/20 grid place-items-center text-accent font-bold">
          CC
        </div>
        <div>
          <div className="text-sm font-semibold">CC Trader</div>
          <div className="text-[10px] text-muted">Chart-Champions-driven</div>
        </div>
      </div>

      <nav className="flex-1 flex flex-col gap-1">
        {items.map(({ href, label, icon: Icon }) => {
          const active = path === href || path.startsWith(href + "/");
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors",
                active ? "bg-accent/10 text-accent" : "text-foreground/80 hover:bg-muted/30"
              )}
            >
              <Icon size={16} />
              {label}
            </Link>
          );
        })}
        {user?.role === "admin" && (
          <Link
            href="/admin"
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm",
              path.startsWith("/admin")
                ? "bg-accent/10 text-accent"
                : "text-foreground/80 hover:bg-muted/30"
            )}
          >
            <Shield size={16} />
            Admin
          </Link>
        )}
      </nav>

      {user && (
        <div className="border-t border-border pt-4 mt-4 text-sm">
          <div className="px-3 py-2">
            <div className="font-medium truncate">{user.email}</div>
            <div className="text-[10px] uppercase text-muted">{user.tier} plan</div>
          </div>
          <button
            onClick={() => logout()}
            className="flex items-center gap-3 w-full rounded-md px-3 py-2 text-sm text-foreground/80 hover:bg-muted/30"
          >
            <LogOut size={16} />
            Sign out
          </button>
        </div>
      )}
    </aside>
  );
}
