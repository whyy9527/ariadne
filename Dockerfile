FROM python:3.11-slim

WORKDIR /app

COPY . .

RUN pip install --no-cache-dir .

ENTRYPOINT ["python3", "-m", "ariadne_mcp.server"]
