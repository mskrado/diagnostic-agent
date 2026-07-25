FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PROFILE_DIR=/app/integrations/publishi \
    AGENT_DEFAULT_PRESET=spring-micrometer

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code + runtime assets (presets ship inside app/profile/presets).
COPY app/ ./app/
COPY integrations/ ./integrations/
COPY service_map.yaml ./service_map.yaml
COPY runbooks/ ./runbooks/
COPY pyproject.toml ./pyproject.toml

# Read-only service account; runs unprivileged.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/audit /app/chroma_db \
    && chown -R agent:agent /app
USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
