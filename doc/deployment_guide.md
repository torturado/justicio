# Local deployment

## Prepare the project

Clone the repository and enter its root:

```sh
git clone https://github.com/bukosabino/justicio.git
cd justicio
```

## Start Qdrant

Run the tested Qdrant server version with persistent storage and the local API-key configuration:

```sh
docker run -d --name justicio-qdrant \
  -p 6333:6333 -p 6334:6334 \
  -v "$PWD/config/example_qdrant_local.yaml:/qdrant/config/production.yaml:ro" \
  -v "$PWD/qdrant_storage:/qdrant/storage" \
  qdrant/qdrant:v1.19.2
```

The REST API is at `http://localhost:6333`; the dashboard is at `http://localhost:6333/dashboard`. The local API key is the `service.api_key` value in `config/example_qdrant_local.yaml`.

## Install application dependencies

Create a Python 3.12 environment with uv:

```sh
uv python install 3.12
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple torch
uv pip install -r requirements.txt
```

Or use the standard library virtual environment module and pip:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple torch
python -m pip install -r requirements.txt
```

Set the application environment:

```sh
export APP_PATH="$PWD"
export GEMINI_API_KEY="<Gemini API key>"
export QDRANT_API_URL="http://localhost:6333"
export QDRANT_API_KEY="<service.api_key from config/example_qdrant_local.yaml>"
export TAVILY_API_KEY="<Tavily API key>"
export SENDGRID_API_KEY="<SendGrid API key>"
# Optional:
export LANGTRACE_API_KEY="<Langtrace API key>"
```

`QDRANT_API_KEY` can be omitted when connecting to a local Qdrant server without authentication. The application needs Gemini and Qdrant credentials at startup. Gemini generates answers; BGE-M3 creates embeddings locally. Tavily is only needed for the BOE web-search endpoint. SendGrid is used by ETL jobs to email load summaries; Langtrace is optional.

The first startup downloads BGE-M3 from Hugging Face (about 2.2 GB); the model uses around 2 GB of RAM at runtime. Sentence Transformers uses an available CUDA or ROCm device automatically and otherwise falls back to CPU. For GPU indexing, run the ETL in an environment with a matching GPU-enabled PyTorch build already installed; the CPU-only installation above is for CPU hosts. On the measured RX 9060 XT with ROCm, set `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` before indexing to enable the faster attention implementation.

The `render.yaml` blueprint still selects [Render's free plan](https://render.com/docs/compute-plans), which provides 512 MB RAM. That is below BGE-M3's runtime requirement, so the service needs a larger compute plan before deploying this model there.

## Load data

Load daily BOE documents for a date range:

```sh
python -m src.etls.boe.load dates 2024/01/01 2024/01/07
```

Load one consolidated norm or the full current catalogue:

```sh
python -m src.etls.boe_consolidada.load document BOE-A-2015-10565
python -m src.etls.boe_consolidada.load all
```

The initial `all` load processes about 12,400 norms and produces roughly 360,000 article fragments. Embeddings are generated locally, so indexing has no per-token Gemini embedding charge. On the measured RX 9060 XT with ROCm, a full load is estimated at about 2–2.5 hours; CPU indexing takes longer. The ETL still needs `GEMINI_API_KEY` because shared application initialization creates the answer-generation client.


## Run the service

```sh
uvicorn src.service.main:APP --host 0.0.0.0 --port 5001 --workers 1 --timeout-keep-alive 125 --log-level info
```

Open `http://localhost:5001/docs` for the API documentation.

## Existing Qdrant collections

The application creates missing collections at startup. Existing collections must have the current schema: a 1024-dimensional `dense` vector and a `bm25` sparse vector. Startup fails with a message if a collection has the previous 1536-dimensional schema. Recreate those collections and reload their documents; vectors from the Gemini embedding model cannot be reused.

For scheduled ingestion, `doc/cron_etl_daily_public.sh` runs both daily BOE jobs. Put the required environment variables in a shell-compatible `~/.config/justicio.env` file, using plain assignments such as:

```sh
GEMINI_API_KEY=your-gemini-api-key
QDRANT_API_URL=http://localhost:6333
QDRANT_API_KEY=your-qdrant-api-key
TAVILY_API_KEY=your-tavily-api-key
SENDGRID_API_KEY=your-sendgrid-api-key
```

Then use the example schedule in `doc/crontab_e.sh`.
