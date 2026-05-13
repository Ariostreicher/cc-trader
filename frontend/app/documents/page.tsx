"use client";

import { useRef } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Upload, Trash2 } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";
import type { DocumentRecord } from "@/lib/types";

export default function DocumentsPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);

  const { data: docs } = useQuery<DocumentRecord[]>({
    queryKey: ["documents"],
    queryFn: async () => (await api.get("/documents")).data,
    refetchInterval: 4000, // ingestion status updates
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return (
        await api.post("/documents", fd, {
          headers: { "Content-Type": "multipart/form-data" },
        })
      ).data;
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/documents/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-2xl font-bold">Methodology library</h1>
        <p className="text-muted text-sm mt-1">
          Upload your Chart Champions PDFs. They are chunked, embedded into ChromaDB, and used as
          RAG context when running the model.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Upload</CardTitle>
          <CardSubtitle>PDF, TXT, or image. Max 50 MB.</CardSubtitle>
        </CardHeader>
        <CardBody>
          <input
            ref={fileRef}
            type="file"
            className="hidden"
            accept="application/pdf,text/plain,image/*"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) upload.mutate(f);
              if (fileRef.current) fileRef.current.value = "";
            }}
          />
          <Button onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
            <Upload size={14} />
            {upload.isPending ? "Uploading…" : "Select a file"}
          </Button>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your documents</CardTitle>
        </CardHeader>
        <CardBody>
          {docs && docs.length > 0 ? (
            <ul className="divide-y divide-border">
              {docs.map((d) => (
                <li key={d.id} className="py-3 flex items-center justify-between text-sm">
                  <div>
                    <div className="font-medium">{d.filename}</div>
                    <div className="text-xs text-muted">
                      {(d.size_bytes / (1024 * 1024)).toFixed(1)} MB · {d.page_count ?? "—"} pages
                      {" · "}
                      <StatusPill status={d.status} />
                    </div>
                    {d.error && <div className="text-xs text-danger mt-1">{d.error}</div>}
                  </div>
                  <button
                    onClick={() => remove.mutate(d.id)}
                    className="text-muted hover:text-danger"
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">No documents yet.</p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const tone =
    status === "ready"
      ? "text-accent"
      : status === "failed"
      ? "text-danger"
      : "text-amber-400";
  return <span className={tone}>{status}</span>;
}
