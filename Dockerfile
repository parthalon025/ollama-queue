FROM python:3.12-slim

WORKDIR /app

# Install build deps, copy project, install package
RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY ollama_queue/ ./ollama_queue/

RUN uv pip install --system --no-cache .

# DB and data live in a volume
ENV OLLAMA_QUEUE_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 7683

CMD ["ollama-queue", "serve", "--port", "7683"]
