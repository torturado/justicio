# RAG evaluation

`questions.json` is a manually curated set of questions. Each case has the `sources` to search and the expected `targets`: an `identificador` and, for consolidated legislation, an `article` block. Cases with several targets compare norms and require all of them; cases with no targets are unanswerable (a nonexistent article or norm, foreign law, facts no norm contains) and the service should abstain.

The runner reports, for the answerable cases:

- Hit@1, Hit@5 and Hit@`top_k_results` and reciprocal rank of the targets in the hybrid candidates and after reranking.
- `selected_hit_rate`: how often all targets are among the sources given to the LLM, and the same without keeping the best hybrid results (`selected_without_hybrid_head_hit_rate`), to check whether `HYBRID_HEAD_SIZE` still helps.

With `--answers` it also generates answers and reports the answer rate and how often the targets are cited for answerable cases, the abstention rate for unanswerable ones, and the share of citations that point to returned sources.

Run it from the repository root with the same environment variables as the service, after loading the target norms and bulletins:

```bash
uv run --no-sync python -m evaluation.rag.run
uv run --no-sync python -m evaluation.rag.run --answers --output evaluation/rag/results.json
```

`--answers` makes two to four Gemini requests per question and incurs API usage.

## Results

Measured on 2026-10-22 with 73 questions (63 answerable, 6 of them comparisons of two norms, 10 on regional bulletins; 10 unanswerable) over 1,243 state norms with the rank of law in force (Ley, Ley Orgánica, Real Decreto Legislativo, Real Decreto-ley), the targeted Reales Decretos, and the BOCM, BOA and BOPV days around each regional target. Answers used `gemini-3.5-flash-lite`.

| Retrieval (answerable) | Hit@1 | Hit@5 | Hit@8 | MRR | Targets among the 8 sources given to the LLM |
| --- | --- | --- | --- | --- | --- |
| Hybrid only | 0.635 | 0.857 | 0.873 | 0.757 | |
| Reranked, PyTorch fp32, 40 candidates | 0.730 | 0.905 | 0.937 | 0.842 | 0.968 (0.937 without the hybrid head) |
| Reranked, OpenVINO int8, 40 candidates | 0.746 | 0.905 | 0.921 | 0.849 | 0.952 (0.921 without the hybrid head) |
| Reranked, OpenVINO int8, 30 candidates | 0.746 | 0.889 | 0.905 | 0.841 | 0.937 |
| Reranked, OpenVINO int8, 20 candidates | 0.746 | 0.905 | 0.905 | 0.841 | 0.921 |

These results set the configuration: 40 candidates (fewer lose one or two targets), keeping the three best hybrid results (`HYBRID_HEAD_SIZE`, +2 targets), and the int8 OpenVINO reranker on CPU (equal to PyTorch within one question, and about three times faster).

With answers (int8 reranker): 95% of the answerable questions were answered (3 abstentions, in which the audit rejected the answer and its regeneration although the target sources were selected), 86% cited every target, and every citation pointed to a returned source. Four of the 10 unanswerable questions ended in the abstention message; the other six answers, reviewed manually, state that the sources do not contain the requested article, law, foreign law, statistic or prediction, without inventing content.

Add cases only when the expected identifier and article have been checked against the official text. Interpret scores against this fixed set; it is a diagnostic sample, not a corpus-wide benchmark.
