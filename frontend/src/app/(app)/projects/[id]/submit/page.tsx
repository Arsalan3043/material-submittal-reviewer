"use client";

import { use, useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { FileText } from "lucide-react";
import { createSubmittal, startSubmittal, uploadToS3, type UploadTarget } from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Button, MonoLabel, TextInput } from "@/components/ui";

interface FileRow {
  file: File;
  pct: number;
}

/**
 * README Screen 5 — one dropzone, minimal metadata, submit. Files upload directly to S3 via
 * presigned URLs (never through our API). Retry-safe: createSubmittal() is only ever called
 * once per visit — its result is cached in pendingSubmittal so a failed upload/start can be
 * retried without creating a duplicate submittal row (3 failed clicks created 3 duplicate
 * DB rows during testing before this guard existed — a real bug, not hypothetical).
 *
 * Cover-page auto-fill (the design's "AUTO-FILLED FROM COVER PAGE" badge) is not a real API
 * feature today — the material field below is a plain editable input, not faked.
 */
export default function SubmitSubmittalPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: projectId } = use(params);
  const router = useRouter();
  const [rows, setRows] = useState<FileRow[]>([]);
  const [materialDesc, setMaterialDesc] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<"idle" | "creating" | "uploading" | "starting">("idle");
  // hasPendingSubmittal mirrors pendingSubmittal.current for rendering — refs can't be read
  // during render (React purity rule), but handleSubmit still needs the ref itself since it
  // must read the value synchronously without waiting for a re-render.
  const [hasPendingSubmittal, setHasPendingSubmittal] = useState(false);
  const pendingSubmittal = useRef<{ submittalId: string; uploads: UploadTarget[] } | null>(null);
  const inFlight = useRef(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    const pdfs = Array.from(incoming).filter((f) => f.type === "application/pdf");
    setRows((prev) => [...prev, ...pdfs.map((file) => ({ file, pct: 0 }))]);
  }, []);

  function removeFile(name: string) {
    setRows((prev) => prev.filter((r) => r.file.name !== name));
  }

  const uploadDone = rows.length > 0 && rows.every((r) => r.pct >= 100);

  async function handleSubmit() {
    if (inFlight.current || rows.length === 0) return;
    inFlight.current = true;
    setError(null);
    try {
      let submittalId: string;
      let uploads: UploadTarget[];

      if (pendingSubmittal.current) {
        ({ submittalId, uploads } = pendingSubmittal.current);
      } else {
        setStage("creating");
        const created = await createSubmittal(projectId, {
          material_desc: materialDesc || undefined,
          files: rows.map((r) => ({ filename: r.file.name })),
        });
        submittalId = created.submittal_id;
        uploads = created.uploads;
        pendingSubmittal.current = { submittalId, uploads };
        setHasPendingSubmittal(true);
      }

      setStage("uploading");
      const byName = new Map(rows.map((r) => [r.file.name, r.file]));
      await Promise.all(
        uploads.map((target) => {
          const file = byName.get(target.filename);
          if (!file) throw new Error(`Missing file for ${target.filename}`);
          return uploadToS3(target, file, (pct) => {
            setRows((prev) =>
              prev.map((r) => (r.file.name === target.filename ? { ...r, pct } : r))
            );
          });
        })
      );

      setStage("starting");
      await startSubmittal(submittalId);
      router.replace(`/submittals/${submittalId}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Submission failed");
      setStage("idle");
    } finally {
      inFlight.current = false;
    }
  }

  const busy = stage !== "idle";
  const startLabel =
    rows.length === 0
      ? "Add files to continue"
      : stage === "creating"
        ? "Preparing…"
        : stage === "uploading"
          ? `Uploading ${rows.length} file${rows.length > 1 ? "s" : ""}…`
          : stage === "starting"
            ? "Starting review…"
            : uploadDone
              ? "Start review · ~4 min"
              : hasPendingSubmittal
                ? "Retry"
                : "Start review · ~4 min";

  return (
    <div className="flex h-full flex-col">
      <PageHeader title="New submittal" backHref={`/projects/${projectId}`} />

      <div className="flex-1 overflow-y-auto p-5 md:flex md:justify-center md:p-6.5">
        <div className="animate-riseIn flex w-full flex-col gap-3.5 md:w-[660px]">
          <div>
            <h1 className="text-lg font-semibold tracking-[-0.015em] text-ink md:text-xl">
              New submittal
            </h1>
            <p className="mt-1.5 text-[13px] leading-relaxed text-[#6E7175]">
              Drop the PDFs. Project and authority are already known from this project.
            </p>
          </div>

          <div
            onDragOver={(e) => {
              e.preventDefault();
              setDragOver(true);
            }}
            onDragLeave={() => setDragOver(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragOver(false);
              addFiles(e.dataTransfer.files);
            }}
            onClick={() => fileInputRef.current?.click()}
            className={`flex cursor-pointer flex-col items-center gap-2.5 rounded-[13px] border-[1.5px] border-dashed p-6.5 text-center transition-colors ${
              dragOver ? "border-accent" : "border-[#C3C9DE]"
            }`}
            style={{ background: "linear-gradient(#F4F6FF,#FBFCFF)" }}
          >
            <div className="animate-softPulse h-9 w-9 rounded-[11px] bg-accent shadow-[0_4px_12px_rgba(27,77,255,.28)]" />
            <div className="text-[15px] font-semibold text-ink">
              {rows.length === 0
                ? "Drop the submittal PDFs here"
                : uploadDone
                  ? "All files uploaded"
                  : stage === "uploading"
                    ? "Uploading…"
                    : `${rows.length} file${rows.length > 1 ? "s" : ""} ready`}
            </div>
            <div className="text-xs text-[#7C7F84]">
              or click to browse · PDF only · up to 20 files
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="application/pdf"
              multiple
              className="hidden"
              onChange={(e) => addFiles(e.target.files)}
            />
          </div>

          {rows.map((r) => (
            <div
              key={r.file.name}
              className="animate-slideUp flex items-center gap-3 rounded-[10px] border border-line bg-panel p-3"
            >
              <div className="flex h-8 w-[26px] shrink-0 items-center justify-center rounded border border-[#E2E2DF] bg-[#F1F1EE] text-text-muted">
                <FileText size={13} strokeWidth={2} />
              </div>
              <div className="min-w-0 flex-1">
                <div className="truncate text-[13px] font-medium text-ink">{r.file.name}</div>
                <div className="text-[11px] text-text-muted">
                  {(r.file.size / (1024 * 1024)).toFixed(1)} MB ·{" "}
                  {r.pct >= 100 ? "uploaded" : r.pct > 0 ? "uploading to encrypted storage" : "ready to upload"}
                </div>
                {r.pct > 0 && r.pct < 100 && (
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[#EDEDEA]">
                    <div
                      className="h-full rounded-full bg-accent transition-[width] duration-300"
                      style={{ width: `${r.pct}%` }}
                    />
                  </div>
                )}
              </div>
              {r.pct > 0 && r.pct < 100 && (
                <span className="font-mono text-[11px] font-medium text-text-muted">
                  {r.pct}%
                </span>
              )}
              {r.pct >= 100 && (
                <span className="animate-popIn font-mono text-[11px] font-medium text-pass">
                  UPLOADED
                </span>
              )}
              {!busy && (
                <button
                  onClick={() => removeFile(r.file.name)}
                  className="shrink-0 text-xs text-text-faint hover:text-critical-text"
                >
                  Remove
                </button>
              )}
            </div>
          ))}

          {rows.length > 0 && (
            <div className="animate-slideUp flex flex-col gap-1.5 rounded-[10px] border border-line bg-panel p-3.5">
              <MonoLabel>MATERIAL DESCRIPTION</MonoLabel>
              <TextInput
                value={materialDesc}
                onChange={(e) => setMaterialDesc(e.target.value)}
                disabled={busy || hasPendingSubmittal}
                placeholder="e.g. HDPE PN16 DN400 pipe — gravity sewer, Zone 4"
                className="h-10 rounded-[9px] border-[#E6E6E3] bg-[#FCFCFB]"
              />
              <p className="text-[11.5px] text-text-faint">
                Optional — helps identify this submittal in the register.
              </p>
            </div>
          )}

          {error && (
            <div className="animate-popIn rounded-[9px] bg-critical-bg px-3 py-2.5 text-[12.5px] text-critical-text">
              {error} {hasPendingSubmittal && "Retrying won't create a duplicate."}
            </div>
          )}

          <Button onClick={handleSubmit} disabled={busy || rows.length === 0} className="h-11 w-full">
            {startLabel}
          </Button>

          <div className="flex items-center gap-2.5 rounded-[10px] bg-panel-2 px-3.5 py-3">
            <span className="h-[7px] w-[7px] shrink-0 rounded-full bg-pass" />
            <p className="text-[12.5px] leading-relaxed text-[#6E7175]">
              Files go straight to encrypted storage — never through our API — and the review
              runs in the background whether or not you stay on this page.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
