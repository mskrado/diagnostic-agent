# Runbook: OpenAIApiErrors (LLM provider failures)

**Alert:** `ExternalDependencyErrors` — logs matching `openai|OpenAI|rate limit|429`.

## Meaning
The `ai` module cannot call OpenAI. AI-assisted features fail; other modules unaffected.

## First checks
1. Logs: `{service="platform-service"} | json | logger_name=~".*ai.*" | level="ERROR"`.
2. Confirm `OPENAI_API_KEY` present on platform-service (not just diagnostic-agent).
3. OpenAI status page / elevated 429 rate in logs.

## Common causes
- Invalid or expired API key.
- Rate limit / quota exhaustion.
- Model deprecation or wrong `OPENAI_MODEL`.

## Blast radius
`ai` module only; content generation and embedding features.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
