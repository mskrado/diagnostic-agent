FROM python:3.12-slim

# No project baked in as the default profile: run with the in-package preset
# unless AGENT_PROFILE_DIR points at one. Bundled profiles under /app/integrations
# (e.g. /app/integrations/publishi) can be selected without a bind mount, which
# matters because Docker turns a missing mount source into an empty directory and
# that would shadow the profile and disable redaction.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PROFILE_DIR="" \
    AGENT_DEFAULT_PRESET=generic-prometheus

WORKDIR /app

# Install deps first for better layer caching. Retries/timeout because the
# resolver downloads several large wheels (chromadb, torch-free langchain stack)
# and a single slow PyPI response otherwise fails the whole build.
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

# App code + runtime assets (presets ship inside app/profile/presets).
COPY app/ ./app/
COPY integrations/ ./integrations/
COPY runbooks/ ./runbooks/
# pyproject reads its dependency lists from requirements*.txt.
COPY pyproject.toml requirements-dev.txt ./

# Read-only service account; runs unprivileged.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/audit /app/chroma_db \
    && chown -R agent:agent /app
USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
