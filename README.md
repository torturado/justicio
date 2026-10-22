# Justicio

Justicio is a question-answering assistant for questions about Spain's official state gazette, the Boletín Oficial del Estado (BOE), and several regional bulletins.

[Spanish link](https://www.boe.es)

[English link](https://www.boe.es/index.php?lang=en)

The service retrieves relevant official text and uses it as context for answers. Retrieved sources are cited as `[n]` in the response.

## Service

The service is available at [Justicio](https://justicio.es/).

## How it works

1. ETL jobs download BOE and regional bulletin documents, split their text into chunks, embed them and store the chunks and their metadata in a single Qdrant collection. Each chunk records its source (`source_name`).
2. For a question, the service embeds the query and searches the requested sources with both dense vectors and Qdrant's server-side BM25 index. Qdrant combines the two ranked result sets with Reciprocal Rank Fusion (RRF).
3. When a question names norms by their official number (e.g. "Ley 39/2015"), the search is first restricted to those norms and, when a single norm and an article are named, to that article. If a restricted search finds nothing, for example because the norm is not indexed, the next broader search runs.
4. A cross-encoder reranks up to 40 hybrid candidates and selects the eight strongest sources while retaining the three highest hybrid results. Each selected chunk is given to the model together with its adjacent chunks of the same article.
5. The service sends the question and the sources, including identifiers, source type (consolidated text or bulletin publication), article labels, dates and links, to Gemini, which generates a Spanish answer with `[n]` citations. A second Gemini pass checks each factual and legal claim against its cited text. An answer that fails the check is regenerated once with the findings; if it still fails, or the check is unavailable, the service abstains and reports the result as `grounding_validation`.

## Components

### Data ingestion

The daily BOE ETL reads the open-data summary API and indexes sections I and T. Consolidated legislation is indexed separately, using the current text of norms in force, organized by article or block. Regional ETLs are available for the BOA, BOCM, BOJA, BOPV and BOPZ; see their READMEs under `src/etls/` for what was verified on each site.

All loads run through one command line:

```sh
python -m src.etls.cli bulletin dates BOE 2024/01/01 2024/01/07
python -m src.etls.cli bulletin update BOCM
python -m src.etls.cli consolidated document BOE-A-2015-10565
python -m src.etls.cli consolidated all
python -m src.etls.cli daily
```

`consolidated all` performs the initial catalogue load. `update` commands resume from the latest indexed date, so a missed run is caught up by the next one. `daily` updates every bulletin and then the consolidated legislation, so that newly consolidated norms replace their originally published text. Each run emails a report when SendGrid is configured and exits with status 1 if any document, day or source failed. Schedule `daily` with cron (see the [deployment guide](doc/deployment_guide.md)).

### Embeddings and search

[`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) creates 1024-dimensional embeddings locally for documents and queries. Sentence Transformers selects an available accelerator and otherwise runs on CPU. Qdrant stores vectors in the named `dense` vector with cosine distance and int8 scalar quantization. It also computes Spanish BM25 sparse vectors on the server with `qdrant/bm25`.

`BAAI/bge-reranker-v2-m3` reranks the fused candidates. Chunks are at most 1,500 characters so that the reranker, limited to 512 tokens, scores each chunk with its title in full: with a long question, 99% of the chunks of a sample of consolidated laws and bulletins fit. Both models load at startup and run on one inference thread. On CPU the reranker runs as an int8 OpenVINO model (`reranker_backend: openvino`), exported on the first start and calibrated with chunks of the collection: reranking 40 candidates takes about 6 s instead of 19 s with PyTorch on an Intel Core i5-13500, with the same retrieval quality on the evaluation set. GPU hosts use `reranker_backend: torch`. Reranking still dominates latency, so the service rejects requests beyond `max_concurrent_answers` with HTTP 503 instead of queueing them.

### Answer generation

The `/qa` endpoint uses Gemini `gemini-3.5-flash-lite` to answer from retrieved context and a second request (`grounding_model_name`) to audit claim-to-citation support. The sources are sent as delimited data in the user message, not in the system instruction. An empty source selection returns an explicit no-indexed-documents answer without calling Gemini. The curated evaluation is documented in [`evaluation/rag`](evaluation/rag/README.md).

## API

Every endpoint except `GET /healthcheck` is a `POST` with a JSON body and requires the `X-API-Key` header with the value of `JUSTICIO_API_KEY`. The API is meant to be called by the Justicio frontend, not directly by browsers.

```sh
curl -X POST http://localhost:5000/qa \
  -H "X-API-Key: $JUSTICIO_API_KEY" -H "Content-Type: application/json" \
  -d '{"query": "¿Qué plazo tiene la Administración para resolver?", "sources": ["BOE"], "client_ip": "203.0.113.7"}'
```

- `/qa` and `/semantic_search`: `query` (up to 2,000 characters), optional `sources` (keys of `sources` in `config/config.yaml`; default `BOE`), `original_query` and `client_ip`. Responses list the numbered `sources` with their text, identifiers, dates, URL, hybrid score and reranker score; neither score is a confidence probability.
- `/qa_tavily` and `/semantic_search_tavily`: answer from a BOE web search (requires `TAVILY_API_KEY`).
- `/qa_feedback`: `span_id`, `trace_id` and `user_score` (`1` or `-1`) of a traced answer (requires Langtrace).

`client_ip` is the end user's IP as forwarded by the frontend. It limits each user to `rate_limit_per_minute` requests and is stored in traces only as its network prefix (/24 or /48).

### Privacy

Questions can contain personal data. The service logs request metadata, not question or answer text. When `LANGTRACE_API_KEY` is set, prompts and answers are sent to Langtrace as traces; define a retention policy for that project.

## Tools

- Python 3.12 managed with uv, FastAPI and Uvicorn
- Google GenAI SDK and Qdrant Client
- BGE-M3 and Sentence Transformers for local embeddings and reranking
- Typer for the ETL command line
- BeautifulSoup, lxml and `langchain-text-splitters` for document processing
- Tavily for BOE web search
- SendGrid for ETL reports
- Langtrace for optional tracing
- Ruff (including the Bandit security rules) and pytest in CI

## Deploy your own service

See the [deployment guide](doc/deployment_guide.md).

## Contributing

You are welcome to help develop the project. Please contact us to discuss contributing.

- [Darío López](https://www.linkedin.com/in/dar%C3%ADo-l%C3%B3pez-padial-45269150/)
- [Alex Dantart](https://www.linkedin.com/in/dantart/)
- [Jorge Iliarte](https://www.linkedin.com/in/jorge-iliarte-llop/)
- [Jorge Barrachina](https://www.linkedin.com/in/jorgebarrachina/)
