"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";

interface AdminUser {
  id: string;
  email: string;
  full_name: string | null;
  role: "user" | "admin";
  is_active: boolean;
  is_verified: boolean;
  tier: string;
  created_at: string;
}

interface Health {
  users_total: number;
  users_active: number;
  paying_users: number;
  equity_reports_total: number;
  ai_calls_today: number;
  ai_cost_usd_today: number;
}

export default function AdminPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const qc = useQueryClient();

  const health = useQuery<Health>({
    queryKey: ["admin", "health"],
    queryFn: async () => (await api.get("/admin/health")).data,
  });

  const users = useQuery<AdminUser[]>({
    queryKey: ["admin", "users"],
    queryFn: async () => (await api.get("/admin/users")).data,
  });

  const disable = useMutation({
    mutationFn: async (id: string) => api.patch(`/admin/users/${id}/disable`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });

  const enable = useMutation({
    mutationFn: async (id: string) => api.patch(`/admin/users/${id}/enable`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["admin", "users"] }),
  });

  return (
    <div className="space-y-6 max-w-6xl">
      <h1 className="text-2xl font-bold">Admin</h1>

      {health.data && (
        <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
          <Stat label="Users" value={health.data.users_total} />
          <Stat label="Active" value={health.data.users_active} />
          <Stat label="Paying" value={health.data.paying_users} />
          <Stat label="Reports" value={health.data.equity_reports_total} />
          <Stat label="AI calls (today)" value={health.data.ai_calls_today} />
          <Stat label="AI $ (today)" value={`$${health.data.ai_cost_usd_today.toFixed(2)}`} />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Users</CardTitle>
        </CardHeader>
        <CardBody>
          {users.data && (
            <table className="w-full text-sm">
              <thead className="text-muted text-xs uppercase">
                <tr>
                  <th className="text-left py-2">Email</th>
                  <th className="text-left">Role</th>
                  <th className="text-left">Plan</th>
                  <th className="text-left">Active</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {users.data.map((u) => (
                  <tr key={u.id} className="border-t border-border">
                    <td className="py-2">{u.email}</td>
                    <td>{u.role}</td>
                    <td>{u.tier}</td>
                    <td>{u.is_active ? "yes" : "no"}</td>
                    <td className="text-right">
                      {u.is_active ? (
                        <Button
                          variant="danger"
                          size="sm"
                          onClick={() => disable.mutate(u.id)}
                        >
                          Disable
                        </Button>
                      ) : (
                        <Button size="sm" onClick={() => enable.mutate(u.id)}>
                          Enable
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Card>
      <div className="p-4">
        <div className="text-xs text-muted uppercase">{label}</div>
        <div className="text-2xl font-semibold mt-1">{value}</div>
      </div>
    </Card>
  );
}
