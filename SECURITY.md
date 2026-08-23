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

- The agent is **hypotheses-only by default** and must stay that way unless a
  host explicitly opts in. The LLM never emits commands; it can at most select a
  named action a host has pre-approved.
- Remediation, when enabled, is bounded by four controls that all fail closed:
  `AGENT_EXEC_ENABLED` (off by default), a host-supplied allowlist in
  `execution_profile.yaml` (presets ship zero actions), the destructive-action
  classifier, and a container with no network, no mounts, no secrets, dropped
  capabilities, and a read-only root filesystem. Actions are argv arrays — never
  shell strings — so parameters cannot be interpolated into a command. Weakening
  any of these is a security-relevant change; see
  [docs/design/sandboxed-execution.md](docs/design/sandboxed-execution.md).
- Runbooks retrieved via RAG become LLM context, and a runbook may also declare
  executable steps. Malicious runbook PRs are both a prompt-injection risk and,
  on a host with execution enabled, an action-selection risk; corpus lint +
  human review are mandatory.
- Redaction rules are host-configured; defaults scrub common secrets but hosts
  must add tenant / PII patterns for multi-tenant deployments.
