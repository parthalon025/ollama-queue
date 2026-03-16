FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend_agent.py .

RUN pip install --no-cache-dir fastapi uvicorn httpx docker

VOLUME ["/data"]
EXPOSE 11435

ARG AGENT_VERSION=dev
ENV AGENT_VERSION=${AGENT_VERSION}

LABEL org.opencontainers.image.source=https://github.com/parthalon025/ollama-queue
LABEL org.opencontainers.image.description="Ollama backend agent — model sync, health monitoring, Docker management"

CMD ["python3", "backend_agent.py"]
