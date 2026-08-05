"""
Declared section labels for submittal files.

Why this exists
---------------
Each uploaded PDF can carry a `declared_label` — the uploader saying "this file is the
Technical Comparison Table". The label is injected into the classifier prompt as
`[Submitted under section: '...']` (src/parsers/classifier.py::classify_document).

It is not a nicety. Real submittals are scanned, so their text comes from OCR, and every
document in a package opens with the same project letterhead. classify_uploaded_file reads
only max_pages=2, so without a label the classifier sees near-identical boilerplate on every
file and returns others/low for all of them. Downstream, the completeness check then reports
every document type missing and the review comes back RESUBMIT with a dozen false criticals —
confidently, with no error. That was measured on all four packages in `Test Submittal/`, not
hypothesised.

The old Streamlit upload page (app/pages/upload.py, removed in 92edd21) had a per-file
dropdown for this. The Next.js rewrite dropped it and sent `{filename}` only, so every
production upload since then has gone in unlabelled. This module is the fix, in two parts:

1. `infer_declared_label()` — a filename fallback applied server-side whenever a client
   sends no label, so correctness does not depend on the UI remembering to ask.
2. `SECTION_LABELS` — the vocabulary the picker offers, kept equal to the classifier's own
   label map so a declared label always means something to the pipeline.

Labels must be the canonical strings in classifier._LABEL_TO_DOCTYPE. Anything else still
helps classification (it lands in the prompt as free text) but silently disables
mislabelled-document detection, which compares the declared label to the detected type.
tests/unit/test_section_labels.py asserts this file and the classifier cannot drift apart.
"""

from __future__ import annotations

# Canonical section labels, in submittal index order. Equal to the keys of
# classifier._LABEL_TO_DOCTYPE — enforced by test, not by import, so that a private name in
# frozen src/ isn't load-bearing here.
SECTION_LABELS: list[str] = [
    "BOQ & Drawings",
    "Copies of Relevant Specifications",
    "Technical Comparison Table",
    "Manufacturer's Technical Data",
    "Recent Test Reports and Certificates",
    "Department of Economic Development (Registration)",
    "Manufacturer/Supplier Guarantee",
    "Previous Approvals",
    "Applicator's Method Statement",
    "Material Approval Form",
    "Material Source Declaration Form",
]

# Filename keywords → canonical label, most specific first; first substring match wins.
# Order matters: "Manufacturer Suppliers Guarantee" contains "manufacturer", so the guarantee
# rule has to be tested before the technical-data one.
_FILENAME_RULES: list[tuple[tuple[str, ...], str]] = [
    (("guarantee", "warranty"), "Manufacturer/Supplier Guarantee"),
    (("method statement",), "Applicator's Method Statement"),
    (("previous approval",), "Previous Approvals"),
    (("economic development", "ded registration", "ded_registration"),
     "Department of Economic Development (Registration)"),
    (("test report", "test certificate"), "Recent Test Reports and Certificates"),
    (("technical data", "catalogue", "catalog", "datasheet"),
     "Manufacturer's Technical Data"),
    (("comparison", "coparison"), "Technical Comparison Table"),
    (("boq", "bill of quantit"), "BOQ & Drawings"),
    (("relevant parts of spec", "copies of relevant", "specification"),
     "Copies of Relevant Specifications"),
    (("source declaration", "msdf"), "Material Source Declaration Form"),
    (("material approval form", "maf"), "Material Approval Form"),
    # Cover page and "Others" deliberately have no canonical label: they map to no section,
    # and forcing one would create a false mismatch signal.
    #
    # Drawings are deliberately absent too, for the same reason. Submittal index item 1 is
    # "BOQ & Drawings" — one section holding two document types — but _LABEL_TO_DOCTYPE maps
    # that single label to DocType.BOQ alone. So labelling a drawings file "BOQ & Drawings"
    # makes the classifier's correct answer (DocType.DRAWING) look like a mismatch, and emits
    # "Wrong document type in section 'BOQ & Drawings'" on essentially every submittal.
    # Measured on RHINF1-NPC-IR-MAT-0001_00 before this was removed.
    #
    # A standing false warning is worse than a missing hint: it trains reviewers to ignore
    # the mismatch signal, which is the one thing that catches genuinely misfiled documents.
    # Fixing this properly means letting one label map to several doc types, which lives in
    # frozen src/ — see notes/12_pipeline_findings.md F4.
]


def infer_declared_label(filename: str) -> str | None:
    """Best-effort section label from a filename. None when nothing matches confidently."""
    lowered = filename.lower()
    for keywords, label in _FILENAME_RULES:
        if any(keyword in lowered for keyword in keywords):
            return label
    return None
