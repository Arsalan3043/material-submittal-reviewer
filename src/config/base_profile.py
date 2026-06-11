from __future__ import annotations

from abc import ABC, abstractmethod

from src.models.submittal import DocType


class AuthorityProfile(ABC):
    """
    Abstract base for authority-specific submittal requirements.
    Add a new authority by subclassing — never modify existing profiles.
    """

    @property
    @abstractmethod
    def authority(self) -> str:
        """Authority code used in ChromaDB metadata and report headers."""
        ...

    @property
    @abstractmethod
    def index_items(self) -> dict[int, str]:
        """Standard submittal index (item number → description)."""
        ...

    @property
    @abstractmethod
    def required_doc_types(self) -> list[DocType]:
        """Document types that must be present for a complete submittal."""
        ...

    @property
    def optional_doc_types(self) -> list[DocType]:
        """Document types that may or may not be present (not flagged as missing)."""
        return [DocType.PREVIOUS_APPROVAL, DocType.OTHERS]

    @property
    def requires_avl_check(self) -> bool:
        """Whether this authority requires Approved Vendor List verification."""
        return False

    @property
    def requires_non_toxicity(self) -> bool:
        """Whether this authority requires non-toxicity certificates."""
        return False

    @property
    def extra_requirements(self) -> list[str]:
        """Authority-specific requirements beyond the standard index (e.g. AVL, non-toxicity)."""
        return []

    @abstractmethod
    def get_max_doc_age_days(self, doc_type: DocType) -> int | None:
        """
        Maximum allowed age in days for a dated document.
        Returns None when there is no age limit (expiry date governs instead).
        """
        ...

    def is_required(self, doc_type: DocType) -> bool:
        return doc_type in self.required_doc_types

    @property
    def chroma_collection_name(self) -> str:
        return f"{self.authority.lower()}_specifications"

    # maf is a valid document for Index 8 (Previous Approvals) in UAE practice.
    # Experiment A proved this is a known convention, not a mismatch.
    INDEX_8_VALID_TYPES: frozenset[DocType] = frozenset(
        [DocType.PREVIOUS_APPROVAL, DocType.MAF]
    )
