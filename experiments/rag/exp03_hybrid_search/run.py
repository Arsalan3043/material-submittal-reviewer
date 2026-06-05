"""
Experiment 03 — Hybrid Search (BM25 + Semantic + RRF)

Run from project root:
    python experiments/rag/exp03_hybrid_search/run.py

Uses the same adm_specifications collection as exp01 (fixed 500-char chunks).
BM25 index is built in memory on first query — takes ~10s for 20k docs.
Results written to:
    experiments/rag/exp03_hybrid_search/results.json
"""

import sys
sys.path.insert(0, ".")

from experiments.rag.exp03_hybrid_search.pipeline import generate, retrieve
from experiments.rag.shared.evaluate import run_ragas_evaluation

DATASET_PATH = "experiments/data/spec_questions.json"
OUTPUT_PATH = "experiments/rag/exp03_hybrid_search/results.json"

if __name__ == "__main__":
    print("=" * 55)
    print("Experiment 03 — Hybrid Search (BM25 + Semantic + RRF)")
    print("  Chunking  : fixed 500-char (same as exp01)")
    print("  Retrieval : BM25 top-20 + semantic top-20 → RRF → top 5")
    print("  Reranking : none")
    print("  Filtering : none (all networks)")
    print("=" * 55)
    print()

    run_ragas_evaluation(
        dataset_path=DATASET_PATH,
        retrieval_fn=retrieve,
        generation_fn=generate,
        output_path=OUTPUT_PATH,
        experiment_name="exp03_hybrid_search",
    )
