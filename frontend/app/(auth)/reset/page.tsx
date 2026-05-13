"use client";

import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";

export default function ResetPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await api.post("/auth/password/forgot", { email });
      setSent(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="min-h-screen grid place-items-center px-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Reset your password</CardTitle>
          <CardSubtitle>We'll email you a link if the account exists.</CardSubtitle>
        </CardHeader>
        <CardBody>
          {sent ? (
            <p className="text-sm text-muted">
              If an account exists for <strong className="text-foreground">{email}</strong>, a
              password reset link is on its way.
            </p>
          ) : (
            <form onSubmit={onSubmit} className="space-y-4">
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="you@example.com"
              />
              <Button type="submit" disabled={busy} className="w-full">
                {busy ? "Sending…" : "Send reset link"}
              </Button>
            </form>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
