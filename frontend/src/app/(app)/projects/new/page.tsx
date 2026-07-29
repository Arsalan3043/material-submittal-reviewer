"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { createProject, attachSpec, listSpecs, type SpecDocument } from "@/lib/api";

/**
 * Screen 3 (planning/07_ui_ux_spec.md): minimum fields to unlock value. Authority is a
 * single choice (ADM/TAQA only, per the ADM/TAQA-only launch scope) rather than the spec's
 * literal "multi-select authorities" wording — a project has one authority
 * (projects.authority, migration 001); what's actually multi-select here is which spec
 * networks (irrigation/road/storm_water) under that authority apply, via project_specs.
 */
export default function NewProjectPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [authority, setAuthority] = useState<"ADM" | "TAQA">("ADM");
  const [description, setDescription] = useState("");
  const [specs, setSpecs] = useState<SpecDocument[]>([]);
  const [selectedSpecIds, setSelectedSpecIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    listSpecs().then(setSpecs).catch(() => setSpecs([]));
  }, []);

  const availableSpecs = specs.filter((s) => s.authority === authority);

  function toggleSpec(id: string) {
    setSelectedSpecIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      const project = await createProject({
        name,
        authority,
        description: description || undefined,
      });
      await Promise.all(
        [...selectedSpecIds].map((specId) => attachSpec(project.id, specId))
      );
      router.replace(`/projects/${project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create project");
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-xl p-8">
      <h1 className="mb-6 text-lg font-semibold text-zinc-900">New Project</h1>

      <form onSubmit={handleSubmit} className="space-y-5">
        <div>
          <label className="mb-1 block text-sm font-medium text-zinc-700">
            Project name
          </label>
          <input
            required
            autoFocus
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Reem Hills Package 3"
            className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-zinc-500"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-zinc-700">Authority</label>
          <div className="flex gap-3">
            {(["ADM", "TAQA"] as const).map((a) => (
              <button
                type="button"
                key={a}
                onClick={() => {
                  setAuthority(a);
                  setSelectedSpecIds(new Set());
                }}
                className={`rounded-md border px-4 py-2 text-sm font-medium ${
                  authority === a
                    ? "border-zinc-900 bg-zinc-900 text-white"
                    : "border-zinc-300 text-zinc-700 hover:bg-zinc-50"
                }`}
              >
                {a}
              </button>
            ))}
          </div>
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-zinc-700">
            Spec libraries{" "}
            <span className="font-normal text-zinc-400">(optional, can add later)</span>
          </label>
          {availableSpecs.length === 0 ? (
            <p className="text-sm text-zinc-400">No {authority} specs indexed yet.</p>
          ) : (
            <div className="space-y-1 rounded-md border border-zinc-200 p-2">
              {availableSpecs.map((s) => (
                <label
                  key={s.id}
                  className="flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-zinc-50"
                >
                  <input
                    type="checkbox"
                    checked={selectedSpecIds.has(s.id)}
                    onChange={() => toggleSpec(s.id)}
                  />
                  <span className="capitalize">{s.network_name.replace("_", " ")}</span>
                  {s.chunk_count != null && (
                    <span className="text-xs text-zinc-400">
                      ({s.chunk_count} chunks indexed)
                    </span>
                  )}
                </label>
              ))}
            </div>
          )}
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium text-zinc-700">
            Description <span className="font-normal text-zinc-400">(optional)</span>
          </label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={2}
            className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-zinc-500"
          />
        </div>

        {error && (
          <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">{error}</p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
        >
          {submitting ? "Creating..." : "Create project"}
        </button>
      </form>
    </div>
  );
}
