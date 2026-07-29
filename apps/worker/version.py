"""
Pipeline version tag written onto every submittal row (submittals.pipeline_version).

Deliberately NOT in src/config/ — CLAUDE.md permits editing only src/parsers/file_io.py
inside src/; everything worker-specific lives under apps/worker/ instead.

Bump this whenever a change to src/agents/, src/rag/, or src/rules/ could change review
output, so past reports stay attributable to the pipeline version that produced them.
"""

PIPELINE_VERSION = "v1"
