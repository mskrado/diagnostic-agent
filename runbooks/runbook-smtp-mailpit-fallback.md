# Runbook: SmtpMailpitFallback (PROD mailer stuck on Mailpit)

**Alert:** `SmtpMailpitFallback` — Loki sees DB email-settings apply failure
and/or mail sender rebuilt with `host=mailpit` (15m window).

## Meaning
Platform intended to use Admin/DB SMTP (e.g. `smtp-relay.gmail.com:465`) but
the live `JavaMailSender` never received those settings. Compose defaults
(`SMTP_HOST=mailpit`, `SMTP_PORT=1025`) remain active. MFA PIN emails are
accepted by the local Mailpit container (or fail there) and **never reach
user inboxes**. Login still returns `MFA_REQUIRED`.

## First checks
1. Loki startup: `Failed to apply DB email settings to mail sender` (often
   `JavaMailSenderImpl.setHost(...) because "sender" is null`).
2. Loki: `Rebuilding mail sender: host=mailpit` vs `host=smtp-relay.gmail.com`.
3. Container env: `docker inspect publishi-platform` → `SMTP_HOST` / `SMTP_PORT`.
4. RDS `system_settings` where `setting_key LIKE 'email.%'` (host/port/user).
5. Mailpit UI / API (`SMTPAccepted` count): PINs may be trapped there on DEV;
   on PROD Mailpit must not be the live relay.

## Common causes
- Startup race: `SystemSettingsService` `@PostConstruct` calls
  `rebuildMailSender` before `DynamicMailSenderConfig.javaMailSender()` sets
  the `mailSender` field → NPE swallowed → env Mailpit defaults stick.
- PROD `.env` never set real `SMTP_*` (compose default `mailpit:1025`).
- Admin UI “Save Email” never re-run after a bad start (re-save reapplies
  settings once the bean exists).

## Blast radius
All outbound app email (MFA PIN, verify-email, password reset, invites).
Alertmanager / diagnostic-agent SMTP paths are separate configs.

## Example log lines (synthetic)
```json
{"@timestamp":"2026-07-19T15:16:04.854Z","level":"INFO","logger_name":"com.publishi.platform.settings.config.DynamicMailSenderConfig","service":"platform-service","message":"Rebuilding mail sender: host=smtp-relay.gmail.com, port=465, tls=true"}
{"@timestamp":"2026-07-19T15:16:04.855Z","level":"WARN","logger_name":"com.publishi.platform.settings.service.SystemSettingsService","service":"platform-service","message":"Failed to apply DB email settings to mail sender: Cannot invoke \"org.springframework.mail.javamail.JavaMailSenderImpl.setHost(String)\" because \"sender\" is null"}
{"@timestamp":"2026-07-19T15:16:05.010Z","level":"INFO","logger_name":"com.publishi.platform.settings.config.DynamicMailSenderConfig","service":"platform-service","message":"Rebuilding mail sender: host=mailpit, port=1025, tls=false"}
```

## Hypotheses-only
This runbook supports surfacing hypotheses. Do NOT auto-remediate; a human
confirms and acts.
