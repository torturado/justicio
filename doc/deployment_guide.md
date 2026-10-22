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

The REST API is at `http://localhost:6333`; the dashboard is at `http://localhost:6333/dashboard`. The client connects through gRPC on port 6334. `config/example_qdrant_local.yaml` defines a read-write key (`service.api_key`), for the ETL, and a read-only key (`service.read_only_api_key`), for the API service. Generate your own keys with `openssl rand -hex 32` for anything but local tests.

## Install application dependencies

Install [uv](https://docs.astral.sh/uv/) and create the environment from the lockfile. Choose the PyTorch build with an extra:

```sh
uv sync --locked --extra cpu    # CPU-only hosts
uv sync --locked --extra rocm   # AMD GPUs with ROCm 7.2
uv sync --locked                # PyPI's default build (CUDA on Linux)
```

The environment is created in `.venv`; run commands with `uv run --no-sync ...` or after `source .venv/bin/activate`.

The first startup downloads BGE-M3 and the reranker from Hugging Face (about 2.2 GB each). With `reranker_backend: openvino` (the default, for CPUs), the service then converts the reranker to an int8 OpenVINO model calibrated with 128 chunks of the collection, so load some documents before the first start. The export takes about three minutes and several GB of memory once, and is kept in `$HF_HOME/justicio/`; delete that directory to export again, e.g. after changing the model. Before exporting, the service declines the anonymous usage telemetry that OpenVINO's converter and NNCF send by default (it writes `~/intel/openvino_telemetry`). A service process with the embedder and the int8 reranker uses about 4.7 GB of RAM. Sentence Transformers uses an available CUDA or ROCm device automatically and otherwise falls back to CPU. On the measured RX 9060 XT with ROCm, set `TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1` before indexing to enable the faster attention implementation. With ROCm 7.2 and PyTorch 2.14 at its stock limits, sustained half-precision embedding hung that GPU's firmware (`amdgpu: MES might be in unrecoverable state`); because the GPU also drove the displays, the machine needed a reboot. With the GPU clock lowered by 150 MHz and the power limit set to 150 W instead of 182 W, the same load ran for 30 minutes without errors at about 2.6 GHz and an 84 °C junction temperature. The limits were set with [LACT](https://github.com/ilya-zlobintsev/LACT), whose clock controls require the kernel parameter `amdgpu.ppfeaturemask=0xfff7ffff`. If a GPU hangs during indexing, apply similar limits or index on the CPU.

## Configure the environment

| Variable | Used by | Required |
| --- | --- | --- |
| `QDRANT_API_URL` | ETL, service | Yes, e.g. `http://localhost:6333` |
| `QDRANT_API_KEY` | ETL (read-write key), service (read-only key) | If Qdrant requires a key |
| `GEMINI_API_KEY` | Service | Yes |
| `JUSTICIO_API_KEY` | Service: value of the `X-API-Key` header | Yes |
| `TAVILY_API_KEY` | Service: BOE web-search endpoints | No |
| `LANGTRACE_API_KEY` | Service: tracing and `/qa_feedback` | No |
| `SENDGRID_API_KEY`, `ETL_NOTIFICATION_EMAIL` | ETL: run reports | No |

The application settings (models, collection, sources, limits and prompts) are in `config/config.yaml`; unknown or missing keys make startup fail.

## Load data

The ETL creates the `legal_documents` collection if it does not exist. Load one consolidated norm, the full current catalogue, or daily bulletins for a date range:

```sh
uv run --no-sync python -m src.etls.cli consolidated document BOE-A-2015-10565
uv run --no-sync python -m src.etls.cli consolidated all
uv run --no-sync python -m src.etls.cli bulletin dates BOE 2024/01/01 2024/01/07
```

The initial `all` load processes about 12,400 norms. Embeddings are generated locally, so indexing has no per-token embedding charge. On the measured RX 9060 XT with ROCm, the previous chunking (6,000 characters) took about 2–2.5 hours; chunks are now at most 1,500 characters, so expect more chunks and a longer load. On a 20-thread CPU, a law of about 200 chunks takes around two minutes.

### Collections from previous versions

Previous versions stored each source in its own collection (`justicio`, `bocm`, `bopz`, `bopv`, `boja`, `boa`) with a different payload layout and larger chunks. They are not read anymore: load the data again into `legal_documents` and then delete the old collections.

## Run the service

```sh
uv run --no-sync uvicorn src.service.main:APP --host 0.0.0.0 --port 5001 --workers 1 --timeout-keep-alive 125 --log-level info
```

Open `http://localhost:5001/docs` for the API documentation. Startup fails if the collection does not exist yet.

The models run on a single inference thread per process, so use one worker per host and scale horizontally; each process needs its own copy of the models in memory. With Docker:

```sh
bin/build
bin/run   # forwards the variables above that are set in your shell
```

## Schedule the ETL

`doc/cron_etl_daily_public.sh` runs `python -m src.etls.cli daily` from the project's `.venv`. Put the ETL environment variables in a shell-compatible `~/.config/justicio.env` file, using plain assignments such as:

```sh
QDRANT_API_URL=http://localhost:6333
QDRANT_API_KEY=your-read-write-qdrant-api-key
SENDGRID_API_KEY=your-sendgrid-api-key
ETL_NOTIFICATION_EMAIL=you@example.com
```

Then use the example schedule in `doc/crontab_e.sh`. The command exits with status 1 when a source fails, and the emailed report lists the failed days and documents.
