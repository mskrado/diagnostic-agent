# Hello-world integration profile — a plain 3-tier web app.
#
# Prove zero-code onboarding: point the agent at this directory and set env vars.
#
#   export AGENT_PROMETHEUS_URL=http://localhost:9090
#   export AGENT_LOKI_URL=http://localhost:3100
#   diag validate -w examples/hello-world
#   diag serve -w examples/hello-world --port 8000
#
# Or with Docker (the image reads /workspace):
#   docker run --rm -p 8001:8000 \
#     -e AGENT_PROMETHEUS_URL=http://host.docker.internal:9090 \
#     -e AGENT_LOKI_URL=http://host.docker.internal:3100 \
#     -e AGENT_RAG_ENABLED=true \
#     -v "$PWD/examples/hello-world:/workspace:ro" \
#     ghcr.io/mskrado/diagnostic-agent:latest

name: hello-world
