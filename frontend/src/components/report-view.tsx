"use client";

import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import type { Finding, RequirementCitation, ReviewReport, SubmittalDetail } from "@/lib/api";
import { Button, StatusChip } from "@/components/ui";
import {
  CITATION_STATUS_LABEL,
  CITATION_STATUS_SEVERITY,
  SEVERITY_CHIP,
  VERDICT_TITLE,
  VERDICT_TOKENS,
  type Severity,
} from "@/lib/status";
import { ChatPanel } from "@/components/chat-panel";

const SEVERITY_ORDER: Record<Severity, number> = { critical: 0, warning: 1, pass: 2 };

interface CitationRow {
  kind: "citation";
  key: string;
  category: string;
  parameter: string;
  required: string;
  submitted: string;
  statusLabel: string;
  severity: Severity;
  clause: string;
  reasoning: string;
  specText: string | null;
  specPage: number | null;
  specViewUrl: string | null;
  evidenceDoc: string | null;
  evidencePage: number | null;
  evidenceText: string | null;
  evidenceViewUrl: string | null;
}

interface TableRow {
  kind: "table";
  key: string;
  category: string;
  parameter: string;
  required: string;
  submitted: string;
  statusLabel: string;
  severity: Severity;
  remark: string;
}

interface GenericRow {
  kind: "generic";
  key: string;
  category: string;
  parameter: string;
  required: string;
  submitted: string;
  statusLabel: string;
  severity: Severity;
  document: string | null;
  actionRequired: string | null;
}

type MatrixRow = CitationRow | TableRow | GenericRow;

type OverrideState = "confirmed" | "dismissed";

function truncate(text: string, max = 90): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

function toSeverity(raw: string): Severity {
  return raw === "critical" || raw === "warning" || raw === "pass" ? raw : "warning";
}

function severityStatusLabel(severity: Severity): string {
  return severity === "pass" ? "MET" : severity === "warning" ? "PARTIAL" : "NOT MET";
}

/**
 * The report has 9 separate finding categories plus a written summary — the report screen
 * originally only surfaced 2 of them (spec-verification citations + the table audit). This
 * builds the other 7 Finding[] arrays into matrix rows so nothing the pipeline actually
 * found is silently missing from the UI.
 */
const GENERIC_CATEGORIES: { key: keyof ReviewReport; label: string }[] = [
  { key: "completeness_findings", label: "COMPLETENESS" },
  { key: "boq_drawing_findings", label: "BOQ/DRAWING" },
  { key: "validity_findings", label: "VALIDITY" },
  { key: "avl_findings", label: "AVL" },
  { key: "statement_findings", label: "STATEMENT" },
  { key: "consistency_findings", label: "CONSISTENCY" },
  { key: "others_findings", label: "OTHER" },
];

export function ReportView({
  submittal,
  report,
  citations,
}: {
  submittal: SubmittalDetail;
  report: ReviewReport;
  citations: RequirementCitation[] | null;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, OverrideState>>({});

  const rows: MatrixRow[] = useMemo(() => {
    const citationRows: CitationRow[] = (citations ?? []).map((c) => ({
      kind: "citation",
      key: `c-${c.requirement_id}`,
      category: "SPEC",
      parameter: c.requirement_summary,
      required: c.spec_citation?.text ? truncate(c.spec_citation.text) : "—",
      submitted:
        c.evidence_citations[0]?.text != null ? truncate(c.evidence_citations[0].text) : "No evidence found",
      statusLabel: CITATION_STATUS_LABEL[c.status],
      severity: CITATION_STATUS_SEVERITY[c.status],
      clause: c.spec_citation?.clause ?? "—",
      reasoning: c.reasoning,
      specText: c.spec_citation?.text ?? null,
      specPage: c.spec_citation?.page ?? null,
      specViewUrl: c.spec_citation?.view_url ?? null,
      evidenceDoc: c.evidence_citations[0]?.document ?? null,
      evidencePage: c.evidence_citations[0]?.page ?? null,
      evidenceText: c.evidence_citations[0]?.text ?? null,
      evidenceViewUrl: c.evidence_citations[0]?.view_url ?? null,
    }));

    const tableRows: TableRow[] = (report.table_audit_findings as unknown as {
      parameter: string;
      specified_value: string;
      proposed_value: string;
      severity: Severity;
      finding: string;
    }[]).map((r, i) => ({
      kind: "table",
      key: `t-${i}`,
      category: "TABLE AUDIT",
      parameter: r.parameter,
      required: r.specified_value || "—",
      submitted: r.proposed_value || "—",
      statusLabel: severityStatusLabel(r.severity),
      severity: r.severity,
      remark: r.finding,
    }));

    const genericRows: GenericRow[] = GENERIC_CATEGORIES.flatMap(({ key, label }) => {
      const findings = (report[key] as unknown as Finding[]) ?? [];
      return findings
        .filter((f) => f.description)
        .map((f, i) => {
          const severity = toSeverity(f.severity);
          return {
            kind: "generic" as const,
            key: `g-${key}-${i}`,
            category: label,
            parameter: f.description ?? "",
            required: "—",
            submitted: "—",
            statusLabel: severityStatusLabel(severity),
            severity,
            document: f.document ?? null,
            actionRequired: f.action_required ?? null,
          };
        });
    });

    // Lead with the highest severity across ALL categories (README: "don't show a flat
    // undifferentiated list") rather than grouping citations-then-table-then-generic.
    return [...citationRows, ...tableRows, ...genericRows].sort(
      (a, b) => SEVERITY_ORDER[a.severity] - SEVERITY_ORDER[b.severity]
    );
  }, [citations, report]);

  const overrideCount = Object.keys(overrides).length;
  const verdict = report.overall_recommendation;
  const tokens = VERDICT_TOKENS[verdict] ?? VERDICT_TOKENS.RESUBMIT;

  function setOverride(key: string, value: OverrideState | null) {
    setOverrides((prev) => {
      const next = { ...prev };
      if (value === null) delete next[key];
      else next[key] = value;
      return next;
    });
  }

  return (
    <div className="flex flex-1 flex-col overflow-hidden md:flex-row">
      <div className="flex flex-1 flex-col gap-3 overflow-y-auto p-5 md:min-w-0 md:p-6">
        {/* Verdict banner */}
        <div
          className="flex items-center gap-3.5 rounded-xl border p-4 transition-colors duration-300 md:gap-3.5 md:p-[17px]"
          style={{ background: tokens.bg, borderColor: tokens.border }}
        >
          <div className="h-full w-1 self-stretch rounded-full" style={{ background: tokens.bar }} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span
                className="text-[17px] font-bold tracking-[-0.01em] md:text-[19px]"
                style={{ color: tokens.title }}
              >
                {VERDICT_TITLE[verdict] ?? verdict}
              </span>
            </div>
            <div className="mt-1.5 text-[12.5px] leading-relaxed md:text-[13px]" style={{ color: tokens.body }}>
              {report.critical_count} critical, {report.warning_count} warning
              {report.warning_count === 1 ? "" : "s"} across {rows.length} checked items.
              {overrideCount > 0 &&
                ` · ${overrideCount} finding${overrideCount > 1 ? "s" : ""} overridden by you`}
            </div>
          </div>
          <div className="hidden shrink-0 gap-2 md:flex">
            <Button
              className="h-[34px] border bg-panel px-3.5 text-xs"
              style={{ borderColor: tokens.border, color: tokens.title }}
              variant="ghost"
            >
              Export PDF
            </Button>
            <Button variant="dark" className="h-[34px] px-3.5 text-xs">
              Draft transmittal
            </Button>
          </div>
        </div>

        {report.summary_comments && (
          <div className="rounded-xl border border-line bg-panel p-3.5 text-[12.5px] leading-relaxed text-ink-2 md:p-4 md:text-[13px]">
            {report.summary_comments}
          </div>
        )}

        {/* Scope transparency line */}
        <div className="hidden flex-wrap items-center gap-2.5 text-xs text-text-muted md:flex">
          <span>
            Checked against <span className="text-ink">{report.authority} spec</span>
            {report.spec_clause ? ` · clause ${report.spec_clause}` : ""} ·{" "}
            {citations?.length ?? 0} spec requirements, {rows.length} total checks
          </span>
          <span className="h-[11px] w-px bg-[#DCDCD8]" />
          <span>
            {report.authority === "TAQA"
              ? "AVL check included — TAQA project"
              : "AVL check skipped — not a TAQA project"}
          </span>
          <span className="h-[11px] w-px bg-[#DCDCD8]" />
          <span>
            {overrideCount > 0
              ? `${overrideCount} of ${rows.length} findings resolved by you`
              : "No findings resolved yet"}
          </span>
        </div>

        {report.missing_documents.length > 0 && (
          <div className="rounded-xl border border-[#F2CFCF] bg-critical-bg p-3.5 text-[12.5px] text-critical-text md:hidden">
            <strong>Missing:</strong> {report.missing_documents.join(", ")}
          </div>
        )}

        {/* Compliance matrix — desktop */}
        <div className="hidden min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-line bg-panel md:flex">
          <div className="grid grid-cols-[1fr_168px_168px_104px_76px] gap-3 border-b border-[#EDEDEA] bg-panel-2 px-4 py-2.5 font-mono text-[10px] font-semibold tracking-[0.09em] text-text-muted">
            <div>PARAMETER</div>
            <div>REQUIRED</div>
            <div>SUBMITTED</div>
            <div>STATUS</div>
            <div>CLAUSE</div>
          </div>
          <div className="flex-1 overflow-y-auto">
            {rows.map((row) => (
              <MatrixRowItem
                key={row.key}
                row={row}
                open={openKey === row.key}
                onToggle={() => setOpenKey(openKey === row.key ? null : row.key)}
                override={overrides[row.key]}
                onOverride={(v) => setOverride(row.key, v)}
              />
            ))}
            {rows.length === 0 && (
              <p className="p-6 text-center text-sm text-text-faint">
                No findings to review — the pipeline produced no checkable items.
              </p>
            )}
          </div>
        </div>

        {/* Mobile findings cards */}
        <div className="flex flex-col gap-2.5 md:hidden">
          {rows.map((row) => (
            <MobileFindingCard
              key={row.key}
              row={row}
              open={openKey === row.key}
              onToggle={() => setOpenKey(openKey === row.key ? null : row.key)}
              override={overrides[row.key]}
              onOverride={(v) => setOverride(row.key, v)}
            />
          ))}
        </div>
      </div>

      {/* Right rail — desktop only; mobile moves this to its own area below */}
      <div className="hidden w-[318px] shrink-0 flex-col border-l border-line-2 bg-panel-2 md:flex">
        <div className="flex flex-col gap-2.5 border-b border-[#E9E9E6] px-4.5 py-4">
          <div className="font-mono text-[11px] font-semibold tracking-[0.08em] text-text-muted">
            MISSING DOCUMENTS · {report.missing_documents.length}
          </div>
          {report.missing_documents.length === 0 && (
            <p className="text-[12.5px] text-text-muted">Nothing outstanding.</p>
          )}
          {report.missing_documents.map((m, i) => (
            <div key={i} className="flex items-start gap-2.5">
              <span className="mt-1.5 h-[5px] w-[5px] shrink-0 rounded-full bg-critical" />
              <span className="text-[12.5px] leading-snug text-ink-2">{m}</span>
            </div>
          ))}
        </div>
        <ChatPanel submittalId={submittal.id} />
      </div>
    </div>
  );
}

function CategoryTag({ category }: { category: string }) {
  return (
    <span className="shrink-0 font-mono text-[9px] font-semibold tracking-[0.06em] text-text-faint">
      {category}
    </span>
  );
}

function MatrixRowItem({
  row,
  open,
  onToggle,
  override,
  onOverride,
}: {
  row: MatrixRow;
  open: boolean;
  onToggle: () => void;
  override: OverrideState | undefined;
  onOverride: (v: OverrideState | null) => void;
}) {
  const dismissed = override === "dismissed";
  return (
    <>
      <div
        onClick={onToggle}
        className={`grid cursor-pointer grid-cols-[1fr_168px_168px_104px_76px] items-center gap-3 border-b border-line-3 px-4 py-3 ${
          open ? "bg-[#FBFBFA]" : ""
        }`}
      >
        <div className="flex min-w-0 items-center gap-2">
          <ChevronRight
            size={14}
            strokeWidth={2.5}
            className={`shrink-0 text-[#B0B0B5] transition-transform duration-200 ${open ? "rotate-90" : ""}`}
          />
          <CategoryTag category={row.category} />
          <span
            className={`truncate text-[13px] font-medium text-ink ${dismissed ? "line-through" : ""}`}
          >
            {row.parameter}
          </span>
        </div>
        <div className="truncate font-mono text-xs text-text-secondary">{row.required}</div>
        <div className="truncate font-mono text-xs text-text-secondary">{row.submitted}</div>
        <div>
          <StatusChip
            label={override ? override.toUpperCase() : row.statusLabel}
            severityClass={SEVERITY_CHIP[dismissed ? "pass" : row.severity]}
          />
        </div>
        <div className="truncate font-mono text-[11px] text-accent">
          {row.kind === "citation" ? row.clause : "—"}
        </div>
      </div>
      {open && (
        <div className="animate-riseIn flex flex-col gap-3 border-b border-line-3 bg-[#FCFCFB] px-4 py-4 pl-[34px]">
          {row.kind === "citation" && (
            <div className="flex flex-col gap-2.5 md:flex-row">
              <div className="flex-1 rounded-[9px] border border-line bg-panel p-3.5">
                <div className="font-mono text-[10px] font-semibold tracking-[0.08em] text-text-muted">
                  SPEC · CL. {row.clause}
                  {row.specPage ? `, P.${row.specPage}` : ""}
                </div>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">
                  {row.specText ?? "No spec text extracted for this requirement."}
                </p>
                {row.specViewUrl && (
                  <a
                    href={row.specViewUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-block text-[11px] font-medium text-accent"
                  >
                    Open spec at page {row.specPage} ↗
                  </a>
                )}
              </div>
              <div className="flex-1 rounded-[9px] border border-line bg-panel p-3.5">
                <div className="font-mono text-[10px] font-semibold tracking-[0.08em] text-text-muted">
                  EVIDENCE {row.evidenceDoc ? `· ${row.evidenceDoc}` : ""}
                </div>
                <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">
                  {row.evidenceText ?? "No evidence found in the uploaded documents."}
                </p>
                {row.evidenceViewUrl && (
                  <a
                    href={row.evidenceViewUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2 inline-block text-[11px] font-medium text-accent"
                  >
                    Open document at page {row.evidencePage} ↗
                  </a>
                )}
              </div>
            </div>
          )}
          {row.kind === "table" && (
            <p className="text-[12.5px] leading-relaxed text-ink-2">{row.remark}</p>
          )}
          {row.kind === "generic" && (
            <div className="rounded-[9px] border border-line bg-panel p-3.5">
              {row.document && (
                <div className="font-mono text-[10px] font-semibold tracking-[0.08em] text-text-muted">
                  DOCUMENT · {row.document}
                </div>
              )}
              <p className="mt-1.5 text-[12.5px] leading-relaxed text-ink-2">
                {row.actionRequired || "No further action specified."}
              </p>
            </div>
          )}

          <OverrideControls override={override} onOverride={onOverride} />
        </div>
      )}
    </>
  );
}

function OverrideControls({
  override,
  onOverride,
}: {
  override: OverrideState | undefined;
  onOverride: (v: OverrideState | null) => void;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        onClick={(e) => {
          e.stopPropagation();
          onOverride(override === "confirmed" ? null : "confirmed");
        }}
        className={`h-7 rounded-lg px-2.5 text-[11.5px] font-semibold ${
          override === "confirmed" ? "bg-ink text-white" : "border border-line-2 bg-panel text-text-secondary"
        }`}
      >
        {override === "confirmed" ? "Confirmed ✓" : "Confirm finding"}
      </button>
      <button
        onClick={(e) => {
          e.stopPropagation();
          onOverride(override === "dismissed" ? null : "dismissed");
        }}
        className={`h-7 rounded-lg px-2.5 text-[11.5px] font-semibold ${
          override === "dismissed" ? "bg-ink text-white" : "border border-line-2 bg-panel text-text-secondary"
        }`}
      >
        {override === "dismissed" ? "Dismissed ✓" : "Dismiss with note"}
      </button>
      {override && (
        <span className="animate-fadeIn text-[11.5px] text-text-muted">
          {override === "confirmed"
            ? "Confirmed — carried into the transmittal"
            : "Dismissed — excluded from the recommendation"}{" "}
          ·{" "}
          <button
            onClick={(e) => {
              e.stopPropagation();
              onOverride(null);
            }}
            className="text-accent"
          >
            undo
          </button>
        </span>
      )}
    </div>
  );
}

function MobileFindingCard({
  row,
  open,
  onToggle,
  override,
  onOverride,
}: {
  row: MatrixRow;
  open: boolean;
  onToggle: () => void;
  override: OverrideState | undefined;
  onOverride: (v: OverrideState | null) => void;
}) {
  const dismissed = override === "dismissed";
  return (
    <div onClick={onToggle} className="flex flex-col gap-2 rounded-xl border border-line bg-panel p-3.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-1.5">
          <CategoryTag category={row.category} />
        </div>
        <StatusChip
          label={override ? override.toUpperCase() : row.statusLabel}
          severityClass={SEVERITY_CHIP[dismissed ? "pass" : row.severity]}
        />
      </div>
      <span className={`text-sm font-medium text-ink ${dismissed ? "line-through" : ""}`}>
        {row.parameter}
      </span>
      {row.kind !== "generic" && (
        <div className="font-mono text-[11.5px] leading-relaxed text-[#6E7175]">
          req {row.required}
          <br />
          got {row.submitted}
        </div>
      )}
      {open ? (
        <div className="animate-riseIn flex flex-col gap-2.5 pt-1">
          {row.kind === "citation" && (
            <>
              <div className="rounded-[9px] bg-panel-2 p-3">
                <div className="font-mono text-[9.5px] font-semibold tracking-[0.08em] text-text-muted">
                  SPEC · CL. {row.clause}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-ink-2">
                  {row.specText ?? "No spec text extracted."}
                </p>
              </div>
              <div className="rounded-[9px] bg-panel-2 p-3">
                <div className="font-mono text-[9.5px] font-semibold tracking-[0.08em] text-text-muted">
                  EVIDENCE {row.evidenceDoc ? `· ${row.evidenceDoc}` : ""}
                </div>
                <p className="mt-1.5 text-xs leading-relaxed text-ink-2">
                  {row.evidenceText ?? "No evidence found."}
                </p>
              </div>
            </>
          )}
          {row.kind === "table" && (
            <p className="text-xs leading-relaxed text-ink-2">{row.remark}</p>
          )}
          {row.kind === "generic" && (
            <div className="rounded-[9px] bg-panel-2 p-3">
              {row.document && (
                <div className="font-mono text-[9.5px] font-semibold tracking-[0.08em] text-text-muted">
                  DOCUMENT · {row.document}
                </div>
              )}
              <p className="mt-1.5 text-xs leading-relaxed text-ink-2">
                {row.actionRequired || "No further action specified."}
              </p>
            </div>
          )}
          <div className="flex gap-2">
            <button
              onClick={(e) => {
                e.stopPropagation();
                onOverride(override === "confirmed" ? null : "confirmed");
              }}
              className={`h-11 flex-1 rounded-[11px] text-[13px] font-semibold ${
                override === "confirmed" ? "bg-ink text-white" : "border border-line-2 bg-panel text-text-secondary"
              }`}
            >
              {override === "confirmed" ? "Confirmed ✓" : "Confirm"}
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onOverride(override === "dismissed" ? null : "dismissed");
              }}
              className={`h-11 flex-1 rounded-[11px] text-[13px] font-semibold ${
                override === "dismissed" ? "bg-ink text-white" : "border border-line-2 bg-panel text-text-secondary"
              }`}
            >
              {override === "dismissed" ? "Dismissed ✓" : "Dismiss"}
            </button>
          </div>
        </div>
      ) : (
        row.kind === "citation" && (
          <div className="font-mono text-[11px] font-medium text-accent">
            CL {row.clause} · TAP FOR EVIDENCE
          </div>
        )
      )}
    </div>
  );
}
