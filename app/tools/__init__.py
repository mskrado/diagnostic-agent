"""Host-facing tooling: corpus lint, runbook scenarios, blind eval.

Each module takes a :class:`app.workspace.Workspace` rather than resolving
paths from this repository, so the same code serves both a host project's
workspace and this repository's own content in CI.
"""
