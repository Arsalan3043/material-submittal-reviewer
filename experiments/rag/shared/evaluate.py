"""
RAGAS-compatible evaluation runner using direct OpenAI calls.

Implements the same 4 metrics as RAGAS but with the openai Python client
directly, avoiding LangChain's async HTTP issues on macOS.

Metrics:
  faithfulness       - fraction of answer claims supported by retrieved context
  answer_relevancy   - how well the answer addresses the question
  context_precision  - fraction of retrieved chunks relevant to the question
  context_recall     - fraction of ground truth covered by retrieved context

Usage:
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

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

RetrievalFn = Callable[[str], list[str]]
GenerationFn = Callable[[str, list[str]], str]

_MODEL = "gpt-4o-mini"
_client: OpenAI | None = None


def _openai() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


def _ask(prompt: str, max_tokens: int = 10) -> str:
    response = _openai().chat.completions.create(
        model=_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content.strip()


def _parse_float(text: str) -> float | None:
    """Extract the first float found in text, return None if unparseable."""
    import re
    match = re.search(r"\d+\.?\d*", text)
    try:
        val = float(match.group()) if match else None
        return val if val is not None and 0.0 <= val <= 1.0 else None
    except (ValueError, TypeError):
        return None


# -- Four RAGAS metrics -------------------------------------------------------

def _faithfulness(question: str, answer: str, contexts: list[str]) -> float:
    """Fraction of answer claims that are grounded in the retrieved context."""
    ctx = "\n\n".join(f"[{i+1}] {c[:600]}" for i, c in enumerate(contexts))
    score = _parse_float(_ask(
        f"Context:\n{ctx}\n\nAnswer: {answer}\n\n"
        "On a scale 0.0–1.0, what fraction of the claims in the answer are "
        "directly supported by the context? Return ONLY a decimal number."
    ))
    return score if score is not None else 0.0


def _answer_relevancy(question: str, answer: str) -> float:
    """How well the answer addresses the question."""
    score = _parse_float(_ask(
        f"Question: {question}\nAnswer: {answer}\n\n"
        "On a scale 0.0–1.0, how relevant and complete is this answer to the question? "
        "Return ONLY a decimal number."
    ))
    return score if score is not None else 0.0


def _context_precision(question: str, contexts: list[str]) -> float:
    """Fraction of retrieved chunks that are relevant to the question."""
    if not contexts:
        return 0.0
    relevant = 0
    for ctx in contexts:
        answer = _ask(
            f"Question: {question}\n\nContext: {ctx[:500]}\n\n"
            "Does this context contain information useful for answering the question? "
            "Reply with only: yes or no",
            max_tokens=5,
        )
        if "yes" in answer.lower():
            relevant += 1
    return relevant / len(contexts)


def _context_recall(question: str, ground_truth: str, contexts: list[str]) -> float:
    """Fraction of the ground truth that is covered by retrieved contexts."""
    ctx = "\n\n".join(f"[{i+1}] {c[:600]}" for i, c in enumerate(contexts))
    score = _parse_float(_ask(
        f"Ground truth answer: {ground_truth}\n\nRetrieved context:\n{ctx}\n\n"
        "On a scale 0.0–1.0, what fraction of the ground truth information "
        "is present in the retrieved context? Return ONLY a decimal number."
    ))
    return score if score is not None else 0.0


# -- Main runner --------------------------------------------------------------

def run_ragas_evaluation(
    dataset_path: str,
    retrieval_fn: RetrievalFn,
    generation_fn: GenerationFn,
    output_path: str,
    experiment_name: str = "",
) -> dict[str, float | None]:
    """
    Run evaluation and write scores to output_path.

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

    totals = {
        "faithfulness": 0.0,
        "answer_relevancy": 0.0,
        "context_precision": 0.0,
        "context_recall": 0.0,
    }

    for i, item in enumerate(questions, 1):
        q = item["question"]
        gt = item["ground_truth"]

        contexts = retrieval_fn(q)
        answer = generation_fn(q, contexts)

        f_score = _faithfulness(q, answer, contexts)
        ar_score = _answer_relevancy(q, answer)
        cp_score = _context_precision(q, contexts)
        cr_score = _context_recall(q, gt, contexts)

        totals["faithfulness"] += f_score
        totals["answer_relevancy"] += ar_score
        totals["context_precision"] += cp_score
        totals["context_recall"] += cr_score

        print(
            f"  [{i:02d}/{len(questions)}] "
            f"F={f_score:.2f} AR={ar_score:.2f} CP={cp_score:.2f} CR={cr_score:.2f}  "
            f"{q[:55]}..."
        )

    n = len(questions)
    scores = {
        "experiment": experiment_name,
        "faithfulness": round(totals["faithfulness"] / n, 4),
        "answer_relevancy": round(totals["answer_relevancy"] / n, 4),
        "context_precision": round(totals["context_precision"] / n, 4),
        "context_recall": round(totals["context_recall"] / n, 4),
    }

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(scores, f, indent=2)

    print(f"\nResults written to {output_path}")
    _print_scores(scores)
    return scores


def _print_scores(scores: dict) -> None:
    print("\n--- Evaluation Scores ---")
    for metric in ("faithfulness", "answer_relevancy", "context_precision", "context_recall"):
        val = scores[metric]
        display = f"{val:.4f}" if val is not None else "failed"
        print(f"  {metric:<22} {display}")
    print("-------------------------\n")
