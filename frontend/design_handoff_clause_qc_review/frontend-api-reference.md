# Frontend Reference — Full API Inventory + Screen List

> Written to be handed to a design tool (e.g. Claude Design) as the ground truth for what
> screens/flows the frontend needs to support. Every endpoint and JSON shape below is copied
> directly from the actual FastAPI route implementations in `apps/api/` — not aspirational.
> A working Next.js frontend already exists in `frontend/`; this document is a clean API
> reference for redesigning or extending its UI, not a from-scratch build brief.
>
> For narrative context (why routes are shaped this way, what's deliberately not built yet,
> and the priority order for filling gaps) see `notes/api.md` — this file is the terse,
> copy-pasteable version of the same information, organized around what a UI needs to render.
>
> §0 is the screen list. §1-7 is the API reference each screen is built from. §8 lists gap
> screens with no backend yet — flagged so a design isn't wired to fake data by mistake.

---

## 0. Screen list

Screens 1-8 below are numbered to match `planning/07_ui_ux_spec.md` exactly — that document
has the full interaction detail (copy, empty states, trust/citation UX principles) for each
one; this list is the condensed version plus the screens it doesn't cover (auth edge states,
admin, and the gap screens from §8). Hand both files to the design tool together.

| # | Screen | Purpose | Built from |
|---|---|---|---|
| 1 | **Login** | Cognito email/password sign-in. No self-registration — accounts are admin-provisioned. | Cognito SDK directly, not a REST route |
| 2 | **Projects (home)** | Sidebar of all projects + "+ New Project"; main area is a project grid when nothing's selected. Empty state pushes straight to project creation. | `GET /projects` |
| 3 | **Create Project** | Minimal form: name, authority (`ADM`\|`TAQA` today — the multi-select-of-authorities and custom-spec-upload described in the UX spec are ahead of current scope, see note below). | `POST /projects` |
| 4 | **Inside a Project** | Project home: "Submit New Submittal" button + a filterable/sortable submittal history table (the audit record UAE engineers already keep). | `GET /projects/{id}`, `GET /projects/{id}/submittals` |
| 5 | **Submit a Submittal** | One drag-and-drop zone, minimal metadata, submit. | `POST /projects/{id}/submittals` (upload URLs), then direct-to-S3 PUTs, then `POST /submittals/{id}/start` |
| 6 | **Review in Progress** | Staged, determinate progress (not a spinner) — checklist of the 11 pipeline stages, weighted so `doc_processor` visibly dominates the wait. Non-blocking: user can navigate away. | `GET /submittals/{id}/events` (poll) or `GET /submittals/{id}/stream` (SSE) |
| 7 | **Findings Report** | The core screen. Headline recommendation + counts, scope-transparency line, a compliance-matrix table with clause/page citations on every row, severity color coding, one-click dismiss/confirm per finding (backend not built — see G1). | `GET /submittals/{id}` (report), `GET /submittals/{id}/citations` |
| 8 | **Chat / Ask** | Per-submittal Q&A panel, grounded in the spec/submittal/report, each answer showing its source. | `POST /submittals/{id}/chat`, `GET /submittals/{id}/chat` |

**Supporting / cross-cutting screens not in the original 8:**

| # | Screen | Purpose | Built from |
|---|---|---|---|
| 9 | **Auth edge states** | Session-expired redirect to Login; a "your account has no tenant" error state (shouldn't normally be reachable — admin-provisioned accounts always have one — but the API can return it, so design a state for it rather than a blank crash). | `GET /me` returning 401/404 |
| 10 | **Spec Library (admin)** | `tenant_admin`-only. Upload + index authority spec PDFs, see what's indexed and its chunk count. No screen for this exists in the current frontend yet — genuinely new. | `POST /specs/upload-url`, `POST /specs/index`, `GET /specs` |
| 11 | **Revision history (within Screen 7)** | Not a separate screen — a chain/timeline element inside the Findings Report showing Rev 0 → Rev 1 → ... Backend doesn't exist yet (G6); design the element but don't wire it. | none yet |

**Note on Screen 3 scope:** the UX spec describes a multi-select of authority spec libraries
plus a drag-and-drop for project-specific custom specs, both at project-creation time. The
current API only supports a single `authority` field at creation and a separate
`POST /projects/{id}/specs` call to attach one already-indexed spec afterward — there's no
multi-select or custom-spec-upload-at-creation endpoint yet. Design Screen 3 to the simpler,
currently-real flow (pick one authority, attach specs as a follow-up step inside Screen 4)
unless you're deliberately designing ahead of the API for a near-term build.

---

## 1. Base URL, auth, and the request shape every route shares

- **Base URL:** `/api/v1`
- **Auth:** every route (except `/health`, `/ready` once built) requires
  `Authorization: Bearer <Cognito access token>`.
- **`tenant_id` is never sent by the client.** It's resolved server-side from the verified
  token. Don't design any form field or hidden input for it.
- **Roles:** `reviewer` (read/write submittals + chat), `tenant_admin` (also spec upload/
  index), `super_admin` (reserved, no routes yet — see Gap G4 below).
- **Error shape:** FastAPI's default `{"detail": "..."}` on non-2xx. Meaningful status codes
  to design for: `404` (not found — also returned for another tenant's resource, deliberately
  indistinguishable from "doesn't exist"), `409` (illegal state transition, e.g. starting an
  already-started submittal), `400` (bad input), `403` (role-gated write, e.g. non-admin
  trying to index a spec).

---

## 2. Identity

### `GET /me`
Returns the logged-in user — call once after login to know what to render (role-gated UI).

```json
{ "user_id": "uuid", "tenant_id": "uuid", "role": "reviewer", "email": "user@example.com" }
```

---

## 3. Projects

A tenant has many projects; each project has one `authority` (`ADM` or `TAQA` — launch
scope) chosen at creation and never changed.

### `POST /projects` — create
Request: `{ "name": "string", "authority": "ADM" | "TAQA", "description": "string?" }`
Response: `{ "id": "uuid", "name": "...", "authority": "...", "description": "...", "created_at": "iso8601" }`

### `GET /projects` — list (tenant-scoped automatically)
Response: `[{ "id", "name", "authority", "description", "created_at" }, ...]`

### `GET /projects/{project_id}` — detail
Same shape as one list item. `404` if not found or belongs to another tenant.

### `POST /projects/{project_id}/specs` — attach a spec library
Request: `{ "spec_document_id": "uuid" }`
Response: `{ "status": "attached" }` (idempotent — attaching twice is a no-op, not an error)

---

## 4. Submittals — the core workflow

Lifecycle: `CREATED` → `QUEUED` → `RUNNING` → `COMPLETED` | `FAILED` | `CANCELLED`
(`CANCELLED` is a legal status today but nothing can set it yet — see Gap G3.)

### `POST /projects/{project_id}/submittals` — create + get upload URLs
This is a two-step upload: create the submittal record (with declared file list), get back
one presigned S3 URL per file, then the browser PUTs bytes **directly to S3** (never through
the API).

Request:
```json
{
  "material_desc": "string?",
  "files": [ { "filename": "string", "declared_label": "string?" } ]
}
```
Response:
```json
{
  "submittal_id": "uuid",
  "uploads": [
    { "filename": "cover.pdf", "s3_key": "...", "upload_url": "https://s3....(presigned PUT)" }
  ]
}
```
**Design note:** the client must `PUT` with header `Content-Type: application/pdf` exactly —
the URL is signed against that content type.

### `POST /submittals/{submittal_id}/start` — kick off the review
Call once all files are uploaded to S3. `409` if not `CREATED`; `400` if zero files uploaded.
Response: `{ "status": "queued", "job_id": "uuid" }`

### `GET /submittals/{submittal_id}` — status + full report (the poll target)
```json
{
  "id": "uuid", "project_id": "uuid", "status": "COMPLETED",
  "material_desc": "string|null", "recommendation": "APPROVE"|"CONDITIONAL"|"RESUBMIT"|null,
  "report": { /* full ReviewReport JSON — see §6 */ } ,
  "error_message": "string|null",
  "created_at": "iso8601", "started_at": "iso8601|null", "completed_at": "iso8601|null"
}
```

### `GET /submittals/{submittal_id}/events` — per-stage progress, polled
```json
[ { "sequence_number": 0, "node_name": "doc_processor", "status": "complete", "created_at": "iso8601" }, ... ]
```
The 11 possible `node_name` values, in order: `doc_processor`, `completeness`, `boq_drawing`,
`spec_verifier`, `validity_checker`, then either `avl_check` (TAQA projects only) or
`skip_avl` (everyone else), then `statement`, `table_auditor`, `consistency`, `others`,
`report_compiler`. **Design note:** `doc_processor` is ~84% of total runtime — don't design a
progress bar that implies even spacing between stages; it will sit on stage 1 for minutes.

### `GET /submittals/{submittal_id}/stream` — Server-Sent Events alternative to polling
Same data as `/events` + `/{submittal_id}`, pushed over one held-open connection instead of
re-polling every 2s. Events: `node_complete` (one per stage), `status` (current status after
each tick), `done` (terminal — connection closes after this). **Design note:** the frontend
must consume this with `fetch()` + a manual stream reader, not native `EventSource` — Cognito
bearer tokens can't be sent via `EventSource`'s API.

### `GET /projects/{project_id}/submittals` — history list for a project
```json
[ { "id", "status", "material_desc", "recommendation", "created_at", "completed_at" }, ... ]
```

### `GET /submittals/{submittal_id}/citations` — the trust/citation view
`409` unless `status == COMPLETED`. This is what backs "click a finding, see the exact spec
clause and exact submittal page it came from" — the core trust mechanism per
`planning/07_ui_ux_spec.md`.
```json
[
  {
    "requirement_id": "string",
    "requirement_summary": "string",
    "status": "MET" | "NOT_MET" | "PARTIALLY_MET" | "...",
    "confidence": "high"|"medium"|"low",
    "reasoning": "string",
    "spec_citation": {
      "clause": "string", "text": "string", "page": 12,
      "view_url": "https://s3.../spec.pdf#page=12"
    },
    "evidence_citations": [
      { "document": "cover.pdf", "page": 3, "text": "string", "view_url": "https://s3.../cover.pdf#page=3" }
    ]
  }
]
```
`view_url` is a presigned S3 GET URL with a `#page=N` anchor — most PDF viewers (including
the browser's native one in an `<iframe>`) jump to that page automatically.

---

## 5. Specs (admin-facing — spec library management)

Specs are global reference data, not per-tenant — any `tenant_admin` can manage them.

### `POST /specs/upload-url` — get a presigned upload URL for a spec PDF
Request: `{ "authority": "ADM", "network": "irrigation", "filename": "spec.pdf" }`
Response: `{ "s3_key": "...", "upload_url": "https://..." }`

### `POST /specs/index` — register + enqueue indexing after upload completes
Request: `{ "authority": "ADM", "network": "irrigation", "filename": "spec.pdf", "s3_key": "..." }`
Response: `{ "spec_document_id": "uuid", "job_id": "uuid" }`
**Design note:** there is currently no way to poll indexing status from the API (`chunk_count`
is `null` until done, with no "indexing" vs "failed" distinction — Gap G5).

### `GET /specs` — list all indexed specs
```json
[ { "id", "authority", "network_name", "source_file", "chunk_count", "indexed_at" }, ... ]
```

---

## 6. Chat (post-review Q&A)

`409` unless the submittal is `COMPLETED`.

### `POST /submittals/{submittal_id}/chat`
Request: `{ "question": "string" }`
Response:
```json
{
  "answer": "string",
  "source": "spec_rag" | "submittal_rag" | "report_json",
  "source_references": [ /* shape varies by source — spec/submittal snippets or report excerpts */ ],
  "confidence": "high" | "medium" | "low"
}
```
`source` tells you which knowledge base grounded the answer — worth surfacing in the UI
("answered from the spec" vs "answered from your uploaded documents" vs "answered from the
review findings") so users understand what the assistant actually consulted.

### `GET /submittals/{submittal_id}/chat` — conversation history
```json
[ { "question", "answer", "route", "sources", "created_at" }, ... ]
```

---

## 7. The `ReviewReport` shape (embedded in `GET /submittals/{id}.report`)

This is the single most important object to design around — it's the whole review result.

```json
{
  "submittal_id": "string",
  "authority": "ADM" | "TAQA",
  "material_description": "string",
  "spec_clause": "string",
  "review_date": "YYYY-MM-DD",
  "completeness_findings": [ /* Finding[] */ ],
  "boq_drawing_findings": [ /* Finding[] */ ],
  "spec_verification_findings": [ /* Finding[] */ ],
  "validity_findings": [ /* Finding[] */ ],
  "avl_findings": [ /* Finding[] — empty for non-TAQA */ ],
  "statement_findings": [ /* Finding[] */ ],
  "table_audit_findings": [ /* TableRowFinding[] */ ],
  "consistency_findings": [ /* Finding[] */ ],
  "others_findings": [ /* Finding[] */ ],
  "critical_count": 0,
  "warning_count": 0,
  "missing_documents": [ "string" ],
  "overall_recommendation": "APPROVE" | "CONDITIONAL" | "RESUBMIT",
  "summary_comments": "string"
}
```

**`Finding`** (used in 8 of the arrays above):
```json
{ "stage": "string", "document": "string", "description": "string", "severity": "pass"|"warning"|"critical", "action_required": "string" }
```

**`TableRowFinding`** (table_audit_findings only — a row-by-row comparison table):
```json
{
  "parameter": "string", "specified_value": "string", "proposed_value": "string",
  "deviation_declared": "string", "measured_value": "string",
  "specified_correct": true, "proposed_verified": true, "measured_verified": true,
  "deviation_accurate": true, "missing_from_spec": false,
  "finding": "string", "severity": "pass" | "warning" | "critical"
}
```

**Design implication:** `severity` is a 3-value traffic light (`pass`/`warning`/`critical`)
across every finding type — this is what backs the "color-coded expandable sections" pattern
the original prototype used and is worth carrying forward. `overall_recommendation` is the
headline result; `critical_count`/`warning_count` are pre-computed, don't re-derive them
client-side.

---

## 8. Not built yet — design placeholders worth reserving screen space for

These are real, planned gaps (`notes/api.md` has the full detail and priority order) — not
inventing scope, just flagging what a design shouldn't assume already works:

- **G1 — Finding override** (`POST /submittals/{id}/findings/{finding_id}/override`): the
  UX spec calls this the "anti-trust-destroying" feature — a one-click dismiss/confirm on
  any individual finding. Findings currently have no stable ID to hang this off, so design
  for it, but the button won't be wireable until that lands.
- **G2 — Report PDF export**: `src/report/` already generates a branded PDF; the route to
  trigger it doesn't exist yet. Design an "Export PDF" action as an async job (spinner →
  download link), not an instant download.
- **G3 — Cancel / retry**: no way today to cancel a stuck `RUNNING` review or retry a
  `FAILED` one without re-uploading everything. Design these as real actions on the
  submittal detail screen even though the routes aren't wired yet.
- **G4 — Tenant/user admin**: onboarding a new client or teammate today requires direct
  database access — no self-serve "invite a teammate" or "create a tenant" screen exists.
  Worth a dedicated (super-admin-only) settings area in the design even though it's backed
  by nothing yet.
- **G5 — Spec detail/reindex/delete**: `chunk_count: null` currently means either "still
  indexing" or "failed" with no way to tell which. Design a spec detail view with an
  explicit status, not just a table row.
- **G6 — Revisions (resubmit cycle)**: UAE workflow is Rev 0 → "Revise & Resubmit" →
  contractor fixes → Rev 1, chained under one submittal. No schema or routes yet — if
  designing the submittal detail screen, leave room for a revision history/timeline, but
  don't wire it to real data.

---

## 9. Conventions to carry into any new screen/flow

1. Never collect or send `tenant_id`/`project_id` as a hidden field — it's always inferred
   server-side or taken from the URL path the user is already navigating.
2. Files never transit through the API — always presigned S3 URLs, both upload and view.
3. Any operation described as "long" in this doc (review, spec indexing, PDF export) is a
   background job — design it as submit-then-poll/stream, never a spinner blocking on a
   single request/response.
4. `404` on someone else's resource looks identical to "doesn't exist" — never design UI copy
   that could leak "that ID exists but isn't yours."
