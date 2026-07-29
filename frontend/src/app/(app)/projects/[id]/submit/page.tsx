"use client";

import { use, useCallback, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { createSubmittal, startSubmittal, uploadToS3, type UploadTarget } from "@/lib/api";

/**
 * Screen 5: keep it to almost nothing — one drop zone, minimal metadata, submit. Files
 * upload directly to S3 via presigned URLs (never through our API — see apps/api/s3.py),
 * matching the plan's upload flow exactly.
 *
 * Retry-safety: createSubmittal() is only ever called ONCE per visit to this page — its
 * result (submittal_id + upload targets) is cached in `pendingSubmittal`. If upload or
 * start fails and the user clicks Submit again, it resumes the SAME submittal instead of
 * creating a new one. Without this, 3 failed clicks created 3 separate DB rows during
 * testing — a real duplicate-submission bug caught by clicking through it for real, not a
 * hypothetical.
 */
export default function SubmitSubmittalPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id: projectId } = use(params);
  const router = useRouter();
  const [files, setFiles] = useState<File[]>([]);
  const [materialDesc, setMaterialDesc] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stage, setStage] = useState<"idle" | "creating" | "uploading" | "starting">("idle");
  const pendingSubmittal = useRef<{ submittalId: string; uploads: UploadTarget[] } | null>(
    null
  );
  const inFlight = useRef(false); // guards against a double-click landing before setState re-renders

  const addFiles = useCallback((incoming: FileList | null) => {
    if (!incoming) return;
    const pdfs = Array.from(incoming).filter((f) => f.type === "application/pdf");
    setFiles((prev) => [...prev, ...pdfs]);
  }, []);

  function removeFile(name: string) {
    setFiles((prev) => prev.filter((f) => f.name !== name));
  }

  async function handleSubmit() {
    if (inFlight.current) return;
    if (files.length === 0) {
      setError("Add at least one PDF.");
      return;
    }
    inFlight.current = true;
    setError(null);
    try {
      let submittalId: string;
      let uploads: UploadTarget[];

      if (pendingSubmittal.current) {
        // Resuming a previously-failed attempt — do NOT create a new submittal.
        ({ submittalId, uploads } = pendingSubmittal.current);
      } else {
        setStage("creating");
        const created = await createSubmittal(projectId, {
          material_desc: materialDesc || undefined,
          files: files.map((f) => ({ filename: f.name })),
        });
        submittalId = created.submittal_id;
        uploads = created.uploads;
        pendingSubmittal.current = { submittalId, uploads };
      }

      setStage("uploading");
      const byName = new Map(files.map((f) => [f.name, f]));
      await Promise.all(
        uploads.map((target) => {
          const file = byName.get(target.filename);
          if (!file) throw new Error(`Missing file for ${target.filename}`);
          return uploadToS3(target, file);
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

  return (
    <div className="mx-auto max-w-2xl p-8">
      <h1 className="mb-6 text-lg font-semibold text-zinc-900">Submit New Submittal</h1>

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
        className={`flex flex-col items-center justify-center rounded-lg border-2 border-dashed p-10 text-center transition-colors ${
          dragOver ? "border-zinc-900 bg-zinc-50" : "border-zinc-300"
        }`}
      >
        <p className="mb-2 text-sm text-zinc-600">
          Drag and drop PDF files here, or
        </p>
        <label className="cursor-pointer rounded-md border border-zinc-300 px-3 py-1.5 text-sm font-medium text-zinc-700 hover:bg-zinc-50">
          Browse files
          <input
            type="file"
            accept="application/pdf"
            multiple
            className="hidden"
            onChange={(e) => addFiles(e.target.files)}
          />
        </label>
        <p className="mt-2 text-xs text-zinc-400">
          Scanned PDFs are fine — OCR handles them.
        </p>
      </div>

      {files.length > 0 && (
        <ul className="mt-4 space-y-1">
          {files.map((f) => (
            <li
              key={f.name}
              className="flex items-center justify-between rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm"
            >
              <span className="truncate text-zinc-700">{f.name}</span>
              <button
                onClick={() => removeFile(f.name)}
                className="ml-3 shrink-0 text-xs text-zinc-400 hover:text-red-600"
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-6">
        <label className="mb-1 block text-sm font-medium text-zinc-700">
          Material description{" "}
          <span className="font-normal text-zinc-400">(optional — confirm/correct later)</span>
        </label>
        <input
          value={materialDesc}
          onChange={(e) => setMaterialDesc(e.target.value)}
          placeholder="e.g. HDPE PN16 DN400 pipe"
          className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-zinc-500"
        />
      </div>

      {error && (
        <p className="mt-4 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {error} {pendingSubmittal.current && "Click retry to resume — this won't create a duplicate."}
        </p>
      )}

      <button
        onClick={handleSubmit}
        disabled={busy || files.length === 0}
        className="mt-6 w-full rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
      >
        {stage === "idle" && (pendingSubmittal.current ? "Retry" : "Submit")}
        {stage === "creating" && "Preparing upload..."}
        {stage === "uploading" && `Uploading ${files.length} file(s)...`}
        {stage === "starting" && "Starting review..."}
      </button>
    </div>
  );
}
