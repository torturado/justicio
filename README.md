# Justicio

Justicio is a question-answering assistant for questions about Spain's official state gazette, the Boletín Oficial del Estado (BOE).

[Spanish link](https://www.boe.es)

[English link](https://www.boe.es/index.php?lang=en)

The service retrieves relevant official text and uses it as context for answers. Retrieved sources are cited as `[n]` in the response.

## Service

The service is available at [Justicio](https://justicio.es/).

## How it works

1. ETL jobs download BOE and regional bulletin documents, split their text, embed it, and store the chunks and metadata in Qdrant.
2. For a question, the service embeds the query and searches both dense vectors and Qdrant's server-side BM25 index.
3. Qdrant combines the two ranked result sets with Reciprocal Rank Fusion (RRF).
4. When a query names a law or article, Qdrant narrows candidates to its indexed legal metadata. A cross-encoder reranks up to 40 hybrid candidates and selects the eight strongest sources while retaining the three highest hybrid results.
5. The service sends the question and sources, including BOE identifiers, article labels, version dates, and links, to Gemini, which generates a Spanish answer with `[n]` citations. A second Gemini pass checks each factual and legal claim against its cited text. If a claim is unsupported or the audit is unavailable, the service abstains and reports the audit status as `grounding_validation`. Generic monitorio questions are answered from the civil procedure first; a labor monitorio, when present, is kept separate with its own legal scope.

## Components

### Data ingestion

The daily BOE ETL reads the open-data summary API and indexes sections I and T. Consolidated legislation is indexed separately, using the current text of norms in force and organizing text by article or block. Long blocks can be split into smaller chunks. Regional ETLs are available for the BOA, BOCM, BOJA, BOPV, and BOPZ. As checked on 2026-10-19, BOA, BOCM, and BOJA worked; BOPV needs a scraper rewrite after a site structure change, and BOPV/BOPZ could not be verified because `euskadi.eus` and `bop.dpz.es` refused or timed out connections from our network. See the [BOPV README](src/etls/bopv/README.md) and [BOPZ README](src/etls/bopz/README.md).

Daily bulletin ingestion:

```sh
python -m src.etls.boe.load today
python -m src.etls.boe.load dates 2024/01/01 2024/01/07
```

Consolidated legislation:

```sh
python -m src.etls.boe_consolidada.load document BOE-A-2015-10565
python -m src.etls.boe_consolidada.load all
```

`all` performs the initial catalogue load. The daily consolidated ETL refreshes changed norms and norms with a scheduled revision date. `python -m src.etls.jobs` schedules the daily jobs for the BOE, consolidated legislation, and regional bulletins. The consolidated job runs after the daily BOE job so newly consolidated norms replace their originally published text.

### Embeddings and search

[`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) creates 1024-dimensional embeddings locally for documents and queries. Sentence Transformers selects an available accelerator and otherwise runs on CPU. Qdrant stores vectors in the named `dense` vector with cosine distance and int8 scalar quantization. It also computes Spanish BM25 sparse vectors on the server with `qdrant/bm25`. Dense and sparse results are fused with RRF for hybrid search.

`BAAI/bge-reranker-v2-m3` reranks the fused candidates on CPU. It loads lazily on the first nonempty query and downloads its weights if they are not cached. Explicit legal references are filtered against `numero_oficial` and article-block metadata. The `/qa` response keeps `context` and adds a numbered `sources` list with source identifiers, article, version and update dates, BOE URL, hybrid score, and reranker score. The two scores have different meanings and neither is a confidence probability.

### Answer generation

The `/qa` endpoint uses Gemini `gemini-3.5-flash-lite` to answer from retrieved context and a second request to audit claim-to-citation support. This adds a Gemini request and its associated latency and API usage to answers with citations. Answers are instructed to cite numbered sources and to say when the context is insufficient. Optional Langtrace instrumentation records traces. Regional collection scopes are included in the grounding prompt. An empty collection returns an explicit no-indexed-documents answer without calling Gemini. The curated retrieval benchmark is documented in [`evaluation/rag`](evaluation/rag/README.md).

## Tools

- Python 3.12, FastAPI, and Uvicorn
- Google GenAI SDK and Qdrant Client
- BGE-M3 and Sentence Transformers for local embeddings
- Typer and `schedule` for ETL commands and daily jobs
- BeautifulSoup and `langchain-text-splitters` for document processing
- Tavily for BOE web search
- SendGrid for ETL notifications
- Langtrace for optional tracing

## Deploy your own service

See the [deployment guide](doc/deployment_guide.md).

## Contributing

You are welcome to help develop the project. Please contact us to discuss contributing.

- [Darío López](https://www.linkedin.com/in/dar%C3%ADo-l%C3%B3pez-padial-45269150/)
- [Alex Dantart](https://www.linkedin.com/in/dantart/)
- [Jorge Iliarte](https://www.linkedin.com/in/jorge-iliarte-llop/)
- [Jorge Barrachina](https://www.linkedin.com/in/jorgebarrachina/)
