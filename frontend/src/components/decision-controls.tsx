"use client";

/**
 * Confirm/Dismiss/Edit for one finding — notes/11_pilot_bar_tickets.md Ticket 3. Replaces
 * report-view.tsx's old local-only OverrideControls. Only ever rendered when the caller has
 * already resolved a real, persisted finding (see report-view.tsx's key->finding matching);
 * a row that can't be matched shows a plain "not available" note instead, one level up.
 */
import { useEffect, useState } from "react";
import { Button, Select, TextArea, TextInput } from "@/components/ui";
import { formatRelativeDate } from "@/lib/status";
import type { DecisionAction, FindingSeverity, PersistedFinding, ReasonCode } from "@/lib/api";

const SEVERITY_OPTIONS: { value: FindingSeverity; label: string }[] = [
  { value: "critical", label: "Critical" },
  { value: "warning", label: "Warning" },
  { value: "observation", label: "Observation" },
];

const ACTION_PAST_TENSE: Record<DecisionAction, string> = {
  confirm: "Confirmed",
  dismiss: "Dismissed",
  edit: "Edited",
};

export interface DecisionShortcutHandlers {
  openConfirm: () => void;
  openDismiss: () => void;
}

export function DecisionControls({
  finding,
  reasonCodes,
  currentUserId,
  onSubmit,
  registerShortcuts,
}: {
  finding: PersistedFinding;
  reasonCodes: ReasonCode[];
  currentUserId: string | null;
  onSubmit: (body: {
    action: DecisionAction;
    reason_code: string;
    note?: string;
    corrected_fields?: Record<string, unknown>;
  }) => Promise<void>;
  registerShortcuts?: (handlers: DecisionShortcutHandlers | null) => void;
}) {
  const [panel, setPanel] = useState<DecisionAction | null>(null);
  const [reasonCode, setReasonCode] = useState("");
  const [note, setNote] = useState("");
  const [editSeverity, setEditSeverity] = useState<FindingSeverity>(finding.severity);
  const [editClause, setEditClause] = useState(finding.clause_reference ?? "");
  const [editDescription, setEditDescription] = useState(finding.description ?? "");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function openPanel(action: DecisionAction) {
    setFormError(null);
    setReasonCode("");
    setNote("");
    if (action === "edit") {
      setEditSeverity(finding.severity);
      setEditClause(finding.clause_reference ?? "");
      setEditDescription(finding.description ?? "");
    }
    setPanel(action);
  }

  // Registers this row's confirm/dismiss openers so report-view.tsx's single top-level
  // "c"/"d" keydown listener can reach the currently-expanded row without prop-drilling
  // reactive state through every row on every keystroke.
  useEffect(() => {
    registerShortcuts?.({
      openConfirm: () => openPanel("confirm"),
      openDismiss: () => openPanel("dismiss"),
    });
    return () => registerShortcuts?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finding.id]);

  async function handleSubmit() {
    if (!panel) return;
    if (!reasonCode) {
      setFormError("Pick a reason before saving.");
      return;
    }
    let correctedFields: Record<string, unknown> | undefined;
    if (panel === "edit") {
      correctedFields = {};
      if (editSeverity !== finding.severity) correctedFields.severity = editSeverity;
      if ((editClause || null) !== finding.clause_reference) {
        correctedFields.clause_reference = editClause || null;
      }
      if ((editDescription || null) !== finding.description) {
        correctedFields.description = editDescription || null;
      }
      if (Object.keys(correctedFields).length === 0) {
        setFormError("Change at least one field before saving an edit.");
        return;
      }
    }
    setSubmitting(true);
    setFormError(null);
    try {
      await onSubmit({
        action: panel,
        reason_code: reasonCode,
        note: note.trim() || undefined,
        corrected_fields: correctedFields,
      });
      setPanel(null);
    } catch {
      // The parent (report-view.tsx) already rolled back the optimistic update and showed
      // a toast with a Retry action — this keeps the panel open with the reviewer's
      // reason/note/edits intact rather than discarding them on failure.
      setFormError("Failed to save — try again.");
    } finally {
      setSubmitting(false);
    }
  }

  const decision = finding.current_decision;
  const filteredReasonCodes = reasonCodes.filter((r) => r.action === panel);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <button
          onClick={(e) => {
            e.stopPropagation();
            openPanel("confirm");
          }}
          className={`h-7 rounded-lg px-2.5 text-[11.5px] font-semibold ${
            decision?.action === "confirm"
              ? "bg-ink text-white"
              : "border border-line-2 bg-panel text-text-secondary"
          }`}
        >
          Confirm
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            openPanel("dismiss");
          }}
          className={`h-7 rounded-lg px-2.5 text-[11.5px] font-semibold ${
            decision?.action === "dismiss"
              ? "bg-ink text-white"
              : "border border-line-2 bg-panel text-text-secondary"
          }`}
        >
          Dismiss
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            openPanel("edit");
          }}
          className={`h-7 rounded-lg px-2.5 text-[11.5px] font-semibold ${
            decision?.action === "edit"
              ? "bg-ink text-white"
              : "border border-line-2 bg-panel text-text-secondary"
          }`}
        >
          Edit
        </button>
        {decision && (
          <span className="text-[11.5px] text-text-muted">
            {ACTION_PAST_TENSE[decision.action]} by{" "}
            {decision.actor_user_id === currentUserId ? "you" : "another reviewer"} ·{" "}
            {formatRelativeDate(decision.created_at)}
            {decision.note ? ` — "${decision.note}"` : ""}
          </span>
        )}
      </div>

      {panel && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="animate-riseIn flex flex-col gap-2.5 rounded-[9px] border border-line bg-panel p-3"
        >
          {panel === "edit" && (
            <>
              <div className="flex flex-col gap-1">
                <span className="font-mono text-[10px] font-semibold tracking-[0.06em] text-text-muted">
                  SEVERITY
                </span>
                <Select
                  value={editSeverity}
                  onChange={(e) => setEditSeverity(e.target.value as FindingSeverity)}
                >
                  {SEVERITY_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="flex flex-col gap-1">
                <span className="font-mono text-[10px] font-semibold tracking-[0.06em] text-text-muted">
                  CLAUSE REFERENCE
                </span>
                <TextInput
                  value={editClause}
                  onChange={(e) => setEditClause(e.target.value)}
                  placeholder="e.g. 10.2.2"
                  className="h-8"
                />
              </div>
              <div className="flex flex-col gap-1">
                <span className="font-mono text-[10px] font-semibold tracking-[0.06em] text-text-muted">
                  DESCRIPTION
                </span>
                <TextArea
                  value={editDescription}
                  onChange={(e) => setEditDescription(e.target.value)}
                  rows={3}
                />
              </div>
              <div className="rounded-[7px] bg-panel-2 p-2.5">
                <div className="font-mono text-[9.5px] font-semibold tracking-[0.06em] text-text-faint">
                  AI ORIGINAL
                </div>
                <p className="mt-1 text-[11.5px] leading-relaxed text-text-muted">
                  {finding.description || "—"}
                  {finding.clause_reference ? ` (clause ${finding.clause_reference})` : ""} ·
                  severity {finding.severity}
                </p>
              </div>
            </>
          )}

          <div className="flex flex-col gap-1">
            <span className="font-mono text-[10px] font-semibold tracking-[0.06em] text-text-muted">
              REASON
            </span>
            <Select value={reasonCode} onChange={(e) => setReasonCode(e.target.value)}>
              <option value="">Select a reason…</option>
              {filteredReasonCodes.map((r) => (
                <option key={r.code} value={r.code}>
                  {r.label}
                </option>
              ))}
            </Select>
          </div>

          <div className="flex flex-col gap-1">
            <span className="font-mono text-[10px] font-semibold tracking-[0.06em] text-text-muted">
              NOTE (OPTIONAL)
            </span>
            <TextInput value={note} onChange={(e) => setNote(e.target.value)} className="h-8" />
          </div>

          {formError && <p className="text-[11.5px] text-critical-text">{formError}</p>}

          <div className="flex justify-end gap-2">
            <Button variant="ghost" className="h-7 px-2.5 text-[11.5px]" onClick={() => setPanel(null)}>
              Cancel
            </Button>
            <Button className="h-7 px-2.5 text-[11.5px]" onClick={handleSubmit} disabled={submitting}>
              {submitting ? "Saving…" : "Save"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
