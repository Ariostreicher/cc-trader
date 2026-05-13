"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, Power } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { api } from "@/lib/api";
import type { Alert, AlertTrigger } from "@/lib/types";

const TRIGGERS: { value: AlertTrigger; label: string; paramHint: string }[] = [
  { value: "price_above", label: "Price above", paramHint: "price" },
  { value: "price_below", label: "Price below", paramHint: "price" },
  { value: "rsi_above", label: "RSI above", paramHint: "level (default 70)" },
  { value: "rsi_below", label: "RSI below", paramHint: "level (default 30)" },
  { value: "macd_cross_up", label: "MACD cross up", paramHint: "—" },
  { value: "macd_cross_down", label: "MACD cross down", paramHint: "—" },
  { value: "volume_spike", label: "Volume spike", paramHint: "multiplier (default 1.5)" },
  { value: "ema_cross_up", label: "EMA cross up", paramHint: "fast, slow" },
  { value: "ema_cross_down", label: "EMA cross down", paramHint: "fast, slow" },
];

export default function AlertsPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const qc = useQueryClient();
  const [symbol, setSymbol] = useState("");
  const [trigger, setTrigger] = useState<AlertTrigger>("price_above");
  const [param, setParam] = useState("");

  const { data: alerts } = useQuery<Alert[]>({
    queryKey: ["alerts"],
    queryFn: async () => (await api.get("/alerts")).data,
  });

  const create = useMutation({
    mutationFn: async () => {
      const params: Record<string, any> = {};
      if (param) {
        if (trigger === "price_above" || trigger === "price_below") params.price = Number(param);
        else if (trigger === "rsi_above" || trigger === "rsi_below") params.level = Number(param);
        else if (trigger === "volume_spike") params.multiplier = Number(param);
      }
      return (
        await api.post("/alerts", {
          symbol,
          trigger,
          params,
          cooldown_seconds: 300,
          channels: ["in_app"],
          is_enabled: true,
        })
      ).data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/alerts/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const toggle = useMutation({
    mutationFn: async (a: Alert) =>
      api.patch(`/alerts/${a.id}`, {
        ...a,
        is_enabled: !a.is_enabled,
        channels: a.channels,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["alerts"] }),
  });

  const currentHint = TRIGGERS.find((t) => t.value === trigger)?.paramHint || "";

  return (
    <div className="space-y-6 max-w-5xl">
      <h1 className="text-2xl font-bold">Alerts</h1>

      <Card>
        <CardHeader>
          <CardTitle>New alert</CardTitle>
          <CardSubtitle>Indicator-based or price-based.</CardSubtitle>
        </CardHeader>
        <CardBody>
          <form
            className="grid grid-cols-1 md:grid-cols-4 gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (symbol) create.mutate();
            }}
          >
            <Input
              placeholder="Symbol e.g. AAPL"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value.toUpperCase())}
              required
            />
            <select
              className="h-10 rounded-md bg-panel border border-border px-3 text-sm"
              value={trigger}
              onChange={(e) => setTrigger(e.target.value as AlertTrigger)}
            >
              {TRIGGERS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
            <Input
              placeholder={currentHint}
              value={param}
              onChange={(e) => setParam(e.target.value)}
            />
            <Button type="submit">Create</Button>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your alerts</CardTitle>
        </CardHeader>
        <CardBody>
          {alerts && alerts.length > 0 ? (
            <ul className="divide-y divide-border">
              {alerts.map((a) => (
                <li key={a.id} className="py-3 flex items-center justify-between text-sm">
                  <div>
                    <div className="font-medium">
                      {a.symbol}{" "}
                      <span className="text-muted text-xs">
                        · {a.trigger.replace(/_/g, " ")}
                      </span>
                    </div>
                    <div className="text-xs text-muted">
                      {Object.entries(a.params || {})
                        .map(([k, v]) => `${k}=${v}`)
                        .join(", ") || "no params"}
                    </div>
                  </div>
                  <div className="flex items-center gap-3">
                    <button
                      onClick={() => toggle.mutate(a)}
                      className={
                        "rounded-full h-6 px-2 text-xs " +
                        (a.is_enabled ? "bg-accent/20 text-accent" : "bg-muted/30 text-muted")
                      }
                    >
                      <Power size={10} className="inline mr-1" />
                      {a.is_enabled ? "enabled" : "off"}
                    </button>
                    <button
                      onClick={() => remove.mutate(a.id)}
                      className="text-muted hover:text-danger"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No alerts yet.</p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
