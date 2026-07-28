# Security Policy

---

## Topics

1. [Supported versions](#supported-versions)
2. [Reporting a vulnerability](#reporting-a-vulnerability)
3. [Threat model notes](#threat-model-notes)

---

## Supported versions

Security fixes are applied to the latest released tag on `main`.

## Reporting a vulnerability

Please **do not** open a public GitHub issue for security reports.

Use GitHub **Security Advisories** on this repository, or email the maintainers
listed in the repository profile.

Include:

- Affected version / commit
- Reproduction steps
- Impact assessment (especially anything that could influence LLM output toward
  unsafe remediation advice, or leak secrets via RAG corpus injection)

## Threat model notes

- The agent is **hypotheses-only** — it must not execute remediation.
- Runbooks retrieved via RAG become LLM context. Malicious runbook PRs are a
  prompt-injection risk; corpus lint + human review are mandatory.
- Redaction rules are host-configured; defaults scrub common secrets but hosts
  must add tenant / PII patterns for multi-tenant deployments.
