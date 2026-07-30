# Handoff: Clause — AI submittal review for Gulf construction QC

## Overview

Clause is an AI-native QA/QC compliance service for GCC construction. A QC engineer uploads a
material submittal (manufacturer PDFs), an 11-stage pipeline reviews it against the project's
authority spec library (ADM / TAQA), and the engineer gets a compliance matrix where every
finding carries the exact spec clause and the page it came from, plus a draft transmittal.

This bundle covers the **web frontend**: authentication, project list, project register,
submittal upload, review progress, findings report with citations and per-finding override,
grounded Q&A, and the admin spec library. Both a laptop layout and a mobile layout are
specified for every screen.

The design goal that drove every decision: **eliminate typing**. A QC engineer reviewing 50
submittals a month should never fill in a form. Project, authority and clause scope come from
context; the material description is read from the cover page and is the only editable field
in the whole submit flow.

## About the Design Files

The files in this bundle are **design references created in HTML** — prototypes that show the
intended look, layout and behavior. They are **not production code to copy**. They use a
small in-house template runtime (`support.js`) that has nothing to do with your app.

Your task is to **recreate these designs in the target codebase's existing environment**,
using its established patterns, component library, router and data layer. A working Next.js
frontend already exists in `frontend/` per the API reference — extend/redesign that, don't
start over. If a component library is already in place (shadcn/ui, MUI, an internal kit), map
the elements described below onto it rather than hand-rolling new primitives.

Do **not** wire the prototype's fake data or fake timers into the app. `frontend-api-reference.md`
in this folder is the ground truth for real endpoints and JSON shapes.

## Fidelity

**High-fidelity.** Final colors, typography, spacing, radii, motion and copy. Recreate the UI
pixel-accurately using the codebase's existing libraries. Where this document and the HTML
disagree, this document wins (the HTML is a single-file prototype and takes some shortcuts —
e.g. it fakes responsiveness with a device toggle instead of media queries).

---

## Design Tokens

### Color

| Token | Hex | Use |
|---|---|---|
| `ink` | `#17181A` | Primary text, dark buttons, sidebar avatar text on light |
| `ink-2` | `#3E4145` | Body copy inside cards |
| `text-secondary` | `#5C5F63` | Table secondary cells, sidebar inactive labels |
| `text-muted` | `#8E8E93` | Meta text, column headers, helper copy |
| `text-faint` | `#A9A9AE` | Disabled text, placeholders |
| `accent` | `#1B4DFF` | Primary action, links, active nav, running state |
| `accent-hover` | `#0B3AE0` | Primary button hover |
| `accent-wash` | `#E9EDFF` | Accent chip background, running pill, hover on accent text rows |
| `canvas` | `#FAFAF9` | App background |
| `panel` | `#FFFFFF` | Cards, table body, inputs |
| `panel-2` | `#F7F7F5` | Table header, right rail, info blocks |
| `sidebar` | `#F2F2F0` | Left sidebar background |
| `line` | `#EAEAE7` | Card borders |
| `line-2` | `#ECECE9` | Chrome dividers (header, rail edge) |
| `line-3` | `#F1F1EE` | Table row separators |
| `border-input` | `#DDDCD8` | Input borders |
| `pass` | `#0E8A5F` / text `#0E7A55` / bg `#E6F4EE` | MET, APPROVE, uploaded |
| `warning` | `#B26A00` / text `#96591A` / bg `#FBF0DF` | PARTIAL, CONDITIONAL |
| `critical` | `#C62828` / text `#A82020` / bg `#FBE9E9` | NOT MET, RESUBMIT, errors |
| `verdict-conditional` | bg `#FBF3E4`, border `#EFDDBF`, bar `#B26A00`, title `#8A5310`, body `#7A5A28` | Conditional verdict banner |
| `verdict-approve` | bg `#E9F5EF`, border `#CBE7DA`, bar `#0E8A5F`, title `#0E7A55`, body `#33705A` | Approved verdict banner |
| `verdict-resubmit` | bg `#FBE9E9`, border `#F2CFCF`, bar `#C62828`, title `#A82020`, body `#7A3232` | Resubmit verdict banner |
| `toast-bg` | `#17181A`, action text `#7FA0FF`, dot `#3DD68C` | Toasts |

### Typography

- **UI / body:** Instrument Sans (Google Fonts), weights 400 / 500 / 600 / 700.
- **Data, codes, labels:** IBM Plex Mono, weights 400 / 500 / 600. Used for refs (`SUB-0148`),
  clause numbers, authority chips, column headers, spec/submitted values in the matrix, and
  all-caps eyebrow labels with `letter-spacing: .07–.10em`.
- Scale actually used: 40/34/30 (marketing + verdict headlines), 22/20/19 (screen titles),
  16/15 (section + card titles), 14/13.5/13 (body, table cells, buttons), 12.5/12 (meta),
  11/10 (mono labels, chips). Letter-spacing `-.01em` to `-.02em` on headings ≥19px.
- **Mobile minimum:** body 13px, inputs 15–16px (prevents iOS zoom), nav labels 10px, tap
  targets ≥44px.

### Spacing, radius, shadow, motion

- Spacing steps: 4, 6, 8, 10, 12, 14, 16, 18, 22, 26 px. Page padding: desktop 22–26px,
  mobile 18px. Card padding 14–18px. Gap between stacked cards 12–16px.
- Radius: chips/labels 5–7px, buttons and inputs 9–10px, cards 10–13px, modal 14px, app frame
  14px, pills/mobile 13–17px, circles 50%.
- Shadows: primary button `0 2px 6px rgba(27,77,255,.3)` (large: `0 3px 10px`);
  card lift on hover none (border color change instead); modal
  `0 24px 60px -20px rgba(0,0,0,.4)`; toast `0 12px 30px -12px rgba(0,0,0,.5)`;
  active sidebar item `0 1px 2px rgba(0,0,0,.04)`.
- Motion (all `ease`, all short): `riseIn` 300–350ms (opacity 0→1, translateY 10px→0) on route
  change; `slideUp` 220–300ms on toasts, modal, newly added file rows; `fadeIn` 180–300ms on
  overlays; `popIn` 250–300ms (scale .5→1.1→1) on stage checkmarks, verdict changes, error
  messages; `softPulse` 1.1–2.6s infinite on running indicators; `sweep` 1.8s linear infinite
  shimmer inside the progress bar while running; caret rotate 90° in 200ms; progress bar
  `width` transition 500ms; upload bar `width` transition 300ms; button/border color
  transitions 180–200ms. Respect `prefers-reduced-motion`: drop `sweep`, `softPulse`, and the
  entry animations; keep state changes instant.

---

## Screens / Views

Screen numbers match `frontend-api-reference.md` §0.

### 1. Login  (`/login`)

**Purpose:** Cognito email/password sign-in. No self-registration — accounts are
admin-provisioned, and the copy says so.

**Laptop layout:** full-bleed two-pane split. Left pane 52% width, `#17181A`, 52px padding,
`justify-content: space-between`: logo lockup at top (22px `#1B4DFF` rounded-6px square +
"Clause" 600/16 `#FAFAF9`); center block = headline 600/34, line-height 1.2, `-.02em`,
`#FAFAF9`, max-width 420px, `text-wrap: pretty` — "Material submittals, reviewed against ADM
and TAQA clauses in under five minutes." — plus supporting line 400/14/1.6 `#9A9A9F`,
max-width 400px: "Every finding carries the spec clause and the page it came from. If we can't
cite it, we don't say it."; bottom row = two stats (`4m 12s` / AVG FIRST PASS, `10 yrs` /
RETENTION READY), value 600/20 `#FAFAF9`, label mono 400/11.5 `#7E7E85`, 26px gap.
Right pane: centered 352px form column, 16px gaps.

**Form:** title "Sign in" 600/22; subtitle 400/13/1.5 `#7C7F84` — "Accounts are provisioned by
your tenant admin — there's no sign-up to fill in."; two fields, each = mono 500/11 uppercase
label (`WORK EMAIL`, `PASSWORD`, `.07em`, `#8E8E93`) + 42px input, 13px horizontal padding,
1px `#DDDCD8` border, radius 10, 14px text, focus border `#1B4DFF`; error block (radius 9,
`#FBE9E9` bg, `#A82020` text, 400/12.5, popIn); primary button 44px full width, `#1B4DFF`,
600/14, radius 10.

**Mobile layout:** single column, 26px/22px padding: 30px logo square, headline 600/26/1.2
"Sign in to Clause", 400/13.5 subtitle, two 52px inputs (radius 13, 16px text), error block,
54px primary button radius 14.

**Validation:** email must contain `@` → else "Enter the work email your admin provisioned.";
password ≥ 4 chars → else "Password must be at least 4 characters." Enter key submits from
either field. Real implementation: Cognito SDK, then `GET /me` to learn `role` for role-gated
UI (spec library is `tenant_admin` only).

### 2. Projects home  (`/projects`)

**Purpose:** pick a project; create one when there are none.

**Persistent app shell (all authenticated screens, laptop):**
- **Sidebar** 236px fixed, `#F2F2F0`, right border 1px `#E6E6E3`, padding 18px 14px, 16px gaps.
  Contents top→bottom: logo lockup (22px accent square + "Clause" 600/15); search field (34px
  tall, `#FFF`, 1px `#E2E2DF`, radius 9, 11px circle outline glyph, 13px input, `⌘K` hint mono
  500/11 `#A9A9AE` right-aligned — the hint may be dropped if you don't implement the command
  palette); `PROJECTS` mono 600/11 `.10em` `#8E8E93`; project rows (9px 10px padding, radius 9;
  **active** = `#FFF` + 1px `#DDDCD8` + `0 1px 2px rgba(0,0,0,.04)`, name 500/13 `#17181A`,
  authority mono 500/10 `#1B4DFF`; **inactive** = transparent, name 400/13 `#4E5054`, authority
  `#9A9A9F`, hover `#EAEAE7`), each row showing name + `AUTHORITY` + "N submittals";
  "+ New project" row 500/13 `#1B4DFF`, hover bg `#E9EDFF`; then `margin-top:auto` footer above
  a 1px `#E4E4E1` top border: "Spec library" + mono `ADMIN` badge (admins only), and the user
  row (24px accent circle with initials, email 400/12 truncated with ellipsis, "Reviewer"
  `#A9A9AE`, "Out" link `#1B4DFF`).
- **Header** 62px, bottom border 1px `#ECECE9`, 26px horizontal padding, space-between:
  left = optional "← Back" ghost button (28px, radius 8, 1px `#E4E4E1`) on sub-screens +
  breadcrumb title 600/16 `-.01em` + authority chip (20px tall, radius 5, `#E9EDFF` bg,
  `#1B4DFF`, mono 600/10, `.06em`); right = live "Review running · NN%" pill (32px, radius 9,
  `#E9EDFF`, accent 8px pulsing dot, 500/12 `#1B4DFF`, click → progress screen) shown only
  while a review is running, then the primary "New submittal" button (34px, radius 9, accent).

**Body:** section title "Your projects" 600/15 with right-aligned count "N active · tenant
Trojan Group" 400/12.5 `#8E8E93`; then a 3-column grid, 13px gap, of project cards: 17px
padding, radius 12, `#FFF`, 1px `#EAEAE7`, 11px gaps; top row = authority chip left +
"N NEED ACTION" critical chip right (only when > 0); name 600/15/1.35; footer "N submittals ·
last activity <when>" 400/12.5 `#8E8E93`. Hover: border → `#1B4DFF`, `translateY(-2px)`, 180ms.
Final grid cell is a dashed "+ New project" tile (1.5px dashed `#D8D7D2`, radius 12, hover
border accent + bg `#F7F8FF`) with sub-line "Name and authority — two fields."

**Empty state:** when `GET /projects` returns `[]`, skip the grid and show the create-project
form directly (per the API reference's "empty state pushes straight to project creation").

**Mobile:** the sidebar becomes a bottom tab bar (see §Responsive). Project cards stack full
width; project switching moves into a sheet opened from the header title.

**Data:** `GET /projects`. Card counts (`subs`, `action`) are not in the current API — either
add them server-side or omit the badges rather than faking them.

### 3. Create Project (modal)

**Purpose:** two fields, nothing else. Overlay `rgba(20,20,24,.42)`, fadeIn 180ms; panel 420px,
radius 14, `#FFF`, 22px padding, 14px gaps, slideUp 220ms; click-outside closes, inner click
stops propagation.

Contents: title "New project" 600/17 + subtitle "Two fields. Attach spec libraries once you're
inside." 400/12.5 `#7C7F84`; `PROJECT NAME` mono label + 40px input (placeholder "e.g.
Saadiyat Marina — Utilities P1"); `AUTHORITY` mono label + two segmented options in a flex row
— "ADM · Abu Dhabi Municipality" (flex 1) and "TAQA" (96px), each 40px tall, radius 8,
selected = `#17181A`/white, unselected = `#FFF` + 1px `#E4E4E1`/`#5C5F63`, mono-adjacent
600/12.5; helper "Set once at creation — it decides which clause library and whether AVL
checks run." 400/11.5 `#A9A9AE`; error block if name < 3 chars — "Give the project a name you
will recognise in the register."; footer right-aligned Cancel (ghost 38px) + "Create project"
(accent 38px). Enter submits.

**Data:** `POST /projects {name, authority}` → navigate into the new project (screen 4). Spec
attachment is a follow-up step inside the project (`POST /projects/{id}/specs`) — do not build
a multi-select or custom-spec upload here; the API doesn't support it.

### 4. Inside a project — submittal register  (`/projects/:id`)

**Purpose:** the audit record UAE engineers already keep, plus the entry point to a new review.

**Layout:** 22px/26px padding, 16px gaps, three bands:
1. **Stat strip** — 3 equal columns, 12px gap. Each: 14px 16px padding, radius 11, `#FFF`, 1px
   `#EAEAE7`, 6px gaps; mono 500/11 `.08em` `#8E8E93` label, 600/24 `-.02em` value, 400/12
   `#8E8E93` note. Content: `AWAITING YOUR ACTION` / 4 / "oldest 2 days"; `FIRST-PASS APPROVAL`
   / 68% / "up from 61% in June"; `AVG FIRST REVIEW` / 4m 12s / "vs 62 min manual".
2. **Toolbar** — "Submittal register" 600/13; filter pills (26px, radius 7; active `#17181A`
   white 500/12, inactive `#FFF` + 1px `#E4E4E1` `#5C5F63` 400/12): "All N", "Needs action N",
   "Last 30 days"; right-aligned "Retained 10 yrs · Law No. 7 of 2025" 400/12 `#8E8E93`.
3. **Table** — 1px `#EAEAE7`, radius 11, `#FFF`, clipped. Grid columns
   `104px 1fr 132px 140px 96px`, 12px gap, 16px horizontal padding. Header row: `#F7F7F5`, 10px
   vertical padding, bottom border `#EDEDEA`, mono 600/10 `.09em` `#8E8E93` —
   REF / MATERIAL / RESULT / FINDINGS / SUBMITTED. Body rows: 13px vertical padding, bottom
   border `#F1F1EE`, hover `#FBFBFA`, whole row clickable → findings report. Cells: ref mono
   500/12 `#5C5F63`; material 400/13 `#17181A`; result chip (24px, radius 6, mono 600/11 —
   APPROVE `#E6F4EE`/`#0E7A55`, CONDITIONAL `#FBF0DF`/`#96591A`, RESUBMIT `#FBE9E9`/`#A82020`);
   findings summary 400/12 `#5C5F63` ("2 critical, 1 warning" / "No findings"); date 400/12
   `#8E8E93`. Body scrolls; header stays.

**Empty / no-results state:** 44px padding, centered: "Nothing matches “<query>”" 600/14,
"Try a material name, a reference, or clear the filter." 400/12.5 `#8E8E93`, and a
"Clear search & filters" ghost button (30px, radius 8). fadeIn 250ms.

**Search:** the sidebar search field filters this table live on material text and ref
(case-insensitive substring). Combined with the active filter pill (AND).

**Mobile:** stat strip becomes 3 compact tiles (11px padding, radius 11, value 600/18, label
400/10); filter pills become 34px scrollable pills radius 17; each submittal becomes a card
(14px padding, radius 13, 9px gaps): ref + date row (mono 500/11 `#8E8E93` / 400/11 `#A9A9AE`),
material 500/14.5/1.35, then result chip (26px) + findings summary 400/11.5 `#6E7175`.

**Data:** `GET /projects/{id}`, `GET /projects/{id}/submittals`. Stats are not in the API today
— compute client-side from the submittal list or add an endpoint; don't ship invented numbers.

### 5. Submit a submittal  (`/projects/:id/submittals/new`)

**Purpose:** upload with as close to zero input as possible.

**Laptop layout:** centered 660px column, 15px gaps, scrollable.
1. Title "New submittal" 600/20 `-.015em` + subtitle 400/13/1.5 `#6E7175`: "Drop the PDFs.
   Project, authority and clause scope are already known — the material description is read
   from your cover page."
2. **Dropzone** — 1.5px dashed `#C3C9DE`, radius 13, background
   `linear-gradient(#F4F6FF,#FBFCFF)`, 26px padding, centered column, 9px gaps: 36px accent
   rounded-11px square with `0 4px 12px rgba(27,77,255,.28)` shadow, softPulse 2.6s; headline
   600/15 (states: "Drop the submittal PDFs here" → "Uploading…" → "All files uploaded");
   sub-line 400/12 `#7C7F84` ("or click to browse · PDF only · up to 20 files" → "3 files ·
   cover letter, datasheet, certificate"). Hover border → accent. Must accept real drag-drop
   as well as click-to-browse.
3. **File rows** (one per file, slideUp on insert) — 11px 14px padding, 1px `#EAEAE7`, radius
   10, `#FFF`, 12px gap: 26×32 PDF thumb (radius 4, `#F1F1EE`, 1px `#E2E2DF`, mono 600/8
   `#8E8E93` "PDF"); name 500/13; sub-line 400/11 `#8E8E93` — while uploading "4.8 MB ·
   uploading to encrypted storage", after "4.8 MB · detected as technical datasheet" (the
   detected label in `#0E7A55`); a 4px `#EDEDEA` track with accent fill (radius 99, width
   transition 300ms) while in flight; right side shows "NN%" mono 500/11 `#8E8E93` in flight,
   then "UPLOADED" mono 500/11 `#0E8A5F` with popIn.
4. **Material card** (appears once the cover page is parsed, slideUp) — 14px padding, 1px
   `#EAEAE7`, radius 10, `#FFF`: mono label `MATERIAL DESCRIPTION` + green
   `AUTO-FILLED FROM COVER PAGE` badge (18px, radius 4, `#E6F4EE`/`#0E7A55`, mono 600/9); a
   40px input prefilled with the parsed value, `#FCFCFB` bg, 1px `#E6E6E3`, radius 9, 14px
   text; helper "The only editable field on this screen." 400/11.5 `#A9A9AE`.
5. **Primary button** 44px, radius 10 — disabled (`#E6E6E3` bg, `#A9A9AE` text,
   `cursor: not-allowed`) with label "Add files to continue", then "Uploading…", then
   "Start review · ~4 min" in accent once every file is at 100%.
6. **Trust note** — 12px 14px, radius 10, `#F7F7F5`, 7px `#0E8A5F` dot, 400/12.5 `#6E7175`:
   "Files go straight to encrypted storage — never through our API — and the review runs in
   the background whether or not you stay on this page."

**Mobile:** dropzone becomes a card with two stacked 52px buttons — "Scan pages with camera"
(`#17181A`, primary on mobile because photographing a printed submittal is the common case) and
"Choose files" (white, 1px `#DDDCD8`); file rows and material card as above with 46px input at
15px text; sticky 54px primary button, radius 14.

**Data / sequence (must follow the API reference):**
1. `POST /projects/{id}/submittals` with `{material_desc?, files:[{filename, declared_label?}]}`
   → returns `submittal_id` + one presigned URL per file.
2. `PUT` each file **directly to S3** with header `Content-Type: application/pdf` exactly (the
   URL is signed against it). Never proxy bytes through the API. Per-file progress comes from
   the XHR/fetch upload progress event.
3. `POST /submittals/{id}/start` once every PUT succeeded → `{status:"queued", job_id}`.
   `409` = already started, `400` = zero files uploaded.
4. Material auto-fill is **not** an API feature today. Either implement cover-page parsing
   server-side and populate it, or ship the field empty with the placeholder text and drop the
   "auto-filled" badge. Do not fake it.
5. Never send `tenant_id` or `project_id` as a form field — path/token only.

### 6. Review in progress  (`/submittals/:id`)

**Purpose:** determinate, honest progress. Explicitly **not** a spinner, and explicitly
non-blocking.

**Laptop layout:** centered 700px column, 16px gaps.
- Header row: title 600/20 ("Reviewing your submittal" → "Review complete") + subtitle 400/13
  ("Eleven pipeline stages, weighted by how long they actually take. Nothing here blocks you."
  → "Eleven stages finished. The findings report is ready with clause citations on every row.")
  and right-aligned `SUB-0148 · REV 0` mono 500/12 `#1B4DFF`.
- **Progress card** — 18px padding, 1px `#EAEAE7`, radius 12, `#FFF`, 12px gaps: current stage
  label 600/13 + right "NN% · ~N min remaining" mono 500/12 `#8E8E93`; 8px track radius 99
  `#EDEDEA` with fill (accent while running, `#0E8A5F` when complete, width transition 500ms)
  and a 22% white gradient `sweep` shimmer overlay while running; then the **11-stage
  checklist**, one 7px-padded row each, 11px gap: status glyph (done = 17px `#0E8A5F` circle
  with white ✓, popIn; running = 17px 2px accent ring, softPulse; pending = 17px 1.5px
  `#DCDCD8` ring), label (running 600/12.5 `#17181A`, done 400/12.5 `#3E4145`, pending 400/12.5
  `#A9A9AE`), node name mono 400/11 `#B0B0B5`, and right-aligned weight mono 400/11 `#C9C9CE`
  in a 38px column.
- **Actions row:** "Leave and keep it running" (ghost 40px) always; "Cancel review" (40px,
  1px `#F2CFCF`, `#A82020`) while running; "Open findings report" (accent 40px, popIn) once
  complete.
- **Explainer block** — `#F7F7F5`, radius 10, 400/12.5 `#6E7175`: "Stage 1 reads and chunks
  every page — on a 60-page datasheet it holds about 84% of the total wait. The remaining ten
  stages finish in seconds."

**The 11 stages, in order, with the weights the bar must use:**

| # | `node_name` | Label | Weight |
|---|---|---|---|
| 1 | `doc_processor` | Reading and chunking documents | 84% |
| 2 | `completeness` | Completeness check | 2% |
| 3 | `boq_drawing` | BOQ and drawing cross-check | 2% |
| 4 | `spec_verifier` | Spec clause verification | 4% |
| 5 | `validity_checker` | Certificate validity | 1% |
| 6 | `avl_check` (TAQA) / `skip_avl` | AVL check — skipped (ADM project) | 0% |
| 7 | `statement` | Compliance statement | 1% |
| 8 | `table_auditor` | Comparison table audit | 3% |
| 9 | `consistency` | Cross-document consistency | 1% |
| 10 | `others` | Other observations | 1% |
| 11 | `report_compiler` | Compiling report | 1% |

Percent = sum of weights of *completed* stages. Never distribute progress evenly — the bar
would sit at 9% for minutes and look broken. Show a minimum 4% sliver as soon as the job is
running so the bar never reads as empty.

**Mobile:** same card at 16px padding, stage rows 9px padding with 18px glyphs and 13px labels,
one-line ETA "~4 min remaining · stage 1 holds ~84% of the wait. Safe to lock your phone.",
and the "Open findings report" button as a 52px full-width accent button on completion.

**Behavior:** navigating away must not cancel or pause anything — the header pill keeps the
percentage live from wherever the user is, and clicking it returns here. When the job reaches a
terminal state while the user is elsewhere, fire the toast (see §Interactions).

**Data:** prefer `GET /submittals/{id}/stream` (SSE-shaped) consumed with `fetch()` + a manual
`ReadableStream` reader — **not** `EventSource`, which cannot send the Cognito bearer token.
Events: `node_complete` per stage, `status`, terminal `done`. Fall back to polling
`GET /submittals/{id}/events` + `GET /submittals/{id}` every 2s if the stream drops. Cancel is
gap G3 — build the button and wire it when the route lands, or hide it behind a flag.

### 7. Findings report  (`/submittals/:id/report`) — the core screen

**Purpose:** the reviewed output. Every row must be traceable to a clause and a page.

**Laptop layout:** two columns inside the shell — main flex-1 (20px/24px padding, 13px gaps)
plus a 318px right rail (left border 1px `#ECECE9`, `#F7F7F5`).

**Main column, top → bottom:**
1. **Verdict banner** — 15px 17px padding, radius 12, palette per verdict (see tokens), 14px
   gap, `align-items: center`, background transitions 300ms when the verdict changes: a 4px
   full-height rounded bar in the verdict color; then title 700/19 `-.01em` ("Revise &
   resubmit" / "Approved with comments" / "Approved") + `SUB-0148 · REV 0` mono 500/12 in the
   title color; body 400/13/1.5 — "2 critical, 1 warning across 34 checked requirements." and,
   when the user has overridden anything, " · N findings overridden by you"; right side two
   34px buttons — "Export PDF" (white bg, 1px in the verdict border color, verdict title color)
   and "Draft transmittal" (`#17181A`, white).
2. **Scope-transparency line** — 400/12 `#8E8E93`, items separated by 1px×11px `#DCDCD8`
   dividers: "Checked against **ADM S-402 rev. 2024** · 34 requirements" · "AVL check skipped —
   not a TAQA project" · "N of 6 findings resolved by you". This line is a trust requirement,
   not decoration: it tells the engineer what was *not* checked.
3. **Compliance matrix** — 1px `#EAEAE7`, radius 12, `#FFF`, clipped, header pinned and body
   scrolling. Grid `1fr 168px 168px 104px 76px`, 12px gap, 16px horizontal padding. Header
   `#F7F7F5`, mono 600/10 `.09em` `#8E8E93`: PARAMETER / REQUIRED / SUBMITTED / STATUS /
   CLAUSE. Row: 12px vertical padding, border-bottom `#F1F1EE`, cursor pointer, background
   `#FBFBFA` when expanded. Cells: caret `›` 600/14 `#B0B0B5` rotating 90° in 200ms when open,
   then parameter 500/13 `#17181A` (line-through when dismissed); required and submitted values
   in **mono** 400/12 `#5C5F63` (mono matters — these are compared numerically by eye); status
   chip 22px radius 6 mono 600/10 (MET / PARTIAL / NOT MET / DISMISSED); clause mono 400/11
   `#1B4DFF`.
4. **Expanded citation panel** (per row, riseIn 250ms) — `#FCFCFB`, 14px 16px 16px 34px padding
   (indented under the caret), bottom border `#F1F1EE`, 12px gaps: two side-by-side cards
   (`#FFF`, 1px `#EAEAE7`, radius 9, 12px 14px):
   - **SPEC · ADM S-402 CL. 4.5, P.44** (mono 600/10 `.08em` `#8E8E93`), quoted clause text
     400/12.5/1.55 `#3E4145`, action "Open spec at page 44 ↗" 500/11 `#1B4DFF`.
   - **EVIDENCE · uPVC-DN300-datasheet.pdf, p.12**, quoted submittal text, "Open document at
     page 12 ↗".
   Below them the override row: "Confirm finding" and "Dismiss with note" chips (28px, radius
   8; selected = `#17181A`/white "Confirmed ✓" / "Dismissed ✓"; unselected = `#FFF` + 1px
   `#E4E4E1` `#5C5F63`), plus, once decided, "Confirmed — carried into the transmittal" /
   "Dismissed — excluded from the recommendation" 400/11.5 `#8E8E93` with an "undo" link.
   Both chips must `stopPropagation` so they don't collapse the row.

**Right rail:**
- **MISSING DOCUMENTS · N** — mono 600/11 `.08em` label, then one row per item: 5px `#C62828`
  dot + 400/12.5/1.45 `#3E4145`. Bottom border 1px `#E9E9E6`.
- **ASK ABOUT THIS SUBMITTAL** — scrolling message list: question bubbles `#17181A`/white,
  radius `10 10 10 3`, right-aligned, max-width 92%; answer bubbles `#FFF` + 1px `#E9E9E6`,
  radius `10 10 3 10`, 400/12.5/1.55 `#3E4145`; under each answer a source chip (20px, radius
  5, `#E9EDFF`/`#1B4DFF`, mono 600/9) reading `FROM SPEC LIBRARY` / `FROM YOUR DOCUMENTS` /
  `FROM REVIEW FINDINGS` — mapped from the API's `source` field (`spec_rag` /
  `submittal_rag` / `report_json`) — plus the confidence 400/11 `#8E8E93`. Typing indicator =
  three 6px `#B0B0B5` dots, softPulse staggered 0/.2/.4s, in an empty answer bubble.
  Footer: three suggestion chips (26px, radius 13, white, 1px `#E2E2DF`, hover accent border
  and text) — "Why resubmit?", "What is still missing?", "Is the SDR acceptable?" — then the
  composer: 38px row, `#FFF`, 1px `#E2E2DF`, radius 10, 12.5px input, 26px accent send square
  (radius 7, `↑`). Enter sends.

**Mobile:** verdict banner as a card (15px padding, radius 13, title 700/17, body 400/12.5);
one-line scope note 400/11 `#8E8E93`; each finding as a card (14px padding, radius 12, 9px
gaps): parameter 500/14 + status chip on one row, then `req … / got …` in mono 400/11.5/1.6,
then either the collapsed hint "CL 4.5 · P.44 · TAP FOR EVIDENCE" (mono 500/11 `#1B4DFF`) or,
expanded, the two evidence blocks as stacked `#F7F7F5` panels (radius 9, 11px 12px) and two
44px full-width Confirm / Dismiss buttons. Chat and missing-documents move to their own tab.

**Data:** `GET /submittals/{id}` (`report` = the `ReviewReport` object), plus
`GET /submittals/{id}/citations` for the clause/evidence panels (`409` until `COMPLETED`).
Render the matrix from `table_audit_findings` (`TableRowFinding[]`) and the eight `Finding[]`
arrays; use the precomputed `critical_count` / `warning_count` and `overall_recommendation` —
do not re-derive them client-side. `view_url` values are presigned S3 GETs with `#page=N`; open
them in an iframe or new tab and the native PDF viewer jumps to the page.

**Known gaps to design around (see §8 of the API reference):**
- **Override (G1)** — findings have no stable id yet, so Confirm/Dismiss cannot be persisted.
  Build the UI, keep the decision in local state, and disable/flag it until
  `POST /submittals/{id}/findings/{finding_id}/override` exists. **Important:** the prototype
  lets a dismissal change the headline verdict locally to demonstrate the interaction; in
  production the server owns `overall_recommendation` — show the user's overrides as an
  annotation ("N findings overridden by you") and only change the verdict once the backend
  recomputes it.
- **Export PDF (G2)** — async job: button → "Generating…" (disabled) → toast with a Download
  action. No instant download.
- **Revision timeline (G6)** — leave room under the verdict banner for a Rev 0 → Rev 1 chain
  but don't wire it.

### 8. Chat / Ask

Covered above as the report's right rail (and its own mobile tab). `POST /submittals/{id}/chat`
`{question}` → `{answer, source, source_references, confidence}`; `GET …/chat` for history on
mount. `409` until the submittal is `COMPLETED` — hide the composer and show "Ask opens when
the review finishes" instead. Always surface `source` and `confidence`; an answer with no
citation is the one thing this product must never show.

### 9. Auth edge states

- **Session expired / 401 anywhere** → clear tokens, redirect to Login with an inline note
  "Your session expired — sign in again." Do not lose the route; return to it after login.
- **`GET /me` 404 (no tenant)** → dedicated centered state, not a blank crash: title "Your
  account isn't attached to a tenant yet", body "Ask your admin to finish provisioning, then
  sign in again.", plus a "Sign out" ghost button. Same card styling as the login form.
- **404 on any resource** → "That submittal isn't available." Never say "it exists but isn't
  yours" — the API deliberately makes those indistinguishable.
- **409 on start** → "This review has already started" + link to the progress screen.
- **403 on spec index** → hide admin UI for non-admins rather than letting them hit it.

### 10. Spec library (admin)

**Purpose:** `tenant_admin` only. Upload and index authority spec PDFs; see what's indexed.

**Layout:** title "Spec library" 600/18 + subtitle "Authority specs indexed once, reused by
every project. Admin only." 400/13 `#6E7175`; then a table (same chrome as the register) with
grid `120px 1fr 150px 130px 120px`: AUTHORITY (mono 600/11 `#1B4DFF`), SOURCE FILE (400/13),
NETWORK (400/12.5 `#5C5F63`), CHUNKS (mono 400/12), STATUS chip — `INDEXED`
`#E6F4EE`/`#0E7A55`, `INDEXING` `#EDEDEA`/`#6E7175`.

**Data:** `GET /specs`, `POST /specs/upload-url`, `POST /specs/index`. G5: `chunk_count: null`
means "indexing **or** failed" with no way to distinguish — show `INDEXING` and add an explicit
status field server-side before this screen can be trusted. A spec detail view with real status
is worth building once that exists.

---

## Interactions & Behavior

**Navigation:** sidebar project → register; register row → report; "New submittal" → submit;
"Start review" → progress; progress completion or toast action → report; "← Back" → register.
Everything is a real route (deep-linkable, back-button correct). Mobile uses the same routes
behind a 4-item bottom tab bar.

**Toasts:** bottom center on laptop (translateX(-50%), 22px from bottom), full-width above the
tab bar on mobile (16px insets, 86px from bottom). `#17181A`, radius 11–13, 12px 16px padding,
7px `#3DD68C` dot, 500/13 `#FAFAF9` text, optional action label 600/13 `#7FA0FF`, slideUp
280ms, auto-dismiss after ~4.2s. Cases: "Review complete — 2 critical findings" + *Open
report*; "Project created — attach a spec library next"; "Report PDF ready" + *Download*;
"Review cancelled — your files are still uploaded".

**Form validation:** inline, on submit, never blocking-modal. Error text sits directly under
the relevant group in a `#FBE9E9` block, popIn 250ms, and clears on the next valid attempt.
Rules: login email contains `@`; login password ≥ 4; project name ≥ 3 chars; "Start review"
disabled until every file reports 100%.

**Loading states:** uploads = per-file determinate bars; review = the weighted 11-stage
checklist; chat = three-dot typing bubble; PDF export = button label "Generating…" + disabled;
table/report initial load = skeleton rows at the same row height (never a centered spinner —
the layout must not jump).

**Error states to build:** upload PUT failure (per-file "Retry" on the row, keep the others);
review `FAILED` (verdict banner in the critical palette with `error_message` and a "Retry
review" action — G3, so flag it); stream disconnect (fall back to polling silently, and only
surface "Reconnecting…" if both fail); chat failure ("Couldn't reach the assistant — try
again", retry link).

**Hover states:** table rows `#FBFBFA`; project cards border → accent + `translateY(-2px)`;
sidebar inactive rows `#EAEAE7`; primary buttons → `#0B3AE0`; ghost buttons → `#F4F4F2`;
suggestion chips → accent border and text; dropzone → accent border. Every interactive element
needs a visible `:focus-visible` ring (2px accent, 2px offset) — the prototype omits it and
production must not.

## Responsive behavior

The prototype fakes device switching with a toggle. **In production use real breakpoints, one
codebase, no separate mobile app.**

- **≥1024px (laptop/desktop):** persistent 236px sidebar; 3-column stat strip and project grid;
  data tables with all five columns; report = matrix + 318px right rail.
- **768–1023px (tablet):** sidebar collapses to icons or a slide-over; project grid → 2
  columns; report rail moves below the matrix as a full-width section; tables keep REF,
  MATERIAL, RESULT and drop FINDINGS/SUBMITTED into the row's second line.
- **<768px (phone):** bottom tab bar (Project / Submit / Review / Findings), 70px tall, `#FFF`,
  top border `#ECECE9`, 18px icon + 500/10 label, accent when active, ≥44px hit area including
  the safe-area inset; tables become cards; the right rail becomes a tab; primary actions
  become full-width 52–54px buttons pinned to the bottom of the scroll area; inputs ≥46px with
  ≥15px text.
- Never rely on hover alone for anything (the citation panel opens on tap, the whole row is the
  target); keep body text ≥13px and never truncate a clause number.

**On-site context (worth planning for, not yet designed):** a high-contrast/dark theme for
bright sunlight and glove use. Build colors as CSS variables from the start so this is a theme
swap, not a rewrite.

## State Management

Server state (React Query / SWR or equivalent): `me`, `projects`, `project`,
`projectSubmittals`, `submittal` (status + report), `submittalEvents`, `citations`, `chat`,
`specs`. Cache keys by id; invalidate `projectSubmittals` when a review completes.

Client state:
- `auth`: tokens, `role` (gates the spec library), redirect-after-login target.
- `activeProjectId`, `registerQuery`, `registerFilter` (`all` | `action` | `recent`).
- Upload: `files[{file, filename, declaredLabel, pct, status: idle|uploading|done|error}]`,
  `materialDesc`, derived `canStart = files.length && files.every(f => f.status === 'done')`.
- Review: `submittalId`, `status`, `completedStages[]` (derived percent), `streamState`.
  This must live above the route so it survives navigation — the header pill reads from it.
- Report: `expandedFindingId | null`, `overrides{findingId: 'confirmed'|'dismissed'}` (local
  until G1 lands), `exportState: idle|working|ready`.
- Chat: `messages[]`, `pending`, `draft`.
- `toast: {text, actionLabel, target} | null`.

Transitions worth naming: `canStart` false → true unlocks the primary button; `start` →
`running` mounts the stream and the header pill; terminal `done` → set `reviewDone`, invalidate
the submittal query, fire the toast; a `dismissed` override → annotate the verdict (and only
recompute it server-side).

## Assets

None. No images, no icon set, no illustrations. Every glyph in the prototype is a CSS shape
(rounded squares, rings, dots), a text arrow (`↑ ↗ › ← ✓ !`) or a mono label. When you
implement, substitute your codebase's icon library (Lucide, Heroicons, an internal set) at
16–18px with `currentColor` — do not recreate the CSS shapes. Fonts are Google Fonts:
Instrument Sans and IBM Plex Mono. The word "Clause" is a working product name; swap in the
real brand when it exists.

## Files

In this folder:
- `Clause Prototype.dc.html` — **the primary reference.** Full clickable prototype: login →
  projects → register → submit → weighted progress → findings report with citations, overrides
  and chat, plus the spec library and a mobile layout for every screen. Open it in a browser;
  it needs `support.js` beside it. Laptop/Mobile toggle top right, "Reset demo" to start over.
  Sign in with any email containing `@` and a 4+ character password; click the dropzone to
  simulate the upload.
- `Clause QC Review (3 directions).dc.html` — the earlier exploration: three visual/navigation
  directions (Quiet Precision / Site Console / Calm Review). Direction **1a (Quiet Precision)**
  was chosen and is what the prototype and this document describe. Keep it for context on
  rejected options; the mobile ergonomics (48px targets, camera-first upload) were pulled from
  1b, and the plain-language verdict headline from 1c.
- `support.js` — the prototype's template runtime. **Not part of the deliverable**; it only
  exists so the HTML files open. Do not port it.
- `frontend-api-reference.md` — the API inventory this design was built against. Ground truth
  for endpoints, JSON shapes, the upload sequence, the 11 stage names, and the gap list
  (G1–G6). Read it before wiring anything.
