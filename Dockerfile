FROM python:3.12-slim

WORKDIR /usr/app

COPY requirements.txt .

RUN pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        --extra-index-url https://pypi.org/simple \
        torch \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

ENV APP_PATH=/usr/app \
    PYTHONPATH=/usr/app

EXPOSE 5000

CMD ["uvicorn", "src.service.main:APP", "--host", "0.0.0.0", "--port", "5000", "--workers", "1", "--timeout-keep-alive", "125", "--log-level", "info"]
