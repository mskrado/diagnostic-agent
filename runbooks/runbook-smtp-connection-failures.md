# Runbook: SmtpConnectionFailures (outbound SMTP unreachable)

**Alert:** `SmtpConnectionFailures` — Loki rate of SMTP connect / auth / delivery
errors in `platform-service` logs for 5m.

## Meaning
The notification module cannot open or authenticate an SMTP session to the
configured relay (Gmail / SES / SendGrid / etc.). MFA login PINs, password
resets, and invites fail while the API may still return success (async send).

## First checks
1. Loki: `{service="platform-service"} |~ "(?i)smtp|MailSend|MessagingException|535|Could not connect to SMTP"`.
2. Confirm live mailer host (Admin → System → Email, or startup log
   `Rebuilding mail sender: host=`). Distinguish real relay vs `mailpit`.
3. From the platform container: TCP reachability to `smtp-relay.*:465|587`.
4. Provider console: credential validity, IP allowlists, daily send quotas.

## Common causes
- Wrong host/port/TLS (587 STARTTLS vs 465 implicit SSL).
- Bad SMTP password or `SETTINGS_ENCRYPTION_KEY` / `JWT_SECRET` rotation
  breaking the encrypted password in `system_settings`.
- Provider rejecting auth (`535 5.7.8`) or blocking the EC2 egress IP.
- Transient DNS / network partition to the relay.

## Blast radius
`notification` module; auth MFA email, registration verification, password
reset, team invites. Other modules unaffected.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-24T05:43:50.100Z","level":"ERROR","logger_name":"com.publishi.notification.service.impl.EmailServiceImpl","service":"platform-service","trace_id":"a1b2c3d4e5f678901234567890abcdef","message":"Failed to send template email to: user@example.com","stack_trace":"org.springframework.mail.MailSendException: Mail server connection failed; nested exception is jakarta.mail.MessagingException: Could not connect to SMTP host: smtp-relay.gmail.com, port: 465"}
{"@timestamp":"2026-07-24T05:43:52.220Z","level":"ERROR","logger_name":"com.publishi.notification.config.AsyncConfig$AsyncExceptionHandler","service":"platform-service","trace_id":"a1b2c3d4e5f678901234567890abcdef","message":"Async exception in method sendTemplate: SMTP send failed: 535 5.7.8 Authentication credentials invalid — check stored SMTP password / SETTINGS_ENCRYPTION_KEY"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
