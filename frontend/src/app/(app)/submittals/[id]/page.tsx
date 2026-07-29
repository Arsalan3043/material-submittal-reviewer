"use client";

import { use, useEffect, useRef, useState } from "react";
import {
  askChat,
  getChatHistory,
  getSubmittal,
  getSubmittalCitations,
  getSubmittalEvents,
  type ChatTurn,
  type Finding,
  type RequirementCitation,
  type SubmittalDetail,
  type SubmittalEvent,
} from "@/lib/api";

const POLL_MS = 2000;

// Real orchestrator node order (src/agents/orchestrator.py) — avl_check/skip_avl collapsed
// into one row since only one of the two runs per review, per authority.
const STAGES: { key: string; label: string; nodeNames: string[] }[] = [
  { key: "doc_processor", label: "Extracting & classifying documents", nodeNames: ["doc_processor"] },
  { key: "completeness", label: "Checking document completeness", nodeNames: ["completeness"] },
  { key: "boq_drawing", label: "Reviewing BOQ & drawings", nodeNames: ["boq_drawing"] },
  { key: "spec_verifier", label: "Matching against spec clauses", nodeNames: ["spec_verifier"] },
  { key: "validity_checker", label: "Checking certificate & date validity", nodeNames: ["validity_checker"] },
  { key: "avl", label: "Checking approved vendor list", nodeNames: ["avl_check", "skip_avl"] },
  { key: "statement", label: "Reviewing compliance statement", nodeNames: ["statement"] },
  { key: "table_auditor", label: "Auditing comparison table", nodeNames: ["table_auditor"] },
  { key: "consistency", label: "Checking name consistency", nodeNames: ["consistency"] },
  { key: "others", label: "Reviewing additional documents", nodeNames: ["others"] },
  { key: "report_compiler", label: "Compiling findings", nodeNames: ["report_compiler"] },
];

const SEVERITY_STYLE: Record<string, string> = {
  critical: "bg-red-50 text-red-700 border-red-200",
  warning: "bg-amber-50 text-amber-700 border-amber-200",
  pass: "bg-green-50 text-green-700 border-green-200",
};

const SEVERITY_ORDER: Record<string, number> = { critical: 0, warning: 1, pass: 2 };

const CITATION_STATUS_STYLE: Record<string, string> = {
  satisfied: "bg-green-50 text-green-700 border-green-200",
  non_compliant: "bg-red-50 text-red-700 border-red-200",
  partially_verified: "bg-amber-50 text-amber-700 border-amber-200",
  missing_evidence: "bg-amber-50 text-amber-700 border-amber-200",
  not_applicable: "bg-zinc-50 text-zinc-500 border-zinc-200",
};
const CITATION_STATUS_ORDER: Record<string, number> = {
  non_compliant: 0,
  missing_evidence: 1,
  partially_verified: 2,
  satisfied: 3,
  not_applicable: 4,
};

const RECOMMENDATION_LABEL: Record<string, string> = {
  APPROVE: "Recommended: Approve",
  CONDITIONAL: "Recommended: Conditional Approval",
  RESUBMIT: "Recommended: Revise & Resubmit",
};

interface TableAuditRow {
  finding: string;
  severity: string;
  parameter: string;
  measured_value?: string;
  proposed_value?: string;
  specified_value?: string;
}

export default function SubmittalDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const [submittal, setSubmittal] = useState<SubmittalDetail | null>(null);
  const [events, setEvents] = useState<SubmittalEvent[]>([]);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;

    async function poll() {
      const [s, e] = await Promise.all([getSubmittal(id), getSubmittalEvents(id)]);
      if (cancelled) return;
      setSubmittal(s);
      setEvents(e);
      if (!["COMPLETED", "FAILED", "CANCELLED"].includes(s.status)) {
        timer = setTimeout(poll, POLL_MS);
      }
    }
    poll();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id]);

  if (!submittal) {
    return <div className="p-8 text-sm text-zinc-400">Loading...</div>;
  }

  if (submittal.status === "FAILED") {
    return (
      <div className="mx-auto max-w-2xl p-8">
        <div className="rounded-lg border border-red-200 bg-red-50 p-6">
          <h1 className="mb-2 font-semibold text-red-800">Review failed</h1>
          <p className="text-sm text-red-700">
            {submittal.error_message || "An unknown error occurred."}
          </p>
        </div>
      </div>
    );
  }

  if (submittal.status !== "COMPLETED" || !submittal.report) {
    return <ProgressView submittal={submittal} events={events} />;
  }

  return <ReportView submittal={submittal} report={submittal.report} />;
}

function ProgressView({
  submittal,
  events,
}: {
  submittal: SubmittalDetail;
  events: SubmittalEvent[];
}) {
  const doneNodeNames = new Set(events.map((e) => e.node_name));

  return (
    <div className="mx-auto max-w-lg p-8">
      <h1 className="mb-1 text-lg font-semibold text-zinc-900">
        Reviewing {submittal.material_desc || "submittal"}...
      </h1>
      <p className="mb-6 text-sm text-zinc-500">
        This usually takes 1-6 minutes. Feel free to navigate away — we&apos;ll notify you
        when it&apos;s done.
      </p>

      <ul className="space-y-2">
        {STAGES.map((stage) => {
          const done = stage.nodeNames.some((n) => doneNodeNames.has(n));
          const isNext =
            !done &&
            STAGES.slice(0, STAGES.indexOf(stage)).every((s) =>
              s.nodeNames.some((n) => doneNodeNames.has(n))
            );
          return (
            <li key={stage.key} className="flex items-center gap-2 text-sm">
              <span className="w-4 text-center">
                {done ? "✓" : isNext ? "⏳" : "○"}
              </span>
              <span className={done ? "text-zinc-900" : isNext ? "text-zinc-700" : "text-zinc-400"}>
                {stage.label}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ReportView({
  submittal,
  report,
}: {
  submittal: SubmittalDetail;
  report: NonNullable<SubmittalDetail["report"]>;
}) {
  const [citations, setCitations] = useState<RequirementCitation[] | null>(null);
  useEffect(() => {
    getSubmittalCitations(submittal.id)
      .then(setCitations)
      .catch(() => setCitations([]));
  }, [submittal.id]);

  const genericFindings: (Finding & { source: string })[] = [
    ...report.completeness_findings.map((f) => ({ ...f, source: "Completeness" })),
    ...report.spec_verification_findings.map((f) => ({ ...f, source: "Spec Verification" })),
    ...report.validity_findings.map((f) => ({ ...f, source: "Validity" })),
    ...report.avl_findings.map((f) => ({ ...f, source: "Approved Vendor List" })),
    ...report.statement_findings.map((f) => ({ ...f, source: "Compliance Statement" })),
    ...report.consistency_findings.map((f) => ({ ...f, source: "Consistency" })),
    ...report.others_findings.map((f) => ({ ...f, source: "Other Documents" })),
  ]
    .filter((f) => f.description)
    .sort((a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9));

  const tableRows = (report.table_audit_findings as unknown as TableAuditRow[]).sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 9) - (SEVERITY_ORDER[b.severity] ?? 9)
  );

  return (
    <div className="mx-auto max-w-4xl space-y-8 p-8">
      {/* Headline */}
      <div className="rounded-lg border border-zinc-200 bg-white p-6">
        <h1 className="mb-1 text-xl font-semibold text-zinc-900">
          {RECOMMENDATION_LABEL[report.overall_recommendation] ?? report.overall_recommendation}
        </h1>
        <p className="mb-3 text-sm text-zinc-600">
          {report.critical_count} Critical · {report.warning_count} Warnings
        </p>
        <p className="mb-3 text-sm text-zinc-700">{report.summary_comments}</p>
        <p className="text-xs text-zinc-400">
          Reviewed against spec clause: {report.spec_clause || "not identified"}
        </p>
        <p className="mt-2 text-xs font-medium text-zinc-500">
          You make the final call — confirm, override, or dismiss any finding below.
        </p>
      </div>

      {report.missing_documents.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <strong>Missing documents:</strong> {report.missing_documents.join(", ")}
        </div>
      )}

      {/* Spec compliance with clickable citations — the trust feature: every requirement
          links to the exact spec page and submittal evidence page it came from. */}
      {citations && citations.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-zinc-900">
            Spec Compliance{" "}
            <span className="font-normal text-zinc-400">
              ({citations.length} requirements checked against the spec)
            </span>
          </h2>
          <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
            <table className="w-full text-sm">
              <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase text-zinc-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Requirement</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Spec source</th>
                  <th className="px-4 py-2 font-medium">Evidence</th>
                </tr>
              </thead>
              <tbody>
                {[...citations]
                  .sort(
                    (a, b) =>
                      (CITATION_STATUS_ORDER[a.status] ?? 9) -
                      (CITATION_STATUS_ORDER[b.status] ?? 9)
                  )
                  .map((c) => (
                    <tr key={c.requirement_id} className="border-b border-zinc-100 last:border-0">
                      <td className="px-4 py-3 text-zinc-800">
                        {c.requirement_summary}
                        <div className="mt-1 text-xs text-zinc-500">{c.reasoning}</div>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-block rounded border px-2 py-0.5 text-xs font-medium capitalize ${
                            CITATION_STATUS_STYLE[c.status] ?? "border-zinc-200 bg-zinc-50 text-zinc-600"
                          }`}
                        >
                          {c.status.replace("_", " ")}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {c.spec_citation?.view_url ? (
                          <a
                            href={c.spec_citation.view_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs font-medium text-blue-600 underline hover:text-blue-800"
                          >
                            Clause {c.spec_citation.clause} (p.{c.spec_citation.page})
                          </a>
                        ) : (
                          <span className="text-xs text-zinc-300">—</span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {c.evidence_citations.length === 0 ? (
                          <span className="text-xs text-zinc-300">—</span>
                        ) : (
                          <div className="flex flex-col gap-0.5">
                            {c.evidence_citations.map((e, i) =>
                              e.view_url ? (
                                <a
                                  key={i}
                                  href={e.view_url}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-xs font-medium text-blue-600 underline hover:text-blue-800"
                                >
                                  {e.document} (p.{e.page})
                                </a>
                              ) : (
                                <span key={i} className="text-xs text-zinc-400">
                                  {e.document} (p.{e.page})
                                </span>
                              )
                            )}
                          </div>
                        )}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Findings */}
      <div>
        <h2 className="mb-2 text-sm font-semibold text-zinc-900">Findings</h2>
        <div className="overflow-hidden rounded-lg border border-zinc-200 bg-white">
          <table className="w-full text-sm">
            <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase text-zinc-500">
              <tr>
                <th className="px-4 py-2 font-medium">Area</th>
                <th className="px-4 py-2 font-medium">Finding</th>
                <th className="px-4 py-2 font-medium">Status</th>
              </tr>
            </thead>
            <tbody>
              {genericFindings.map((f, i) => (
                <tr key={i} className="border-b border-zinc-100 last:border-0">
                  <td className="whitespace-nowrap px-4 py-3 text-zinc-500">{f.source}</td>
                  <td className="px-4 py-3 text-zinc-800">
                    {f.description}
                    {f.action_required && (
                      <div className="mt-1 text-xs text-zinc-500">→ {f.action_required}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`inline-block rounded border px-2 py-0.5 text-xs font-medium capitalize ${
                        SEVERITY_STYLE[f.severity] ?? "border-zinc-200 bg-zinc-50 text-zinc-600"
                      }`}
                    >
                      {f.severity}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Comparison table audit */}
      {tableRows.length > 0 && (
        <div>
          <h2 className="mb-2 text-sm font-semibold text-zinc-900">
            Comparison Table Audit
          </h2>
          <div className="overflow-x-auto rounded-lg border border-zinc-200 bg-white">
            <table className="w-full text-sm">
              <thead className="border-b border-zinc-200 bg-zinc-50 text-left text-xs uppercase text-zinc-500">
                <tr>
                  <th className="px-4 py-2 font-medium">Parameter</th>
                  <th className="px-4 py-2 font-medium">Specified</th>
                  <th className="px-4 py-2 font-medium">Proposed</th>
                  <th className="px-4 py-2 font-medium">Measured</th>
                  <th className="px-4 py-2 font-medium">Status</th>
                  <th className="px-4 py-2 font-medium">Remarks</th>
                </tr>
              </thead>
              <tbody>
                {tableRows.map((r, i) => (
                  <tr key={i} className="border-b border-zinc-100 last:border-0">
                    <td className="px-4 py-3 text-zinc-800">{r.parameter}</td>
                    <td className="px-4 py-3 text-zinc-600">{r.specified_value || "—"}</td>
                    <td className="px-4 py-3 text-zinc-600">{r.proposed_value || "—"}</td>
                    <td className="px-4 py-3 text-zinc-600">{r.measured_value || "—"}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`inline-block rounded border px-2 py-0.5 text-xs font-medium capitalize ${
                          SEVERITY_STYLE[r.severity] ?? "border-zinc-200 bg-zinc-50 text-zinc-600"
                        }`}
                      >
                        {r.severity}
                      </span>
                    </td>
                    <td className="max-w-xs px-4 py-3 text-xs text-zinc-500">{r.finding}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <ChatBox submittalId={submittal.id} />
    </div>
  );
}

function ChatBox({ submittalId }: { submittalId: string }) {
  const [history, setHistory] = useState<ChatTurn[]>([]);
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getChatHistory(submittalId).then(setHistory);
  }, [submittalId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history]);

  async function handleAsk(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setAsking(true);
    const q = question;
    setQuestion("");
    try {
      const res = await askChat(submittalId, q);
      setHistory((prev) => [
        ...prev,
        {
          question: q,
          answer: res.answer,
          route: res.source,
          sources: res.source_references,
          created_at: new Date().toISOString(),
        },
      ]);
    } finally {
      setAsking(false);
    }
  }

  return (
    <div>
      <h2 className="mb-2 text-sm font-semibold text-zinc-900">Ask about this submittal</h2>
      <div className="rounded-lg border border-zinc-200 bg-white p-4">
        <div className="max-h-80 space-y-4 overflow-y-auto">
          {history.length === 0 && (
            <p className="text-sm text-zinc-400">
              Ask a question grounded in this submittal and its spec, e.g. &ldquo;what does
              the contractor need to fix?&rdquo;
            </p>
          )}
          {history.map((turn, i) => (
            <div key={i} className="text-sm">
              <p className="font-medium text-zinc-900">{turn.question}</p>
              <p className="mt-1 text-zinc-700">{turn.answer}</p>
              {turn.sources?.length > 0 && (
                <p className="mt-1 text-xs text-zinc-400">
                  Source: {turn.sources.join(", ")}
                </p>
              )}
            </div>
          ))}
          <div ref={bottomRef} />
        </div>

        <form onSubmit={handleAsk} className="mt-4 flex gap-2">
          <input
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask a question..."
            className="flex-1 rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 outline-none placeholder:text-zinc-400 focus:border-zinc-500"
          />
          <button
            type="submit"
            disabled={asking}
            className="rounded-md bg-zinc-900 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:opacity-50"
          >
            {asking ? "..." : "Ask"}
          </button>
        </form>
      </div>
    </div>
  );
}
