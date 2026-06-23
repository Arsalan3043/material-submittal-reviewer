from __future__ import annotations

from pathlib import Path

# Absolute project root — resolved relative to this file so it works regardless
# of the working directory when Streamlit or scripts are launched.
PROJECT_ROOT: Path = Path(__file__).parent.parent.parent

DATA_DIR:        Path = PROJECT_ROOT / "data"
CHROMA_PATH:     str  = str(DATA_DIR / "chromadb")
STORE_DIR:       Path = DATA_DIR / "knowledge_stores"
SPECS_DIR:       Path = DATA_DIR / "specs"
SUBMITTALS_DIR:  Path = DATA_DIR / "submittals"


def ensure_dirs() -> None:
    """Create all data subdirectories if they don't exist yet."""
    for d in (STORE_DIR, SPECS_DIR, SUBMITTALS_DIR):
        d.mkdir(parents=True, exist_ok=True)
