"""
Experiment 02 — Clause-Based Chunking

Run from project root:
    python experiments/rag/exp02_clause_chunking/run.py

Indexes specs into 'adm_clause' collection on first run (skips if already populated).
Results written to:
    experiments/rag/exp02_clause_chunking/results.json
"""

import sys
sys.path.insert(0, ".")

import chromadb
from experiments.rag.exp02_clause_chunking.pipeline import CHROMA_LOCAL_PATH, COLLECTION_NAME, generate, retrieve
from experiments.rag.shared.evaluate import run_ragas_evaluation
from experiments.rag.shared.load_spec import load_adm_specs

DATASET_PATH = "experiments/data/spec_questions.json"
OUTPUT_PATH = "experiments/rag/exp02_clause_chunking/results.json"


def _ensure_indexed() -> None:
    """Index specs with clause chunking if adm_clause collection is empty."""
    client = chromadb.PersistentClient(path=CHROMA_LOCAL_PATH)
    collection = client.get_or_create_collection(name=COLLECTION_NAME)
    count = collection.count()
    if count > 0:
        print(f"Collection '{COLLECTION_NAME}' already has {count} chunks — skipping indexing.")
        return

    print(f"Collection '{COLLECTION_NAME}' is empty — indexing specs with clause chunking...")
    totals = load_adm_specs(
        chunking_strategy="clause",
        collection_name=COLLECTION_NAME,
        overwrite=False,
    )
    total_chunks = sum(totals.values())
    print(f"Indexed {total_chunks} clause chunks across {len(totals)} networks.")


if __name__ == "__main__":
    print("=" * 55)
    print("Experiment 02 — Clause-Based Chunking")
    print("  Chunking  : clause boundary splits")
    print("  Retrieval : semantic only, top 5")
    print("  Reranking : none")
    print("  Filtering : none (all networks)")
    print("=" * 55)

    _ensure_indexed()

    run_ragas_evaluation(
        dataset_path=DATASET_PATH,
        retrieval_fn=retrieve,
        generation_fn=generate,
        output_path=OUTPUT_PATH,
        experiment_name="exp02_clause_chunking",
    )
