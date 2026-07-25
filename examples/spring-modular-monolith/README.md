# Spring Boot modular-monolith — reference integration profile.
#
# Full-stack example: API gateway + modular monolith + common backing stores.
# Copy and adapt for your own Spring / Micrometer host.
#
#   export AGENT_PROFILE_DIR=$PWD/examples/spring-modular-monolith
#   export AGENT_DEFAULT_PRESET=spring-micrometer
#   export AGENT_RUNBOOKS_PATH=$PWD/runbooks   # optional; defaults to package runbooks/
#   diagnostic-agent serve --port 8000
#
# Files:
#   service_map.yaml      topology / blast radius
#   metrics_profile.yaml  PromQL templates (extends spring-micrometer)
#   logs_profile.yaml     Loki labels + alert line filters
#   redaction.yaml        tenant / PII scrubbing
#   prompt_profile.yaml   platform description + tool-run hints

name: spring-modular-monolith
