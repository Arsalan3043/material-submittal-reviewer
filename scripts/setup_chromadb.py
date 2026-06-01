"""
Run once to create the two permanent spec collections in ChromaDB Cloud.
Safe to re-run — uses get_or_create_collection.

Collections created:
  adm_specifications  — ADM authority spec clauses
  taqa_specifications — TAQA authority spec clauses
"""

import os
import sys
from dotenv import load_dotenv

load_dotenv()

_required = ["CHROMA_API_KEY", "CHROMA_TENANT", "CHROMA_DATABASE"]
_missing = [k for k in _required if not os.getenv(k)]
if _missing:
    print(f"Missing env vars: {', '.join(_missing)}")
    sys.exit(1)

import chromadb

COLLECTIONS = [
    {
        "name": "adm_specifications",
        "metadata": {
            "description": "ADM authority specification clauses — hierarchical parent+child chunks",
            "authority": "ADM",
        },
    },
    {
        "name": "taqa_specifications",
        "metadata": {
            "description": "TAQA authority specification clauses — hierarchical parent+child chunks",
            "authority": "TAQA",
        },
    },
]


def get_client() -> chromadb.HttpClient:
    return chromadb.HttpClient(
        host="api.trychroma.com",
        ssl=True,
        tenant=os.environ["CHROMA_TENANT"],
        database=os.environ["CHROMA_DATABASE"],
        headers={"x-chroma-token": os.environ["CHROMA_API_KEY"]},
    )


def setup_collections(client: chromadb.HttpClient) -> None:
    for spec in COLLECTIONS:
        collection = client.get_or_create_collection(
            name=spec["name"],
            metadata=spec["metadata"],
        )
        print(f"  {spec['name']} — {collection.count()} documents (ready)")


if __name__ == "__main__":
    print("Connecting to ChromaDB Cloud...")
    client = get_client()
    print(f"  tenant   : {os.environ['CHROMA_TENANT']}")
    print(f"  database : {os.environ['CHROMA_DATABASE']}")
    print("Creating collections...")
    setup_collections(client)
    print("ChromaDB setup complete.")
