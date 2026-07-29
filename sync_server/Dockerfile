# Multi-stage Dockerfile for the GhostBrowser sync server.
# The server is a minimal FastAPI application with only stdlib SQLite persistence.

# ---------------------------------------------------------------------------
# Build stage
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies if any wheels are needed.
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY sync_server/requirements.txt ./sync_server/requirements.txt
COPY sync_server/*.py ./sync_server/

# Install Python runtime dependencies into a virtual environment.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r ./sync_server/requirements.txt

# ---------------------------------------------------------------------------
# Runtime stage
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONFAULTHANDLER=1 \
    # Default port the sync server binds to.
    PORT=8080

WORKDIR /app

# Copy the virtual environment with runtime dependencies.
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy only application source needed for runtime.
COPY sync_server ./sync_server

# Create a non-root user to own the process and data volume.
RUN useradd --create-home --shell /bin/bash ghostsync \
    && mkdir -p /data \
    && chown -R ghostsync:ghostsync /app /data

USER ghostsync

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health')" || exit 1

CMD ["uvicorn", "sync_server.main:app", "--host", "0.0.0.0", "--port", "8080"]
