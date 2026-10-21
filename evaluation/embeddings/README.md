# Embedding and retrieval evaluation

Run the evaluator from the repository root with Python 3.12:

```sh
python -m evaluation.embeddings.eval --help
```

The CLI has two commands:

## Dataset comparison

`dataset` compares dense retrieval, Qdrant server-side Spanish BM25, and hybrid retrieval with reciprocal rank fusion
on [`dariolopez/justicio-rag-embedding-qa`](https://huggingface.co/datasets/dariolopez/justicio-rag-embedding-qa).
It downloads the 815 question/context/answer rows in pages of 100 through the Hugging Face datasets-server JSON API.
The rows are cached in `evaluation/embeddings/.cache/dataset.json`; result JSON files are written beside the cache.

Contexts are deduplicated before indexing. Each question is relevant to the unique corpus item matching its own
context, so identical contexts share one relevant item. Reported measures are Recall@1, Recall@5, Recall@10, and
MRR@10, averaged over all questions. Retrieval considers the top 10 items for each mode.

Configure `APP_PATH` to the repository root and set `QDRANT_API_URL` (plus `QDRANT_API_KEY` if required). The
dataset command creates a temporary collection named `eval_<embedder-slug>_<dimensions>` and removes it when it
finishes. Pass `--keep-collection` to retain it.

Use Gemini embeddings with the default 1536-dimensional output:

```sh
export APP_PATH="$PWD"
export QDRANT_API_URL="http://localhost:6333"
export GEMINI_API_KEY="..."
python -m evaluation.embeddings.eval dataset --embedder gemini
```

An optional `--dimensions` overrides the Gemini output dimensions.

For local sentence-transformers models, use a Python 3.12 environment with the application dependencies installed.
To evaluate on CPU, install the CPU PyTorch wheel first:

```sh
python3.12 -m venv /tmp/justicio-eval-venv
/tmp/justicio-eval-venv/bin/python -m pip install \
  --index-url https://download.pytorch.org/whl/cpu \
  --extra-index-url https://pypi.org/simple \
  torch
/tmp/justicio-eval-venv/bin/python -m pip install -r requirements.txt
/tmp/justicio-eval-venv/bin/python -m evaluation.embeddings.eval dataset \
  --embedder st:Qwen/Qwen3-Embedding-0.6B
```

When a model defines a `query` prompt, the evaluator applies it only to question embeddings; document contexts are
encoded without a prompt. The default BGE-M3 model does not require a query instruction.

## Existing index check

`index` checks the application's configured `(BOE identifier, question)` pairs against an existing Qdrant
collection. It uses the configured local model for query embeddings and the application's hybrid
`VectorStore.search` path, with `top_k_results` and `prefetch_limit` from `config/config.yaml`.

```sh
python -m evaluation.embeddings.eval index
python -m evaluation.embeddings.eval index --collection justicio
```

Each question contributes at most one hit. The output includes the rank of the first chunk with the expected
`metadata.identificador`, or `miss`, followed by the total hit@k count.
