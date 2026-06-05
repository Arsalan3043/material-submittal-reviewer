"""
Experiment 05 — Hybrid Search + Cohere Reranking + Metadata Filtering

Run from project root:
    python experiments/rag/exp05_metadata_filter/run.py

Requires COHERE_API_KEY in .env.
Per-network BM25 indexes built on first run (~10s, then cached).
Results written to:
    experiments/rag/exp05_metadata_filter/results.json
"""

import sys
sys.path.insert(0, ".")

from experiments.rag.exp05_metadata_filter.pipeline import generate, retrieve
from experiments.rag.shared.evaluate import run_ragas_evaluation

DATASET_PATH = "experiments/data/spec_questions.json"
OUTPUT_PATH = "experiments/rag/exp05_metadata_filter/results.json"

if __name__ == "__main__":
    print("=" * 60)
    print("Experiment 05 — Hybrid Search + Reranking + Metadata Filter")
    print("  Chunking  : fixed 500-char (same as exp01)")
    print("  Retrieval : BM25 top-20 + semantic top-20 (filtered by network)")
    print("              → RRF → top-20")
    print("  Reranking : Cohere rerank-english-v3.0 → top 5")
    print("  Filtering : where={network: <from dataset>} before semantic search")
    print("=" * 60)
    print()

    run_ragas_evaluation(
        dataset_path=DATASET_PATH,
        retrieval_fn=retrieve,
        generation_fn=generate,
        output_path=OUTPUT_PATH,
        experiment_name="exp05_metadata_filter",
    )
