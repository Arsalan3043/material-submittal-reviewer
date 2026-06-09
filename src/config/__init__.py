from __future__ import annotations

from src.config.adm_profile import ADM, ADMProfile
from src.config.base_profile import AuthorityProfile
from src.config.taqa_profile import TAQA, TAQAProfile

_PROFILES: dict[str, AuthorityProfile] = {
    "ADM": ADM,
    "TAQA": TAQA,
}


def get_authority_profile(authority: str) -> AuthorityProfile:
    if authority not in _PROFILES:
        raise ValueError(
            f"Unknown authority: {authority!r}. Supported: {list(_PROFILES.keys())}"
        )
    return _PROFILES[authority]


__all__ = [
    "AuthorityProfile",
    "ADMProfile",
    "TAQAProfile",
    "ADM",
    "TAQA",
    "get_authority_profile",
]
