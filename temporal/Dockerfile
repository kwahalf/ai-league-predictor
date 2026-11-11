FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml ./
COPY src ./src
# Agent configurations are read at run time by both the worker and the API.
COPY agents ./agents
# Dev extras are installed so `pytest` runs in this image: the test suite is the
# only way to exercise the agent loop without model credentials.
RUN pip install --no-cache-dir ".[dev]"

CMD ["python", "-m", "src.worker"]
