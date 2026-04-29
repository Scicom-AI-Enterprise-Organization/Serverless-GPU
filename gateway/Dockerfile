FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml ./
COPY gateway ./gateway
RUN uv pip install --system --no-cache-dir -e .
EXPOSE 8080
ENV GATEWAY_BIND=0.0.0.0:8080
CMD ["python", "-m", "gateway.main"]
