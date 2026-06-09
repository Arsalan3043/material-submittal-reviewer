from __future__ import annotations

from src.config.adm_profile import ADMProfile
from src.models.submittal import DocType


class TAQAProfile(ADMProfile):
    """
    TAQA authority profile.
    Inherits the full ADM 10-item index and adds AVL and non-toxicity requirements.
    """

    @property
    def authority(self) -> str:
        return "TAQA"

    @property
    def requires_avl_check(self) -> bool:
        return True

    @property
    def requires_non_toxicity(self) -> bool:
        return True

    @property
    def extra_requirements(self) -> list[str]:
        return [
            "Approved Vendor List (AVL) verification required",
            "Non-toxicity certificates required (must be included in test reports)",
        ]

    @property
    def chroma_collection_name(self) -> str:
        return "taqa_specifications"


TAQA = TAQAProfile()
