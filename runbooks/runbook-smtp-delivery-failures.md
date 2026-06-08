# Runbook: SmtpDeliveryFailures (email outbound)

**Alert:** `ExternalDependencyErrors` — logs matching `smtp|MailSend|MessagingException|535`.

## Meaning
The `notification` module cannot send email. Password resets, invites, and alerts queue or fail.

## First checks
1. Logs: `{service="platform-service"} | json | logger_name=~".*notification.*" | level="ERROR"`.
2. Admin-panel SMTP settings (encrypted at rest) vs env fallbacks.
3. Provider bounce/block lists if partial delivery.

## Common causes
- Bad credentials or TLS mismatch.
- Provider IP reputation / rate limits.
- `SETTINGS_ENCRYPTION_KEY` rotation breaking stored SMTP password.

## Blast radius
`notification` module; auth flows relying on email.

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
