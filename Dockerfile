FROM python:3.12-slim

# No project baked in as the default profile: the agent runs on the in-package
# preset unless a host workspace is mounted at /workspace (or AGENT_PROFILE_DIR
# points at a bundled example under /app/examples). Presets always supply
# redaction rules, so an unmounted /workspace degrades to preset-only behaviour
# rather than silently disabling redaction — Docker turns a missing mount source
# into an empty directory, and that used to shadow the profile.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_PROFILE_DIR="" \
    AGENT_WORKSPACE=/workspace
# AGENT_DEFAULT_PRESET is deliberately unset: the host workspace's agent.yaml
# `extends:` supplies it. Baking a value here would override every host.

WORKDIR /app

# Install deps first for better layer caching. Retries/timeout because the
# resolver downloads several large wheels (chromadb, torch-free langchain stack)
# and a single slow PyPI response otherwise fails the whole build.
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt

# App code + runtime assets (presets ship inside app/profile/presets).
COPY app/ ./app/
COPY examples/ ./examples/
COPY runbooks/ ./runbooks/
# pyproject reads its dependency lists from requirements*.txt; README is the
# declared readme. Deps are already installed, so only console scripts are added.
COPY pyproject.toml requirements-dev.txt README.md ./
RUN pip install --no-cache-dir --no-deps -e .

# Read-only service account; runs unprivileged.
RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/audit /app/chroma_db /workspace \
    && chown -R agent:agent /app /workspace
USER agent

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://localhost:8000/health').status==200 else sys.exit(1)"

CMD ["diag", "serve", "--host", "0.0.0.0", "--port", "8000"]
