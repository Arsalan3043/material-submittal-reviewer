"use client";

import { use, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  getProject,
  listProjectSubmittals,
  type Project,
  type SubmittalSummary,
} from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { Button, StatusChip } from "@/components/ui";
import { useSidebarSearch } from "@/lib/hooks/use-sidebar-search";
import {
  RECOMMENDATION_LABEL,
  RECOMMENDATION_SEVERITY,
  SEVERITY_CHIP,
  SUBMITTAL_STATUS_LABEL,
  formatRelativeDate,
} from "@/lib/status";

type FilterKey = "all" | "action" | "recent";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "All" },
  { key: "action", label: "Needs action" },
  { key: "recent", label: "Last 30 days" },
];

function shortRef(id: string): string {
  return `SUB-${id.replace(/-/g, "").slice(0, 5).toUpperCase()}`;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const m = Math.floor(totalSeconds / 60);
  const s = totalSeconds % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** README Screen 4 — the submittal register. Stats and finding-summary text are computed
 * from real data already on the page (per the API reference's "don't ship invented
 * numbers"), not fetched from an endpoint that doesn't exist. */
export default function ProjectDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const router = useRouter();
  const { query } = useSidebarSearch();
  const [project, setProject] = useState<Project | null>(null);
  const [submittals, setSubmittals] = useState<SubmittalSummary[] | null>(null);
  const [filter, setFilter] = useState<FilterKey>("all");

  useEffect(() => {
    getProject(id).then(setProject);
    listProjectSubmittals(id).then(setSubmittals);
  }, [id]);

  // Captured once via useState's lazy initializer (the sanctioned one-time-impure-read
  // pattern) rather than called fresh during render/memo bodies (React purity rule: Date.now()
  // is impure) — "last 30 days" only needs to be this fresh, not live-ticking.
  const [now] = useState(() => Date.now());

  const stats = useMemo(() => {
    if (!submittals) return null;
    const completed = submittals.filter((s) => s.status === "COMPLETED");
    const needsAction = submittals.filter(
      (s) => s.recommendation === "RESUBMIT" || s.recommendation === "CONDITIONAL"
    ).length;
    const approved = completed.filter((s) => s.recommendation === "APPROVE").length;
    const firstPassRate = completed.length > 0 ? Math.round((approved / completed.length) * 100) : null;
    const durations = completed
      .filter((s) => s.completed_at)
      .map((s) => new Date(s.completed_at!).getTime() - new Date(s.created_at).getTime())
      .filter((ms) => ms > 0);
    const avgMs = durations.length > 0 ? durations.reduce((a, b) => a + b, 0) / durations.length : null;

    return [
      { label: "NEEDS ACTION", value: String(needsAction), note: "resubmit or conditional" },
      {
        label: "FIRST-PASS APPROVAL",
        value: firstPassRate === null ? "—" : `${firstPassRate}%`,
        note: `${completed.length} reviewed`,
      },
      {
        label: "AVG REVIEW TIME",
        value: avgMs === null ? "—" : formatDuration(avgMs),
        note: `${durations.length} completed`,
      },
    ];
  }, [submittals]);

  const visibleSubmittals = useMemo(() => {
    if (!submittals) return [];
    const q = query.trim().toLowerCase();
    return submittals.filter((s) => {
      if (filter === "action" && s.recommendation !== "RESUBMIT" && s.recommendation !== "CONDITIONAL") {
        return false;
      }
      if (filter === "recent") {
        const ageMs = now - new Date(s.created_at).getTime();
        if (ageMs > 30 * 24 * 60 * 60 * 1000) return false;
      }
      if (q) {
        const haystack = `${s.material_desc ?? ""} ${shortRef(s.id)}`.toLowerCase();
        if (!haystack.includes(q)) return false;
      }
      return true;
    });
  }, [submittals, filter, query, now]);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={project?.name ?? "…"}
        authority={project?.authority}
        backHref="/projects"
        right={
          <Button className="h-[34px] px-4" onClick={() => router.push(`/projects/${id}/submit`)}>
            New submittal
          </Button>
        }
      />

      <div className="flex-1 overflow-y-auto p-5.5 md:p-6">
        <div className="animate-riseIn flex flex-col gap-4">
          <div className="grid grid-cols-3 gap-2 md:gap-3">
            {(stats ?? [0, 1, 2].map(() => null)).map((s, i) => (
              <div
                key={i}
                className="flex flex-col gap-1.5 rounded-[11px] border border-line bg-panel p-2.5 md:p-4"
              >
                <div className="hidden font-mono text-[11px] font-medium tracking-[0.08em] text-text-muted md:block">
                  {s?.label ?? ""}
                </div>
                <div className="text-lg font-semibold tracking-[-0.02em] text-ink md:text-2xl">
                  {s?.value ?? "…"}
                </div>
                <div className="text-[10px] text-text-muted md:text-xs">{s?.note ?? ""}</div>
              </div>
            ))}
          </div>

          <div className="flex items-center gap-2.5">
            <span className="hidden text-[13px] font-semibold text-ink md:inline">
              Submittal register
            </span>
            <div className="flex gap-1.5 overflow-x-auto md:ml-2">
              {FILTERS.map((f) => {
                const active = filter === f.key;
                const count =
                  f.key === "all"
                    ? submittals?.length ?? 0
                    : f.key === "action"
                      ? submittals?.filter(
                          (s) => s.recommendation === "RESUBMIT" || s.recommendation === "CONDITIONAL"
                        ).length ?? 0
                      : submittals?.filter(
                          (s) => now - new Date(s.created_at).getTime() <= 30 * 24 * 60 * 60 * 1000
                        ).length ?? 0;
                return (
                  <button
                    key={f.key}
                    onClick={() => setFilter(f.key)}
                    className={`h-[26px] shrink-0 whitespace-nowrap rounded-[7px] px-2.5 text-xs md:h-[34px] md:rounded-[17px] md:px-3.5 md:text-[12.5px] ${
                      active
                        ? "bg-ink font-medium text-white"
                        : "border border-line-2 bg-panel font-normal text-text-secondary"
                    }`}
                  >
                    {f.label} {count}
                  </button>
                );
              })}
            </div>
            <span className="ml-auto hidden text-xs text-text-muted md:inline">
              Retained 10 yrs · Law No. 7 of 2025
            </span>
          </div>

          {/* Desktop table */}
          <div className="hidden overflow-hidden rounded-[11px] border border-line bg-panel md:block">
            <div className="grid grid-cols-[104px_1fr_132px_140px_96px] gap-3 border-b border-[#EDEDEA] bg-panel-2 px-4 py-2.5 font-mono text-[10px] font-semibold tracking-[0.09em] text-text-muted">
              <div>REF</div>
              <div>MATERIAL</div>
              <div>RESULT</div>
              <div>FINDINGS</div>
              <div>SUBMITTED</div>
            </div>
            <div className="max-h-[calc(100vh-320px)] overflow-y-auto">
              {submittals === null && (
                <p className="p-6 text-center text-sm text-text-faint">Loading…</p>
              )}
              {submittals?.length === 0 && (
                <p className="p-6 text-center text-sm text-text-faint">
                  No submittals yet in this project.
                </p>
              )}
              {visibleSubmittals.map((s) => (
                <button
                  key={s.id}
                  onClick={() => router.push(`/submittals/${s.id}`)}
                  className="grid w-full grid-cols-[104px_1fr_132px_140px_96px] items-center gap-3 border-b border-line-3 px-4 py-3 text-left last:border-0 hover:bg-[#FBFBFA]"
                >
                  <span className="font-mono text-xs font-medium text-text-secondary">
                    {shortRef(s.id)}
                  </span>
                  <span className="truncate text-[13px] text-ink">
                    {s.material_desc || <span className="text-text-faint">Untitled</span>}
                  </span>
                  <span>
                    {s.recommendation ? (
                      <StatusChip
                        label={RECOMMENDATION_LABEL[s.recommendation]}
                        severityClass={SEVERITY_CHIP[RECOMMENDATION_SEVERITY[s.recommendation]]}
                      />
                    ) : (
                      <span className="text-xs text-text-secondary">
                        {SUBMITTAL_STATUS_LABEL[s.status] ?? s.status}
                      </span>
                    )}
                  </span>
                  <span className="text-xs text-text-secondary">
                    {s.status === "FAILED" ? "Review failed" : "—"}
                  </span>
                  <span className="text-xs text-text-muted">{formatRelativeDate(s.created_at)}</span>
                </button>
              ))}
              {submittals && submittals.length > 0 && visibleSubmittals.length === 0 && (
                <div className="animate-fadeIn flex flex-col items-center gap-2 p-11 text-center">
                  <p className="text-sm font-semibold text-ink">
                    Nothing matches &ldquo;{query}&rdquo;
                  </p>
                  <p className="text-[12.5px] text-text-muted">
                    Try a material name, a reference, or clear the filter.
                  </p>
                </div>
              )}
            </div>
          </div>

          {/* Mobile cards */}
          <div className="flex flex-col gap-2.5 md:hidden">
            {visibleSubmittals.map((s) => (
              <button
                key={s.id}
                onClick={() => router.push(`/submittals/${s.id}`)}
                className="flex flex-col gap-2 rounded-[13px] border border-line bg-panel p-3.5 text-left"
              >
                <div className="flex items-center justify-between">
                  <span className="font-mono text-[11px] font-medium text-text-muted">
                    {shortRef(s.id)}
                  </span>
                  <span className="text-[11px] text-text-faint">{formatRelativeDate(s.created_at)}</span>
                </div>
                <div className="text-[14.5px] font-medium leading-snug text-ink">
                  {s.material_desc || "Untitled"}
                </div>
                {s.recommendation && (
                  <StatusChip
                    label={RECOMMENDATION_LABEL[s.recommendation]}
                    severityClass={SEVERITY_CHIP[RECOMMENDATION_SEVERITY[s.recommendation]]}
                    className="w-fit"
                  />
                )}
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
