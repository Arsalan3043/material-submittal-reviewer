from __future__ import annotations

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
    network: str          # ChromaDB metadata filter value
    clause_hint: str      # included in the question string for BM25 matching
    metadata_filter: dict # ChromaDB where= dict


def build_query(
    spec_clause: str,
    material_description: str,
    authority: str,
) -> StructuredQuery:
    """
    Build a structured retrieval query from the cover page clause reference.
    This is the production equivalent of the exp05 retrieve(question, item) call
    where item contained the 'network' label.
    """
    network = _resolve_network(spec_clause)

    question = (
        f"What are the technical requirements for {material_description} "
        f"per specification clause {spec_clause}?"
    )
    if spec_clause:
        question += f" Clause {spec_clause}."

    return StructuredQuery(
        question=question,
        authority=authority,
        network=network,
        clause_hint=spec_clause,
        metadata_filter={"network": network},
    )


def _resolve_network(clause: str) -> str:
    """Resolve a clause reference to a network label for metadata filtering."""
    clause_clean = clause.strip().upper().replace("-", " ")
    for prefix, network in _CLAUSE_TO_NETWORK.items():
        if clause_clean.startswith(prefix.upper()):
            return network
    # Fall back: use first two digits as a coarse network key
    digits = "".join(c for c in clause_clean if c.isdigit())
    if digits:
        return f"section_{digits[:2]}"
    return "general"
