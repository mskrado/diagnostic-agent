# Runbook: TwilioSmsFailures (SMS/voice)

**Alert:** `ExternalDependencyErrors` — logs matching `twilio|TwilioRestException|21211`.

## Meaning
SMS or voice notifications fail. MFA/SMS alerts may not deliver.

## First checks
1. Logs: `{service="platform-service"} | json | logger_name=~".*notification.*" |~ "twilio"`.
2. Verify `TWILIO_ACCOUNT_SID`, auth token, and `TWILIO_PHONE_NUMBER`.
3. Twilio console for error codes on recent messages.

## Common causes
- Invalid from-number or unverified destination (trial account).
- Insufficient balance or account suspension.
- Webhook signature mismatch (inbound).

## Blast radius
`notification` module; SMS-based flows only.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:13:40.400Z","level":"ERROR","logger_name":"com.publishi.platform.notification.TwilioSmsClient","service":"platform-service","trace_id":"56789abcdef0123456789012345678901","message":"Twilio SMS failed: TwilioRestException: HTTP 503 — Upstream Twilio API error; Unable to create record"}
{"@timestamp":"2026-07-20T20:13:43.910Z","level":"ERROR","logger_name":"com.publishi.platform.notification.TwilioSmsClient","service":"platform-service","trace_id":"6789abcdef0123456789012345678903","message":"Twilio SMS failed: TwilioRestException: HTTP 400 code=21211 — The 'To' number +1000 is not a valid phone number"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
