# Host workspace reference

A **workspace** is one directory in your repository holding everything specific
to your stack. The agent ships as a generic image; the workspace is the only
thing you write.

```
infrastructure/diagnostic-agent/
├── agent.yaml        # manifest
├── profile/          # integration profile
├── runbooks/         # RAG corpus (markdown)
├── scenarios.yaml    # runbook E2E scenarios
└── blind_eval.yaml   # blind-eval dataset
```

Because the manifest declares every path, commands take no path arguments:

```bash
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<tag> diag validate
```

## Locating the workspace

Resolved in this order:

1. `-w` / `--workspace` on the command line
2. `AGENT_WORKSPACE` (the image sets this to `/workspace`)
3. The nearest enclosing directory containing `agent.yaml`
4. The working directory

## Manifest

`agent.yaml` is optional. A directory following the conventional layout
resolves identically; the manifest exists to pin a version, choose a preset, and
override paths.

| Key | Default | Purpose |
|---|---|---|
| `schema` | `1` | Manifest format. The agent refuses a schema newer than it supports. |
| `agent_version` | *(none)* | Version this workspace was written against. `diag validate` warns on a mismatch. |
| `extends` | `generic-prometheus` | Built-in preset the profile inherits from. |
| `profile` | `./profile`, else the workspace root | Integration profile directory. |
| `runbooks` | `./runbooks` | RAG corpus. |
| `scenarios` | `./scenarios.yaml` | Runbook E2E scenarios. |
| `blind_eval` | `./blind_eval.yaml` | Blind-eval dataset. |

```yaml
schema: 1
agent_version: 0.1.0
extends: spring-micrometer
profile: ./profile
runbooks: ./runbooks
scenarios: ./scenarios.yaml
blind_eval: ./blind_eval.yaml
```

Unknown keys are ignored with a warning, so a typo surfaces in `diag validate`
rather than silently doing nothing. A path you *declare* must exist — that is an
error, not a fallback — while a path you omit is simply absent, and tools skip
the checks that need it. This lets you adopt the corpus incrementally.

Presets are named by `extends` and ship inside the image:

- `generic-prometheus` — community `http_requests_total` naming. Every preset
  chain is rooted here, so a partial preset can never resolve a section to nothing.
- `spring-micrometer` — Spring Boot Micrometer (`http_server_requests_seconds_*`,
  HikariCP, JVM).

Presets carry naming conventions, not topology: `service_map.yaml` comes from
your profile only.

## Flat layout

Profile sections sitting directly in the workspace root are detected as the
profile, so a small host needs no `profile/` subdirectory:

```
diagnostic-agent/
├── agent.yaml
├── metrics_profile.yaml
├── logs_profile.yaml
├── redaction.yaml
├── prompt_profile.yaml
├── service_map.yaml
└── runbooks/
```

Both bundled examples use this layout — see
[`examples/hello-world/`](../examples/hello-world/) and
[`examples/spring-modular-monolith/`](../examples/spring-modular-monolith/).

## Precedence

**Environment variables > manifest > profile files > built-in presets.**

Environment wins so a container can retarget a setting without editing the host
repository. `AGENT_PROFILE_DIR` and `AGENT_RUNBOOKS_PATH` are derived from the
workspace only when they are not already set.

## Redaction is fail-closed

`redaction.yaml` rules **accumulate** across an `extends:` chain — your rules are
appended to the preset's secret scrubbing rather than replacing it. Reuse a
parent rule's `name` to override it.

The agent refuses to start when the resolved profile yields zero redaction
rules. This matters most with a mounted workspace: Docker turns a missing mount
source into an empty directory, which would otherwise shadow your profile and
silently disable redaction. `diag validate` and `GET /health` both report the
count. Set `AGENT_REQUIRE_REDACTION=false` to opt out deliberately.

## Validating in CI

```bash
docker run --rm -v "$PWD/infrastructure/diagnostic-agent:/workspace:ro" \
  ghcr.io/mskrado/diagnostic-agent:<tag> sh -c "diag validate && diag lint"
```

`validate` covers configuration; `lint` covers content. Neither needs LLM
credentials or a running stack. Add `diag e2e --url` once an agent is deployed.
