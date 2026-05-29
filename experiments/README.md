# Experiments

All RAG and LLM component experiments run before any production code is written.
Results are tracked in `rag/comparison_results.csv`.

## RAG Experiment Sequence
1. `exp01_naive_rag` — Fixed 500-token chunks, semantic only (baseline)
2. `exp02_clause_chunking` — Clause-boundary chunking
3. `exp03_hybrid_search` — BM25 + semantic + RRF
4. `exp04_reranking` — Cohere reranking added
5. `exp05_metadata_filter` — Authority + clause metadata filtering added

## RAGAS Metrics Tracked
- `faithfulness` — Answer grounded in retrieved context?
- `answer_relevancy` — Answer relevant to question?
- `context_precision` — Retrieved context precise?
- `context_recall` — All relevant info retrieved?
