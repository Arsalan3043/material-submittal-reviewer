"""
Experiment 04 — Hybrid Search + Cohere Reranking

Run from project root:
    python experiments/rag/exp04_reranking/run.py

Requires COHERE_API_KEY in .env (free tier at cohere.com).
BM25 index built in memory on first query (~10s for 20k docs).
Results written to:
    experiments/rag/exp04_reranking/results.json
"""

import sys
sys.path.insert(0, ".")

from experiments.rag.exp04_reranking.pipeline import generate, retrieve
from experiments.rag.shared.evaluate import run_ragas_evaluation

DATASET_PATH = "experiments/data/spec_questions.json"
OUTPUT_PATH = "experiments/rag/exp04_reranking/results.json"

if __name__ == "__main__":
    print("=" * 55)
    print("Experiment 04 — Hybrid Search + Cohere Reranking")
    print("  Chunking  : fixed 500-char (same as exp01)")
    print("  Retrieval : BM25 top-20 + semantic top-20 → RRF → top-20")
    print("  Reranking : Cohere rerank-english-v3.0 → top 5")
    print("  Filtering : none (all networks)")
    print("=" * 55)
    print()

    run_ragas_evaluation(
        dataset_path=DATASET_PATH,
        retrieval_fn=retrieve,
        generation_fn=generate,
        output_path=OUTPUT_PATH,
        experiment_name="exp04_reranking",
    )
