# Reproducible builds: pin BASE_IMAGE by digest in client/agent compose build.args.
# Internal mirrors: pass PIP_INDEX_URL / PIP_EXTRA_INDEX_URL at build time.
ARG BASE_IMAGE=python:3.12-slim
ARG PIP_INDEX_URL=
ARG PIP_EXTRA_INDEX_URL=
FROM ${BASE_IMAGE}

# No project baked in as the default profile: the agent runs on the in-package
# preset unless a host workspace is mounted at /workspace. Presets always supply
# redaction rules, so an unmounted /workspace degrades to preset-only behaviour
# rather than silently disabling redaction — Docker turns a missing mount source
# into an empty directory, and that used to shadow the profile.
#
# Do NOT set AGENT_PROFILE_DIR="" here. An empty string is a set value, and
# `diag serve` must be able to fill AGENT_PROFILE_DIR from the mounted workspace
# (see app.cli._apply_workspace_env). Compose also pins AGENT_PROFILE_DIR=/workspace.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    AGENT_WORKSPACE=/workspace
# AGENT_DEFAULT_PRESET is deliberately unset: the host workspace's agent.yaml
# `extends:` supplies it. Baking a value here would override every host.

WORKDIR /app

# Install deps first for better layer caching. Prefer requirements.lock when present.
COPY requirements.txt requirements.lock* ./
RUN if [ -f requirements.lock ]; then \
      pip install --no-cache-dir -r requirements.lock; \
    else \
      pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt; \
    fi

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
