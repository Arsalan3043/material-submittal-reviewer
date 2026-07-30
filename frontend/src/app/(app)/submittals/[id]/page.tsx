"use client";

import { use, useEffect, useState } from "react";
import {
  getProject,
  getSubmittal,
  getSubmittalCitations,
  subscribeToSubmittalProgress,
  type Project,
  type RequirementCitation,
  type SubmittalDetail,
  type SubmittalEvent,
} from "@/lib/api";
import { PageHeader } from "@/components/page-header";
import { ProgressView } from "@/components/progress-view";
import { ReportView } from "@/components/report-view";
import { Button } from "@/components/ui";

/**
 * Routes between the progress view and the findings report based on real submittal.status.
 * Live via SSE (apps/api/routers/submittals.py::stream_submittal_progress) instead of
 * client polling; onDone re-fetches once since the stream carries status + events, not the
 * full report payload.
 */
export default function SubmittalDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [submittal, setSubmittal] = useState<SubmittalDetail | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [events, setEvents] = useState<SubmittalEvent[]>([]);
  const [citations, setCitations] = useState<RequirementCitation[] | null>(null);
  const [streamError, setStreamError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    getSubmittal(id).then((s) => {
      if (cancelled) return;
      setSubmittal(s);
      getProject(s.project_id).then((p) => {
        if (!cancelled) setProject(p);
      });
    });

    const unsubscribe = subscribeToSubmittalProgress(id, {
      onNodeComplete: (e) => {
        if (cancelled) return;
        setEvents((prev) =>
          prev.some((existing) => existing.sequence_number === e.sequence_number)
            ? prev
            : [...prev, { ...e, status: "complete" }]
        );
      },
      onStatus: (status) => {
        if (cancelled) return;
        setSubmittal((prev) => (prev ? { ...prev, status: status as SubmittalDetail["status"] } : prev));
      },
      onDone: () => {
        if (cancelled) return;
        getSubmittal(id).then((s) => {
          if (!cancelled) setSubmittal(s);
        });
      },
      onError: () => {
        if (!cancelled) setStreamError(true);
      },
    });

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [id]);

  useEffect(() => {
    if (submittal?.status !== "COMPLETED") return;
    getSubmittalCitations(submittal.id)
      .then(setCitations)
      .catch(() => setCitations([]));
  }, [submittal?.status, submittal?.id]);

  if (!submittal) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <p className="text-sm text-text-faint">Loading…</p>
      </div>
    );
  }

  if (submittal.status === "FAILED") {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="Review failed" backHref={`/projects/${submittal.project_id}`} />
        <div className="flex flex-1 items-center justify-center p-6">
          <div className="w-full max-w-lg rounded-xl border border-[#F2CFCF] bg-critical-bg p-6">
            <h1 className="mb-2 font-semibold text-critical-text">Review failed</h1>
            <p className="text-sm text-critical-text">
              {submittal.error_message || "An unknown error occurred."}
            </p>
            <Button variant="dark" className="mt-4 h-9 px-4 text-xs">
              Retry review
            </Button>
          </div>
        </div>
      </div>
    );
  }

  if (submittal.status !== "COMPLETED" || !submittal.report) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader
          title={submittal.material_desc || "New submittal"}
          authority={project?.authority}
          backHref={`/projects/${submittal.project_id}`}
        />
        <ProgressView
          submittal={submittal}
          authority={project?.authority}
          events={events}
          streamError={streamError}
        />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <PageHeader
        title={submittal.material_desc || "Findings report"}
        authority={project?.authority}
        backHref={`/projects/${submittal.project_id}`}
      />
      <ReportView submittal={submittal} report={submittal.report} citations={citations} />
    </div>
  );
}
