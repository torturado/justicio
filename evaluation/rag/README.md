# RAG retrieval evaluation

`questions.json` is a small, manually curated set of questions with expected BOE identifiers and article blocks. The runner reports exact target Hit@1, Hit@5, Hit@12, and reciprocal rank for hybrid retrieval and after reranking. By default it does not call Gemini. Add `--answers` to generate answers, include the service's `grounding_validation` status, and measure whether citations point to returned sources and cite the expected law/article. The citation metrics check reference integrity and target coverage; the semantic claim audit is the Gemini verification pass used by the service.

Run it from the repository root with the same environment variables as the service:

```bash
python -m evaluation.rag.run
```

The `--answers` option makes one Gemini request per question and may incur API usage.

Write a JSON report to a file with `--output evaluation/rag/results.json`, or select a Qdrant collection with `--collection`. Service initialization ensures the payload indexes needed by explicit law/article filters exist; running the evaluation may therefore create missing Qdrant payload indexes. The reranker downloads its configured model on first use if it is not cached.

Add cases only when the expected BOE identifier and article have been checked against the official BOE text. Interpret scores against this fixed set; it is a diagnostic sample, not a representative corpus-wide benchmark.
