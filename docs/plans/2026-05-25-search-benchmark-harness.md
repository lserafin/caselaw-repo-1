# Search Benchmark Harness Baseline

**Status:** Proposed  
**Date:** 2026-05-25  
**Scope:** Search evaluation only; no ranking or retrieval behavior changes.

## Context

The current search stack combines several retrieval and ranking layers:

- SQLite FTS5 / BM25 candidate retrieval
- multiple natural-language and field-focused FTS strategies
- reciprocal-rank-style fusion across strategies
- deterministic legal ranking signals
- optional graph, vector, and sparse retrieval signals
- optional Anthropic-backed query expansion, structured parsing, and reranking
- optional local cross-encoder reranking

This makes search quality harder to reason about from isolated examples. A
change can improve one legal query while quietly regressing docket lookup,
BGE lookup, statute-oriented search, or multilingual/cross-lingual behavior.

Before introducing a simplified search pipeline, a new reranker, a different
indexing backend, or changes to LLM usage, the project should have a
repeatable benchmark harness that establishes the current behavior as a
baseline.

## Goal

Create a reproducible search-only evaluation workflow that acts as a safety
net for future search improvements.

The harness should make it possible to answer:

1. Did search quality improve or regress?
2. Which query categories changed?
3. Did exact lookup behavior remain stable?
4. What is the latency impact?
5. Did external LLM usage or other expensive behavior increase or decrease?

The first implementation should not modify the search algorithm. It should
only make existing and future search behavior easier to measure.

## Existing Assets

The repository already contains useful building blocks:

- `benchmarks/search_relevance_golden.json` contains golden relevance
  judgments for search queries.
- `benchmarks/run_search_benchmark.py` computes retrieval metrics such as
  MRR, Recall, nDCG, Hit@1, latency, and per-tag summaries.
- `scripts/search_optimizer/evaluate.py` runs a similar evaluation path with
  richer traces for optimizer workflows.
- `benchmarks/swiss_legal_rag_bench/` evaluates broader retrieval-augmented
  generation behavior, but is intentionally broader than a search-only
  benchmark.

The proposed work should build on these files instead of creating a separate
benchmark convention.

## Non-Goals

- Do not change ranking weights.
- Do not enable or disable rerankers by default.
- Do not replace SQLite, FTS5, vector search, or any other retrieval backend.
- Do not claim that the existing golden set is a fully representative legal
  IR benchmark.
- Do not use end-to-end answer generation metrics for this first harness.

## Evaluation Metrics

The benchmark should report at least:

- `MRR@10`
- `Recall@10`
- `nDCG@10`
- `Hit@1`
- latency average
- latency p50 / p95, and p99 if repeated runs are supported
- evaluated query count
- skipped query count
- per-tag metrics

If feasible, future versions should also report:

- external LLM calls per query
- estimated LLM cost per benchmark run
- timeout/error rate
- duplicate result rate
- empty-result rate

## Golden Query Shape

The existing JSON shape should remain the primary format:

```json
{
  "id": "q002",
  "query": "Je cherche un arrêt sur le permis de construire d'un parc éolien",
  "tags": ["nl", "fr", "public-law"],
  "relevant": [
    {"decision_id": "bger_1A.122_2005", "grade": 3},
    {"decision_id": "bger_1A.124_2005", "grade": 2}
  ]
}
```

Useful query tags to preserve or add over time:

- `docket`
- `bge`
- `statute`
- `de`
- `fr`
- `it`
- `cross-lingual`
- `civil`
- `criminal`
- `public-law`
- `social-law`
- `concept-query`
- `exact-known-case`
- `broad-doctrine`

Global metrics are not sufficient on their own. Exact lookup and multilingual
regressions should be visible even when the aggregate score improves.

## Baseline Profiles

The harness should support named baseline profiles so benchmark reports are
comparable.

### Offline Baseline

Purpose: deterministic local and CI-friendly evaluation.

Expected behavior:

- no network calls
- no Anthropic query expansion
- no Anthropic structured parse
- no Anthropic reranking
- no hosted model dependency

Example intent:

```bash
ANTHROPIC_API_KEY= \
LLM_EXPANSION_ENABLED=false \
SWISS_CASELAW_LLM_RERANK=false \
SWISS_CASELAW_VECTOR_SEARCH=false \
python benchmarks/run_search_benchmark.py \
  --db output/decisions.db \
  --golden benchmarks/search_relevance_golden.json \
  -k 10 \
  --json-output artifacts/search_eval/offline_baseline.json \
  --show-misses
```

### Production-Like Baseline

Purpose: measure behavior close to the hosted MCP server.

Expected behavior:

- uses the same environment flags as the deployed server
- may include Anthropic query expansion and reranking
- may be slower and less deterministic
- should record feature flags and environment metadata clearly

This profile is useful for operational comparison, but should not replace the
offline baseline for deterministic regression testing.

## Result Metadata

Every benchmark JSON report should record:

- benchmark schema version
- timestamp
- git commit
- database path
- database row count
- database generation or snapshot identifier, if available
- golden file path and version
- top-k value
- Python version
- host/platform summary
- relevant search environment flags

Important flags include:

- `LLM_EXPANSION_ENABLED`
- `SWISS_CASELAW_LLM_RERANK`
- `SWISS_CASELAW_LLM_RERANK_TOP_N`
- `SWISS_CASELAW_VECTOR_SEARCH`
- `SWISS_CASELAW_CROSS_ENCODER`
- `SWISS_CASELAW_GRAPH_SIGNALS`

Without this metadata, benchmark results can be misleading because offline and
production-like search modes may behave differently.

## Comparison Workflow

Add a small comparison command that compares two benchmark JSON reports:

```bash
python benchmarks/search_eval/compare.py \
  --base artifacts/search_eval/offline_baseline.json \
  --candidate artifacts/search_eval/search_v2.json
```

The comparison should report:

- aggregate metric deltas
- latency deltas
- per-tag deltas
- top regressions
- top improvements
- queries where relevant results disappeared from top-k
- queries where exact lookup behavior changed

Example output shape:

```text
Metric              Base      Candidate   Delta
MRR@10              0.470     0.528       +0.058
Recall@10           0.496     0.552       +0.056
nDCG@10             0.510     0.566       +0.056
Hit@1               0.330     0.390       +0.060
p95 latency         1800ms    420ms       -1380ms
LLM calls/query     1.7       0.0         -1.7
```

The comparison should also list concrete query-level changes:

```text
Top regressions:
- q017: relevant result moved from rank 2 to missing
- q044: BGE lookup changed from rank 1 to rank 6

Top improvements:
- q012: relevant result moved from missing to rank 1
- q038: relevant result moved from rank 8 to rank 2
```

## Suggested Implementation Plan

1. Extend `benchmarks/run_search_benchmark.py` so its JSON report includes
   reproducibility metadata and richer latency percentiles.
2. Add `benchmarks/search_eval/compare.py` for before/after comparisons.
3. Add `benchmarks/search_eval/README.md` with recommended commands for
   offline and production-like baselines.
4. Optionally add a tiny smoke test that runs only a few benchmark queries so
   CI can verify the harness shape without requiring the full corpus.
5. Keep the first PR limited to the harness and documentation.

## Acceptance Criteria

- A documented command can run the current search benchmark against a
  `decisions.db` snapshot.
- The benchmark writes a machine-readable JSON report.
- The report includes aggregate metrics, per-query results, per-tag metrics,
  latency data, and environment/config metadata.
- A comparison command can compare two benchmark JSON files.
- The comparison output identifies aggregate changes and query-level
  regressions/improvements.
- Documentation explains how this benchmark acts as a safety net before
  changing ranking or retrieval logic.
- No search ranking behavior changes are included in the initial harness PR.

## How This Supports Future Search Changes

Future search changes should include a benchmark comparison table in the PR
description. For example:

```text
Compared with offline_baseline:
- MRR@10 improved from X to Y
- Recall@10 improved from X to Y
- p95 latency changed from X ms to Y ms
- docket and BGE exact-query tags did not regress
- French, Italian, and cross-lingual tags did not regress
```

This makes search changes easier to review. The argument for a new search
methodology should be measured behavior on the repository's own legal queries,
not just general claims about newer retrieval techniques.
