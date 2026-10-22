FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /usr/app

COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --extra cpu --no-install-project

COPY config config
COPY src src

# The UID matches the usual host user, so the mounted Hugging Face cache stays writable
RUN useradd --create-home --uid 1000 justicio
USER justicio

ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/usr/app

EXPOSE 5000

CMD ["uvicorn", "src.service.main:APP", "--host", "0.0.0.0", "--port", "5000", "--workers", "1", "--timeout-keep-alive", "125", "--log-level", "info"]
