FROM python:3.12-slim

WORKDIR /app

# System deps for openpyxl, httpx, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl git && rm -rf /var/lib/apt/lists/*

# Copy uv binary directly from official image (avoids PATH/installer issues)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

COPY pyproject.toml .
RUN uv pip install --system --no-cache ".[dev]"

COPY . .

# Pre-load the model artifact if present (built during CI / make train)
RUN mkdir -p models

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -f http://localhost:8000/healthz || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
