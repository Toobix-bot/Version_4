# Multi-stage build for Evolution Sandbox
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# System deps (add build-essential only if native wheels needed)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates tini && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first for layer caching
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy source
COPY src ./src
COPY run_simulation.py ./
COPY examples ./examples
COPY scripts ./scripts
COPY tests ./tests
COPY README.md ./

# Create logs dir for volume mount
RUN mkdir -p logs

EXPOSE 8099

# Non-root user (optional)
RUN useradd -u 1001 -ms /bin/bash appuser
USER appuser

ENV UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8099 \
    LOG_LEVEL=info

ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["python","-m","uvicorn","src.api.app:app","--host","0.0.0.0","--port","8099"]
