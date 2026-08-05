"""
Tests for declared section labels.

Zero OpenAI, zero AWS. The important one is test_labels_match_the_classifier: this module
hardcodes the label vocabulary rather than importing a private name out of frozen src/, so a
test has to be what stops the two drifting apart.
"""

from __future__ import annotations

import pytest

from apps.api.section_labels import SECTION_LABELS, infer_declared_label


def test_labels_match_the_classifier():
    """SECTION_LABELS must equal the classifier's label map keys.

    If they diverge, declared labels still reach the classifier prompt and still help
    classification — but mislabelled-document detection silently stops working, because it
    looks the declared label up in this map and finds nothing. Silent, so it needs a test.
    """
    from src.parsers.classifier import _LABEL_TO_DOCTYPE

    assert set(SECTION_LABELS) == set(_LABEL_TO_DOCTYPE)


@pytest.mark.parametrize(
    "filename,expected",
    [
        # Real filenames from all four packages in Test Submittal/ — three different
        # naming conventions, because contractors don't share one.
        ("1. Material Source Declaration Form ( MSDF ).pdf", "Material Source Declaration Form"),
        ("01_Material Source Declaration Form (MSDF).pdf", "Material Source Declaration Form"),
        ("2. Copies of Relevant Parts of Specs..pdf", "Copies of Relevant Specifications"),
        ("2.0_Copies of relevant parts of specs.pdf", "Copies of Relevant Specifications"),
        ("2.1 BOQ.pdf", "BOQ & Drawings"),
        ("3. Technical Coparison.pdf", "Technical Comparison Table"),
        ("03_Technical Comparison.pdf", "Technical Comparison Table"),
        (
            "4. Manufacturer's Technical Data Original Catalogues.pdf",
            "Manufacturer's Technical Data",
        ),
        ("5. Recent Test Reports Certificates.pdf", "Recent Test Reports and Certificates"),
        (
            "6. Department of Economic Development ( Registration ).pdf",
            "Department of Economic Development (Registration)",
        ),
        (
            "7. Manufacturer Suppliers Guarantee ( as per Contract ).pdf",
            "Manufacturer/Supplier Guarantee",
        ),
        ("8. Previous Approvals.pdf", "Previous Approvals"),
        ("9. Applicator's Method Statement.pdf", "Applicator's Method Statement"),
    ],
)
def test_infers_real_filenames(filename, expected):
    assert infer_declared_label(filename) == expected


@pytest.mark.parametrize(
    "filename", ["2.2 Drawings.pdf", "2.2 Drawigns.pdf", "2.2_Drawigs.pdf", "2.2_Drawings.pdf"]
)
def test_drawings_are_left_unlabelled(filename):
    """Regression: labelling drawings emitted a false mismatch on every submittal.

    Index item 1 is "BOQ & Drawings" — one section, two doc types — but the classifier's
    label map resolves it to DocType.BOQ alone. A drawings file correctly classified as
    DocType.DRAWING then reads as misfiled, producing "Wrong document type in section
    'BOQ & Drawings'" every time. Observed on RHINF1-NPC-IR-MAT-0001_00. Leaving drawings
    unlabelled costs a hint and buys back a clean mismatch signal.

    (The last three are real misspellings from the test set — they must stay unlabelled too,
    not fall through to some other rule.)
    """
    assert infer_declared_label(filename) is None


@pytest.mark.parametrize(
    "filename", ["00. Cover Page.pdf", "10. Others.pdf", "10_Others (Please Specify).pdf"]
)
def test_unlabelled_sections_return_none(filename):
    """Cover page and Others map to no section. Guessing one would create a false
    mislabelled-document signal, which is worse than no label."""
    assert infer_declared_label(filename) is None


def test_guarantee_wins_over_manufacturer_technical_data():
    """'Manufacturer Suppliers Guarantee' contains 'manufacturer' — rule order is what keeps
    it out of the technical-data bucket, so it's worth pinning."""
    assert (
        infer_declared_label("7_Manufacturer - Suppliers Guarantee.pdf")
        == "Manufacturer/Supplier Guarantee"
    )


def test_unrecognised_filename_returns_none():
    assert infer_declared_label("scan_0001.pdf") is None
    assert infer_declared_label("") is None


def test_every_inferable_label_is_offered_in_the_picker():
    """Anything inference can produce must also be pickable, or the UI can't express a
    correction the server would otherwise make on its own."""
    from apps.api.section_labels import _FILENAME_RULES

    assert {label for _, label in _FILENAME_RULES} <= set(SECTION_LABELS)
