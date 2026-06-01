"""
RAGAS evaluation runner — shared across all 5 RAG experiments.

Usage in each experiment's run.py:

    from experiments.rag.shared.evaluate import run_ragas_evaluation

    scores = run_ragas_evaluation(
        dataset_path="experiments/data/spec_questions.json",
        retrieval_fn=my_retriever,
        generation_fn=my_generator,
        output_path="experiments/rag/exp01_naive_rag/results.json",
        experiment_name="exp01_naive_rag",
    )
"""

from __future__ import annotations

import json
import os
from typing import Callable

from datasets import Dataset
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas import evaluate
from ragas.metrics import (
    answer_relevancy,
    context_precision,
    context_recall,
    faithfulness,
)

load_dotenv()

RetrievalFn = Callable[[str], list[str]]
GenerationFn = Callable[[str, list[str]], str]


def _build_hf_dataset(
    questions: list[dict],
    retrieval_fn: RetrievalFn,
    generation_fn: GenerationFn,
) -> Dataset:
    rows: dict[str, list] = {
        "question": [],
        "answer": [],
        "contexts": [],
        "ground_truth": [],
    }

    for item in questions:
        question = item["question"]
        ground_truth = item["ground_truth"]

        contexts = retrieval_fn(question)
        answer = generation_fn(question, contexts)

        rows["question"].append(question)
        rows["answer"].append(answer)
        rows["contexts"].append(contexts)
        rows["ground_truth"].append(ground_truth)

    return Dataset.from_dict(rows)


def run_ragas_evaluation(
    dataset_path: str,
    retrieval_fn: RetrievalFn,
    generation_fn: GenerationFn,
    output_path: str,
    experiment_name: str = "",
) -> dict[str, float]:
    """
    Run RAGAS evaluation and write scores to output_path.

    Args:
        dataset_path:    Path to spec_questions.json golden dataset.
        retrieval_fn:    fn(question) -> List[str] of retrieved context chunks.
        generation_fn:   fn(question, contexts) -> answer string.
        output_path:     Where to write results.json.
        experiment_name: Label stored in results file.

    Returns:
        Dict with keys: faithfulness, answer_relevancy, context_precision, context_recall.
    """
    with open(dataset_path, "r") as f:
        questions: list[dict] = json.load(f)

    if not questions:
        raise ValueError(f"Golden dataset is empty: {dataset_path}")

    print(f"Loaded {len(questions)} questions from {dataset_path}")
    print("Running retrieval and generation...")

    dataset = _build_hf_dataset(questions, retrieval_fn, generation_fn)

    llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")

    print("Running RAGAS evaluation...")
    result = evaluate(
        dataset=dataset,
        metrics=[faithfulness, answer_relevancy, context_precision, context_recall],
        llm=llm,
        embeddings=embeddings,
    )

    scores = {
        "experiment": experiment_name,
        "faithfulness": round(float(result["faithfulness"]), 4),
        "answer_relevancy": round(float(result["answer_relevancy"]), 4),
        "context_precision": round(float(result["context_precision"]), 4),
        "context_recall": round(float(result["context_recall"]), 4),
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(scores, f, indent=2)

    print(f"Results written to {output_path}")
    _print_scores(scores)
    return scores


def _print_scores(scores: dict) -> None:
    print("\n--- RAGAS Scores ---")
    for metric in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        print(f"  {metric:<22} {scores[metric]:.4f}")
    print("--------------------\n")
