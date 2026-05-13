"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { api } from "@/lib/api";
import type { Watchlist } from "@/lib/types";

export default function WatchlistsPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const qc = useQueryClient();
  const [newName, setNewName] = useState("");
  const [newSymbol, setNewSymbol] = useState<Record<string, string>>({});

  const { data: lists } = useQuery<Watchlist[]>({
    queryKey: ["watchlists"],
    queryFn: async () => (await api.get("/watchlists")).data,
  });

  const createList = useMutation({
    mutationFn: async (name: string) =>
      (await api.post("/watchlists", { name, description: null, pinned: false })).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  const deleteList = useMutation({
    mutationFn: async (id: string) => api.delete(`/watchlists/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  const addAsset = useMutation({
    mutationFn: async ({ id, symbol }: { id: string; symbol: string }) =>
      (
        await api.post(`/watchlists/${id}/assets`, {
          symbol,
          asset_class: symbol.length > 5 || symbol.includes("/") ? "crypto" : "stock",
        })
      ).data,
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  const removeAsset = useMutation({
    mutationFn: async ({ wlId, id }: { wlId: string; id: string }) =>
      api.delete(`/watchlists/${wlId}/assets/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlists"] }),
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-2xl font-bold">Watchlists</h1>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Create a watchlist</CardTitle>
          <CardSubtitle>Group tickers by sector, theme, or methodology run.</CardSubtitle>
        </CardHeader>
        <CardBody>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (newName.trim()) {
                createList.mutate(newName.trim());
                setNewName("");
              }
            }}
          >
            <Input
              placeholder="e.g. 2026 Research Top Picks"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <Button type="submit">
              <Plus size={14} /> New
            </Button>
          </form>
        </CardBody>
      </Card>

      {lists?.map((wl) => (
        <Card key={wl.id}>
          <CardHeader className="flex items-center justify-between flex-row">
            <div>
              <CardTitle>{wl.name}</CardTitle>
              {wl.description && <CardSubtitle>{wl.description}</CardSubtitle>}
            </div>
            {!wl.is_public && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => deleteList.mutate(wl.id)}
              >
                <Trash2 size={14} />
              </Button>
            )}
          </CardHeader>
          <CardBody>
            <form
              className="flex gap-2 mb-3"
              onSubmit={(e) => {
                e.preventDefault();
                const symbol = (newSymbol[wl.id] || "").toUpperCase().trim();
                if (symbol) {
                  addAsset.mutate({ id: wl.id, symbol });
                  setNewSymbol((s) => ({ ...s, [wl.id]: "" }));
                }
              }}
            >
              <Input
                placeholder="Add symbol e.g. NVDA"
                value={newSymbol[wl.id] || ""}
                onChange={(e) =>
                  setNewSymbol((s) => ({ ...s, [wl.id]: e.target.value }))
                }
              />
              <Button type="submit" size="sm">
                Add
              </Button>
            </form>
            <ul className="divide-y divide-border">
              {wl.assets.length === 0 && (
                <li className="text-sm text-muted py-2">No assets yet.</li>
              )}
              {wl.assets.map((a) => (
                <li
                  key={a.id}
                  className="py-2 flex items-center justify-between text-sm"
                >
                  <Link
                    href={`/equity/${a.symbol}`}
                    className="font-medium hover:text-accent"
                  >
                    {a.symbol}
                  </Link>
                  <div className="flex items-center gap-3 text-xs text-muted">
                    <span>{a.asset_class}</span>
                    {!wl.is_public && (
                      <button
                        onClick={() => removeAsset.mutate({ wlId: wl.id, id: a.id })}
                        className="hover:text-danger"
                      >
                        <Trash2 size={12} />
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}
