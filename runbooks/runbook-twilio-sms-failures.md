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

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
