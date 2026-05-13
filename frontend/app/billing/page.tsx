"use client";

import { useState } from "react";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const TIERS = [
  {
    name: "Free",
    id: "free",
    price: "$0",
    features: ["5 reports / month", "1 watchlist", "5 alerts", "Single methodology doc"],
    priceEnv: "",
  },
  {
    name: "Pro",
    id: "pro",
    price: "$29 / mo",
    features: [
      "Unlimited reports",
      "Unlimited watchlists",
      "Unlimited alerts",
      "Methodology library",
      "Backtests",
      "Paper trading",
    ],
    priceEnv: process.env.NEXT_PUBLIC_STRIPE_PRICE_PRO || "",
  },
  {
    name: "Enterprise",
    id: "enterprise",
    price: "Contact us",
    features: ["Team seats", "Priority support", "SLA", "Custom data sources"],
    priceEnv: process.env.NEXT_PUBLIC_STRIPE_PRICE_ENTERPRISE || "",
  },
];

export default function BillingPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const { user } = useAuth();
  const [busy, setBusy] = useState<string | null>(null);

  async function subscribe(priceId: string) {
    setBusy(priceId);
    try {
      const { data } = await api.post<{ url: string }>("/billing/checkout", {
        price_id: priceId,
        success_url: `${window.location.origin}/billing?status=success`,
        cancel_url: `${window.location.origin}/billing?status=cancel`,
      });
      window.location.href = data.url;
    } catch (err: any) {
      alert(err.response?.data?.detail || "Stripe checkout unavailable.");
    } finally {
      setBusy(null);
    }
  }

  async function portal() {
    setBusy("portal");
    try {
      const { data } = await api.post<{ url: string }>("/billing/portal", {
        return_url: `${window.location.origin}/billing`,
      });
      window.location.href = data.url;
    } catch (err: any) {
      alert(err.response?.data?.detail || "Stripe portal unavailable.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Billing</h1>
          <p className="text-muted text-sm mt-1">
            Current plan: <span className="uppercase font-medium">{user?.tier}</span>
          </p>
        </div>
        {user?.tier !== "free" && (
          <Button variant="secondary" onClick={portal} disabled={busy === "portal"}>
            Manage subscription
          </Button>
        )}
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {TIERS.map((t) => (
          <Card key={t.id}>
            <CardHeader>
              <CardTitle>{t.name}</CardTitle>
              <CardSubtitle>{t.price}</CardSubtitle>
            </CardHeader>
            <CardBody>
              <ul className="text-sm space-y-1 text-foreground/80 mb-4">
                {t.features.map((f) => (
                  <li key={f}>• {f}</li>
                ))}
              </ul>
              {t.id !== "free" && t.priceEnv && (
                <Button
                  className="w-full"
                  variant={user?.tier === t.id ? "secondary" : "primary"}
                  onClick={() => subscribe(t.priceEnv)}
                  disabled={busy === t.priceEnv}
                >
                  {user?.tier === t.id ? "Current plan" : `Choose ${t.name}`}
                </Button>
              )}
              {t.id === "enterprise" && !t.priceEnv && (
                <Button variant="secondary" className="w-full" disabled>
                  Contact sales
                </Button>
              )}
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}
