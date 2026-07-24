# Runbook: SmtpDeliveryFailures (email outbound — umbrella)

**Alert:** Prefer dedicated alerts `SmtpConnectionFailures` and
`SmtpMailpitFallback`. Legacy / broad log noise may still surface under
`ExternalApiErrorsInLogs` without the `smtp` token.

## Meaning
Outbound email from the `notification` module is failing or never leaving the
box. Prefer the specific runbooks below when log evidence matches.

## Related runbooks
| Symptom | Runbook |
|---|---|
| Connect / TLS / 535 auth failures to a real relay | `runbook-smtp-connection-failures.md` |
| Live sender stuck on `mailpit:1025` after DB apply failure | `runbook-smtp-mailpit-fallback.md` |
| ENOSPC / host full cascading into send failures | `runbook-host-disk-pressure.md` |

## First checks
1. Loki: `{service="platform-service"} |~ "(?i)smtp|MailSend|MessagingException|mail sender|mailpit"`.
2. Admin SMTP settings vs env `SMTP_HOST` / `EMAIL_ENABLED`.
3. Mailpit message count (DEV) vs real inbox (PROD).

## Blast radius
`notification` module; auth flows relying on email (MFA PIN, verify, reset).

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
