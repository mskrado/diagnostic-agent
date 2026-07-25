# publishi.ai — reference integration profile for the diagnostic agent.
#
# Point the agent at this directory:
#   AGENT_PROFILE_DIR=/path/to/integrations/publishi
#   AGENT_DEFAULT_PRESET=spring-micrometer
#
# Runbooks stay at the package-root `runbooks/` (shared RAG corpus). Override
# with AGENT_RUNBOOKS_PATH if you keep a private corpus elsewhere.
#
# Files:
#   service_map.yaml      topology / blast radius
#   metrics_profile.yaml  PromQL templates (extends spring-micrometer)
#   logs_profile.yaml     Loki labels + alert line filters
#   redaction.yaml        tenant / PII scrubbing
#   prompt_profile.yaml   platform description + tool-run hints

name: publishi
