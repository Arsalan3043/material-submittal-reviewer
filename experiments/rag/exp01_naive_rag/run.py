"""
Experiment 01 — Naive RAG Baseline

Run from project root:
    python experiments/rag/exp01_naive_rag/run.py

Results written to:
    experiments/rag/exp01_naive_rag/results.json
"""

import sys
sys.path.insert(0, ".")

from experiments.rag.exp01_naive_rag.pipeline import generate, retrieve
from experiments.rag.shared.evaluate import run_ragas_evaluation

DATASET_PATH = "experiments/data/spec_questions.json"
OUTPUT_PATH = "experiments/rag/exp01_naive_rag/results.json"

if __name__ == "__main__":
    print("=" * 55)
    print("Experiment 01 — Naive RAG Baseline")
    print("  Chunking  : fixed 500 chars")
    print("  Retrieval : semantic only, top 5")
    print("  Reranking : none")
    print("  Filtering : none (all networks)")
    print("=" * 55)

    run_ragas_evaluation(
        dataset_path=DATASET_PATH,
        retrieval_fn=retrieve,
        generation_fn=generate,
        output_path=OUTPUT_PATH,
        experiment_name="exp01_naive_rag",
    )
