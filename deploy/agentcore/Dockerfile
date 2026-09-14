# AgentCore Runtime requires linux/arm64.
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY deploy/agentcore/requirements.txt /app/requirements-agentcore.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-agentcore.txt

COPY backend/closer /app/closer
COPY deploy/agentcore/agentcore_app.py /app/agentcore_app.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    CLOSER_MODE=demo \
    CLOSER_MODEL_PROVIDER=bedrock \
    CLOSER_DB=/tmp/closer.db

# AgentCore Runtime contract: HTTP on 8080 with /invocations and /ping.
EXPOSE 8080
CMD ["python", "-m", "agentcore_app"]
