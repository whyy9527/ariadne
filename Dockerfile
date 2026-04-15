FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir mcp onnxruntime tokenizers huggingface_hub

COPY . .

ENTRYPOINT ["python3", "mcp_server.py"]
