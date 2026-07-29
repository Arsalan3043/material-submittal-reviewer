"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import {
  getProject,
  listProjectSubmittals,
  type Project,
  type SubmittalSummary,
} from "@/lib/api";

const STATUS_BADGE: Record<string, string> = {
  CREATED: "bg-zinc-100 text-zinc-600",
  QUEUED: "bg-blue-50 text-blue-700",
  RUNNING: "bg-blue-50 text-blue-700",
  COMPLETED: "bg-zinc-100 text-zinc-600",
  FAILED: "bg-red-50 text-red-700",
  CANCELLED: "bg-zinc-100 text-zinc-500",
};

const RECOMMENDATION_BADGE: Record<string, string> = {
  APPROVE: "bg-green-50 text-green-700",
  CONDITIONAL: "bg-amber-50 text-amber-700",
  RESUBMIT: "bg-red-50 text-red-700",
};

/** Screen 4: project home — "Submit New Submittal" + the submittal-log-style history table. */
export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [project, setProject] = useState<Project | null>(null);
  const [submittals, setSubmittals] = useState<SubmittalSummary[] | null>(null);

  useEffect(() => {
    getProject(id).then(setProject);
    listProjectSubmittals(id).then(setSubmittals);
  }, [id]);

  return (
    <div className="p-8">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-zinc-900">
            {project?.name ?? "..."}
          </h1>
          {project && (
            <span className="mt-1 inline-block rounded bg-zinc-100 px-2 py-0.5 text-xs text-zinc-600">
              {project.authority}
            </span>
          )}
        </div>
        <Link href={`/projects/${id}/submit`}>
          <button className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800">
            Submit New Submittal
          </button>
        </Link>
      </div>

      <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
        <table className="w-full text-sm">
          <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase text-zinc-500">
            <tr>
              <th className="px-4 py-2 font-medium">Material</th>
              <th className="px-4 py-2 font-medium">Date</th>
              <th className="px-4 py-2 font-medium">Status</th>
              <th className="px-4 py-2 font-medium">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {submittals === null && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-zinc-400">
                  Loading...
                </td>
              </tr>
            )}
            {submittals?.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-6 text-center text-zinc-400">
                  No submittals yet in this project.
                </td>
              </tr>
            )}
            {submittals?.map((s) => (
              <tr
                key={s.id}
                className="cursor-pointer border-b border-zinc-100 last:border-0 hover:bg-zinc-50"
                onClick={() => (window.location.href = `/submittals/${s.id}`)}
              >
                <td className="px-4 py-3 text-zinc-900">
                  {s.material_desc || <span className="text-zinc-400">Untitled</span>}
                </td>
                <td className="px-4 py-3 text-zinc-500">
                  {new Date(s.created_at).toLocaleDateString()}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${
                      STATUS_BADGE[s.status] ?? "bg-zinc-100 text-zinc-600"
                    }`}
                  >
                    {s.status}
                  </span>
                </td>
                <td className="px-4 py-3">
                  {s.recommendation ? (
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        RECOMMENDATION_BADGE[s.recommendation] ?? "bg-zinc-100 text-zinc-600"
                      }`}
                    >
                      {s.recommendation}
                    </span>
                  ) : (
                    <span className="text-zinc-300">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
