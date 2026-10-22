FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

RUN uv sync --locked --no-dev --no-editable

ENV PATH="/app/.venv/bin:$PATH"

ENTRYPOINT ["python3", "-m", "ariadne_mcp.server"]
