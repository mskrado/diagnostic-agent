# Spring Boot modular-monolith — reference integration profile.
#
# Full-stack example: API gateway + modular monolith + common backing stores.
# Copy and adapt for your own Spring / Micrometer host.
#
#   diag validate -w examples/spring-modular-monolith
#   diag serve -w examples/spring-modular-monolith --port 8000
#
# Files:
#   agent.yaml            workspace manifest (schema + preset)
#   service_map.yaml      topology / blast radius
#   metrics_profile.yaml  PromQL templates (extends spring-micrometer)
#   logs_profile.yaml     Loki labels + alert line filters
#   redaction.yaml        tenant / PII scrubbing
#   prompt_profile.yaml   platform description + tool-run hints

name: spring-modular-monolith
