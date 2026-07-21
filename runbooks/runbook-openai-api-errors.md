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

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:13:01.100Z","level":"ERROR","logger_name":"com.publishi.platform.ai.OpenAiClient","service":"platform-service","trace_id":"23456789abcdef0123456789012345678","message":"OpenAI API error: 429 Too Many Requests — Rate limit reached for requests; retry-after=21s"}
{"@timestamp":"2026-07-20T20:13:04.900Z","level":"ERROR","logger_name":"com.publishi.platform.ai.OpenAiClient","service":"platform-service","trace_id":"3456789abcdef01234567890123456780","message":"OpenAI health check failed: 401 Unauthorized — Incorrect API key provided"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
