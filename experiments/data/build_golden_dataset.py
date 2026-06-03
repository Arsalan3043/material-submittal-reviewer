"""
Builds the golden dataset for RAGAS evaluation.

Samples chunks from the local ChromaDB adm_specifications collection,
uses GPT-4o to extract Q&A pairs that are grounded in real spec text,
and writes them to spec_questions.json.

Run from the project root:
    python experiments/data/build_golden_dataset.py
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

import chromadb
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

CHROMA_LOCAL_PATH = "data/chromadb"
COLLECTION_NAME = "adm_specifications"
OUTPUT_PATH = Path("experiments/data/spec_questions.json")

# Target Q&A pairs total (GPT-4o filters out bad chunks)
TARGET_QA = 30

NETWORKS = ["irrigation", "road", "storm_water"]

# Queries designed to retrieve chunks with specific testable requirements
TECHNICAL_QUERIES = [
    "minimum compressive strength MPa concrete",
    "pipe material HDPE PVC diameter wall thickness pressure rating",
    "water pressure test leakage acceptance criteria",
    "aggregate grading sieve size percentage passing",
    "soil compaction density proctor test percent",
    "reinforcement steel yield tensile strength ASTM",
    "concrete slump workability admixture water cement ratio",
    "pipe joint fitting specification ASTM BS EN standard",
    "valve material body pressure rating testing",
    "coating thickness anticorrosion protection specification",
    "asphalt bitumen penetration grade temperature",
    "geotextile tensile strength permeability specification",
    "concrete cover reinforcement depth exposure class",
    "pump flow rate head efficiency motor power",
    "manhole cover load class material specification",
    "cable conductor size insulation voltage rating",
    "filter media gravel sand layer thickness",
    "irrigation drip emitter flow rate pressure",
    "drainage pipe gradient slope minimum velocity",
    "backfill material compaction layer thickness tolerance",
]

SYSTEM_PROMPT = """You are a construction specification expert reviewing ADM (Abu Dhabi Municipality) technical specifications.

Given a chunk of spec text, your job is to determine if it contains a SPECIFIC, TESTABLE requirement
that an engineer would verify during a material submittal review.

Good requirements:
- Specific material properties with values (e.g. "minimum compressive strength 35 MPa")
- Standards compliance requirements (e.g. "pipes shall conform to ASTM D3034")
- Specific test methods (e.g. "slump test per BS EN 12350-2")
- Dimensional requirements with tolerances
- Chemical composition requirements
- Physical property minimums/maximums

Bad chunks (return null):
- Table of contents, headers, page numbers
- Administrative procedures (submittals, scheduling, reporting)
- General quality statements without specific values
- Contractor responsibilities without measurable criteria
- Text that is too fragmented to understand

If the chunk has a good requirement, return a JSON object. Otherwise return null."""

USER_PROMPT_TEMPLATE = """Spec chunk (network: {network}, source: {source_file}):

---
{text}
---

If this contains a specific, testable requirement, return ONLY this JSON (no markdown):
{{
  "question": "A specific question an engineer would ask when reviewing a submittal",
  "ground_truth": "The exact answer from the spec text above (quote directly, add clause if visible)",
  "material_type": "the material or component being specified (e.g. concrete, hdpe_pipe, valve)",
  "spec_clause": "clause or section number if visible in the text, else 'unknown'",
  "relevant_clause": "most specific clause number visible, else 'unknown'"
}}

If the chunk is not suitable, return: null"""


class QAPair(BaseModel):
    question: str
    ground_truth: str
    material_type: str
    spec_clause: str
    relevant_clause: str


def _get_collection() -> chromadb.Collection:
    client = chromadb.PersistentClient(path=CHROMA_LOCAL_PATH)
    return client.get_collection(name=COLLECTION_NAME)


def _embed_queries(queries: list[str], client: OpenAI) -> list[list[float]]:
    """Embed query strings with text-embedding-3-small (same model used at index time)."""
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=queries,
    )
    return [r.embedding for r in response.data]


def _sample_chunks(collection: chromadb.Collection, openai_client: OpenAI) -> list[dict]:
    """
    Embed technical queries with OpenAI and use vector search to find chunks
    that contain specific testable requirements, avoiding TOC/admin chunks.
    """
    print("  Embedding technical queries...")
    query_embeddings = _embed_queries(TECHNICAL_QUERIES, openai_client)

    seen_ids: set[str] = set()
    all_chunks: list[dict] = []

    for query_vec in query_embeddings:
        for network in NETWORKS:
            result = collection.query(
                query_embeddings=[query_vec],
                n_results=3,
                where={"network": network},
                include=["documents", "metadatas"],
            )
            docs = result["documents"][0]
            metas = result["metadatas"][0]
            ids = result["ids"][0]

            for doc, meta, chunk_id in zip(docs, metas, ids):
                if chunk_id in seen_ids:
                    continue
                if len(doc) < 200:
                    continue
                seen_ids.add(chunk_id)
                all_chunks.append({"text": doc, "meta": meta})

    random.shuffle(all_chunks)
    return all_chunks


def _extract_qa(client: OpenAI, chunk: dict) -> QAPair | None:
    """Ask GPT-4o to extract a Q&A pair from a chunk. Returns None if not suitable."""
    prompt = USER_PROMPT_TEMPLATE.format(
        network=chunk["meta"].get("network", ""),
        source_file=chunk["meta"].get("source_file", ""),
        text=chunk["text"][:1500],  # cap at 1500 chars to control cost
    )

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=0,
        max_tokens=400,
    )

    content = response.choices[0].message.content.strip()

    if content.lower() == "null" or not content.startswith("{"):
        return None

    try:
        # Strip markdown code fences if present
        content = re.sub(r"^```(?:json)?\n?", "", content)
        content = re.sub(r"\n?```$", "", content)
        data = json.loads(content)
        return QAPair(**data)
    except Exception:
        return None


def build_dataset(target: int = TARGET_QA, seed: int = 42) -> list[dict]:
    random.seed(seed)

    collection = _get_collection()
    client = OpenAI()

    print(f"Sampling chunks from {COLLECTION_NAME}...")
    chunks = _sample_chunks(collection, client)
    print(f"Sampled {len(chunks)} candidate chunks across {len(NETWORKS)} networks")

    qa_pairs: list[dict] = []
    processed = 0
    skipped = 0

    for chunk in chunks:
        if len(qa_pairs) >= target:
            break

        processed += 1
        network = chunk["meta"].get("network", "")
        print(f"  [{processed}/{len(chunks)}] {network} — ", end="", flush=True)

        qa = _extract_qa(client, chunk)
        if qa is None:
            skipped += 1
            print("skipped")
            continue

        entry = {
            "id": f"Q{len(qa_pairs) + 1:03d}",
            "authority": "ADM",
            "network": network,
            "material_type": qa.material_type,
            "spec_clause": qa.spec_clause,
            "relevant_clause": qa.relevant_clause,
            "question": qa.question,
            "ground_truth": qa.ground_truth,
        }
        qa_pairs.append(entry)
        print(f"OK  [{qa.material_type}] — {qa.question[:60]}...")

    print(f"\nExtracted {len(qa_pairs)} Q&A pairs ({skipped} chunks skipped)")
    return qa_pairs


if __name__ == "__main__":
    print(f"Building golden dataset (target: {TARGET_QA} Q&A pairs)...")
    pairs = build_dataset(target=TARGET_QA)

    if not pairs:
        print("No Q&A pairs generated — check ChromaDB has data.")
    else:
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(OUTPUT_PATH, "w") as f:
            json.dump(pairs, f, indent=2, ensure_ascii=False)
        print(f"\nSaved {len(pairs)} Q&A pairs to {OUTPUT_PATH}")

        # Print breakdown by network
        from collections import Counter
        counts = Counter(p["network"] for p in pairs)
        print("\nBreakdown by network:")
        for net, count in sorted(counts.items()):
            print(f"  {net:<15} {count} questions")
