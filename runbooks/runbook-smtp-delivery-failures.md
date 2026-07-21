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

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-20T20:13:28.300Z","level":"ERROR","logger_name":"com.publishi.platform.notification.SmtpMailSender","service":"platform-service","trace_id":"456789abcdef012345678901234567890","message":"SMTP delivery failed: MessagingException: Could not connect to SMTP host: smtp.example.com, port: 587; nested exception is java.net.ConnectException: Connection timed out"}
{"@timestamp":"2026-07-20T20:13:31.770Z","level":"ERROR","logger_name":"com.publishi.platform.notification.SmtpMailSender","service":"platform-service","trace_id":"56789abcdef0123456789012345678902","message":"SMTP send failed: 535 5.7.8 Authentication credentials invalid — check stored SMTP password / SETTINGS_ENCRYPTION_KEY"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
