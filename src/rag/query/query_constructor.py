from __future__ import annotations

import re
from dataclasses import dataclass

# Maps spec clause prefixes to network labels used in ChromaDB metadata.
# These match the 'network' field stored during indexing (exp05 filter key).
# Extend this map as new spec books are indexed.
_CLAUSE_TO_NETWORK: dict[str, str] = {
    # Irrigation / water distribution
    "33 10": "irrigation",
    "33 12": "irrigation",
    "33 14": "irrigation",
    "33 16": "irrigation",
    "33 30": "irrigation",
    "33 40": "storm_water",
    "33 41": "storm_water",
    "33 42": "storm_water",
    "33 44": "storm_water",
    "33 46": "storm_water",
    # Road / pavement
    "32 01": "road",
    "32 11": "road",
    "32 12": "road",
    "32 13": "road",
    "32 14": "road",
    "32 16": "road",
    "32 17": "road",
    # Legacy 5-digit format
    "02": "site_work",
    "03": "concrete",
    "07": "waterproofing",
    "22": "plumbing",
    "23": "hvac",
    "26": "electrical",
}


@dataclass
class StructuredQuery:
    question: str
    authority: str
    network: str          # ChromaDB metadata filter value (for BM25 corpus)
    clause_hint: str      # normalized section number (e.g. "02810") for BM25 terms
    metadata_filter: dict # ChromaDB where= dict passed to semantic search


def normalize_clause_ref(raw: str) -> str:
    """
    Extract the numeric section number from a freeform cover page clause reference.

    Cover pages vary widely:
      "ADM Specs Div-02-Section 02810"  →  "02810"
      "Section 03600"                   →  "03600"
      "Clause 33 40 00"                 →  "33 40 00"
      "Chapter 2: Earthworks"           →  "2"
      "07100"                           →  "07100"

    When no numeric identifier is found (e.g. "Earthworks" only), the
    normalised text is returned as-is in lowercase so build_query() can
    use it as a chapter_name metadata filter.
    """
    if not raw:
        return raw

    # Strip common non-numeric label words so the number search below is unambiguous.
    # Includes "Chapter/Ch" for road specs.
    stripped = re.sub(
        r'\b(ADM\s+specs?|TAQA\s+specs?|Division|Section|Clause|Chapter|Div|Sec|Cl|Ch)\b\s*[-:]*\s*',
        ' ', raw, flags=re.IGNORECASE,
    )
    stripped = stripped.replace("-", " ").strip()

    # Modern space-separated 6-digit format: "33 40 00"
    m = re.search(r'\b(\d{2})\s+(\d{2})\s+(\d{2})\b', stripped)
    if m:
        return f"{m.group(1)} {m.group(2)} {m.group(3)}"

    # Legacy 5-digit format: "02810", "03600", "07100"
    m = re.search(r'\b(\d{5})\b', stripped)
    if m:
        return m.group(1)

    # 4-digit
    m = re.search(r'\b(\d{4})\b', stripped)
    if m:
        return m.group(1)

    # 2-digit (e.g. chapter number "2" or division "02")
    m = re.search(r'\b(\d{1,2})\b', stripped)
    if m:
        return m.group(1)

    # No digits at all — likely a text-only road chapter name like "earthworks".
    # Return lowercase normalised so build_query can use it as chapter_name filter.
    return re.sub(r'\s+', '_', stripped.strip().lower())


def build_query(
    spec_clause: str,
    material_description: str,
    authority: str,
) -> StructuredQuery:
    """
    Build a structured retrieval query from the cover page clause reference.

    Normalises the raw clause text extracted by the LLM (which may include
    verbose prefixes like "ADM Specs Div-02-Section 02810") to a clean section
    number ("02810") and uses it as both the BM25 keyword hint and the primary
    ChromaDB metadata filter.  This avoids the previous failure mode where
    "ADM Specs Div-02-Section 02810" was passed directly to _resolve_network
    and the digit-concatenation fallback returned "section_02" — a network
    label that matched zero stored chunks.
    """
    normalized = normalize_clause_ref(spec_clause)
    network = _resolve_network(normalized)

    question = (
        f"What are the technical requirements for {material_description} "
        f"per specification clause {normalized}?"
    )
    if normalized:
        question += f" Clause {normalized}."

    # Choose the most precise metadata filter available:
    #   ≥4-digit number  →  {"section": "02810"}   precise section filter
    #   1-2 digit only   →  {"network": network}    division-level scope
    #   text only        →  {"chapter_name": "earthworks"}  road chapter by name
    if re.search(r'\d{4,}', normalized):
        metadata_filter: dict = {"section": normalized}
    elif re.search(r'\d', normalized):
        metadata_filter = {"network": network}
    else:
        # Text-only: road chapter name (e.g. "earthworks")
        metadata_filter = {"chapter_name": normalized}

    return StructuredQuery(
        question=question,
        authority=authority,
        network=network,
        clause_hint=normalized,
        metadata_filter=metadata_filter,
    )


def _resolve_network(clause: str) -> str:
    """
    Resolve a *normalised* clause number to a network label for BM25 corpus
    filtering.  Input should already be the clean numeric form (e.g. "02810"),
    not the raw cover page text.
    """
    clause_clean = clause.strip().upper()
    for prefix, network in _CLAUSE_TO_NETWORK.items():
        if clause_clean.startswith(prefix.upper()):
            return network
    # Fallback: derive a coarse key from first two digits
    digits = "".join(c for c in clause_clean if c.isdigit())
    if digits:
        return f"section_{digits[:2]}"
    return "general"
