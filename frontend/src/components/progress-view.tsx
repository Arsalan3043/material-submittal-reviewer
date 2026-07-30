"use client";

import { useEffect, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Check } from "lucide-react";
import type { SubmittalDetail, SubmittalEvent } from "@/lib/api";
import { resolveStages } from "@/lib/stages";
import { Button } from "@/components/ui";
import { useRunningReview } from "@/lib/hooks/use-running-review";

/** README Screen 6 — determinate, weighted 11-stage progress. Never a spinner. */
export function ProgressView({
  submittal,
  authority,
  events,
  streamError,
}: {
  submittal: SubmittalDetail;
  authority: string | undefined;
  events: SubmittalEvent[];
  streamError: boolean;
}) {
  const router = useRouter();
  const { setRunningReview } = useRunningReview();
  const stages = useMemo(() => resolveStages(authority), [authority]);
  const doneNodes = new Set(events.map((e) => e.node_name));

  const completedWeight = stages.reduce((sum, s) => sum + (doneNodes.has(s.node) ? s.weight : 0), 0);
  const running = submittal.status === "QUEUED" || submittal.status === "RUNNING";
  const percent = Math.max(completedWeight, running ? 4 : 0);
  const activeIndex = stages.findIndex((s) => !doneNodes.has(s.node));
  const reviewDone = submittal.status === "COMPLETED";

  useEffect(() => {
    if (running) {
      setRunningReview({ submittalId: submittal.id, percent, projectId: submittal.project_id });
    } else {
      setRunningReview(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [running, percent, submittal.id, submittal.project_id]);

  useEffect(() => {
    return () => setRunningReview(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const currentStageLabel = reviewDone
    ? "Review complete"
    : submittal.status === "QUEUED"
      ? "Queued"
      : stages[Math.max(activeIndex, 0)]?.label ?? "Queued";

  return (
    <div className="flex-1 overflow-y-auto p-6 md:flex md:justify-center">
      <div className="animate-riseIn flex w-full flex-col gap-4 md:w-[700px]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold tracking-[-0.015em] text-ink md:text-xl">
              {reviewDone ? "Review complete" : "Reviewing your submittal"}
            </h1>
            <p className="mt-1.5 text-[13px] leading-relaxed text-[#6E7175]">
              {reviewDone
                ? "Eleven stages finished. The findings report is ready with clause citations on every row."
                : "Eleven pipeline stages, weighted by how long they actually take. Nothing here blocks you."}
            </p>
          </div>
        </div>

        {streamError && (
          <p className="rounded-[9px] bg-warning-bg px-3 py-2 text-xs text-warning-text">
            Live updates disconnected — the review is still running on the server. Refresh to
            check its current status.
          </p>
        )}

        <div className="flex flex-col gap-3 rounded-xl border border-line bg-panel p-4.5">
          <div className="flex items-baseline justify-between">
            <span className="text-[13px] font-semibold text-ink">{currentStageLabel}</span>
            <span className="font-mono text-xs font-medium text-text-muted">
              {percent}% {reviewDone ? "· finished" : "· ~4 min remaining"}
            </span>
          </div>
          <div className="relative h-2 overflow-hidden rounded-full bg-[#EDEDEA]">
            <div
              className={`h-full rounded-full transition-[width] duration-500 ${
                reviewDone ? "bg-pass" : "bg-accent"
              }`}
              style={{ width: `${percent}%` }}
            />
            {running && (
              <div
                className="animate-sweep absolute inset-0 w-[22%]"
                style={{
                  background:
                    "linear-gradient(90deg,transparent,rgba(255,255,255,.8),transparent)",
                }}
              />
            )}
          </div>

          <div className="flex flex-col">
            {stages.map((s, i) => {
              const done = doneNodes.has(s.node);
              const isActive = running && i === activeIndex;
              return (
                <div key={s.node} className="flex items-center gap-2.5 px-1 py-[7px]">
                  {done ? (
                    <span className="animate-popIn flex h-[17px] w-[17px] shrink-0 items-center justify-center rounded-full bg-pass text-white">
                      <Check size={10} strokeWidth={3} />
                    </span>
                  ) : isActive ? (
                    <span className="animate-softPulse h-[17px] w-[17px] shrink-0 rounded-full border-2 border-accent" />
                  ) : (
                    <span className="h-[17px] w-[17px] shrink-0 rounded-full border-[1.5px] border-[#DCDCD8]" />
                  )}
                  <span
                    className={`flex-1 text-[12.5px] leading-snug ${
                      done ? "text-ink-2" : isActive ? "font-semibold text-ink" : "text-text-faint"
                    }`}
                  >
                    {s.label}
                  </span>
                  <span className="hidden font-mono text-[11px] text-[#B0B0B5] md:inline">
                    {s.node}
                  </span>
                  <span className="w-[38px] text-right font-mono text-[11px] text-[#C9C9CE]">
                    {s.weight ? `${s.weight}%` : "—"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex gap-2.5">
          <Button
            variant="ghost"
            className="h-10 px-4"
            onClick={() => router.push(`/projects/${submittal.project_id}`)}
          >
            Leave and keep it running
          </Button>
          {reviewDone && (
            <Button
              className="animate-popIn h-10 px-4"
              onClick={() => router.push(`/submittals/${submittal.id}`)}
            >
              Open findings report
            </Button>
          )}
        </div>

        {activeIndex === 0 && running && (
          <div className="rounded-[10px] bg-panel-2 px-3.5 py-3 text-[12.5px] leading-relaxed text-[#6E7175]">
            Stage 1 reads and chunks every page — on a 60-page datasheet it holds about 84% of
            the total wait. The remaining ten stages finish in seconds.
          </div>
        )}
      </div>
    </div>
  );
}
