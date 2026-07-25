# Standalone public repository

The diagnostic agent is published as a standalone OSS project:

**https://github.com/mskrado/diagnostic-agent**

This monorepo keeps a vendored copy under `diagnostic-agent/` so publishi can
iterate and CI can build locally. Compose mounts
`diagnostic-agent/integrations/publishi` as the integration profile.

To consume the published image instead of a local build:

```bash
DIAGNOSTIC_AGENT_IMAGE=ghcr.io/mskrado/diagnostic-agent:latest \
  docker compose -f docker-compose.yml -f docker-compose.observability.yml \
  --profile diagnostic-agent up -d
```

History was extracted with `git filter-repo --path diagnostic-agent/` and
secret-scanned before the first public push.
