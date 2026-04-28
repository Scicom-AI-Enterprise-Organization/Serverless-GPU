FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
COPY worker_agent ./worker_agent
RUN uv pip install --system --no-cache-dir -e .
CMD ["python", "-m", "worker_agent.main"]
