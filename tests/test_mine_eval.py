"""Phase 4: redaction before→after review and blind-eval mining."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.draft import plan, redaction
from app.draft.plan import DraftOptions
from app.draft.verify import StubOracle
from app.mine_eval import mine_paths, mine_records, render_dataset
from app.scan import scrub
from app.scan.models import (
    AlertRule,
    Findings,
    LokiEvidence,
    PrometheusEvidence,
    ScanEvidence,
    ServiceCandidate,
)
from app.scan.scrub import SecretHit, census, scrub_text


def _evidence_with_secrets() -> ScanEvidence:
    raw = [
        "user=ops@example.com tenant_id=tenant-9 failed",
        "Authorization: Bearer "
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "signaturevaluehere01",
    ]
    hits = census(raw)
    return ScanEvidence(
        generated_at="now",
        agent_version="test",
        prometheus=PrometheusEvidence(
            reachable=True,
            label_values={"service": ("app",)},
            metric_names=("up",),
        ),
        loki=LokiEvidence(
            reachable=True,
            service_label="service",
            label_values={"service": ("app",)},
            secrets=tuple(hits),
        ),
        findings=Findings(
            services=(ServiceCandidate("app", has_metrics=True, has_logs=True),)
        ),
    )


def test_census_includes_safe_before_after_examples():
    hits = {h.name: h for h in census(["contact ops@example.com please"])}
    assert "email" in hits
    example = hits["email"].examples[0]
    assert "ops@example.com" not in example.before_display
    assert "«email»" in example.before_display
    assert "[EMAIL-REDACTED]" in example.after
    assert "ops@example.com" not in example.after


def test_census_examples_round_trip_in_bundle():
    evidence = _evidence_with_secrets()
    restored = ScanEvidence.from_dict(json.loads(json.dumps(evidence.to_dict())))
    assert restored.loki.secrets[0].examples
    assert "«" in restored.loki.secrets[0].examples[0].before_display


def test_redaction_review_lists_before_after():
    review = redaction.draft_redaction_review(_evidence_with_secrets())
    assert review is not None
    assert review.path == "redaction-review.md"
    assert "before (match marked)" in review.content
    assert "«email»" in review.content or "«bearer_token»" in review.content
    # Raw secrets must not appear in the review.
    assert "ops@example.com" not in review.content
    assert "eyJhbGci" not in review.content


def test_draft_includes_redaction_review():
    oracle = StubOracle(promql_ok=lambda q: True, logql_ok=lambda q: True)
    result = plan.draft(
        _evidence_with_secrets(),
        DraftOptions(prometheus_url="http://prometheus:9090"),
        oracle,
    )
    paths = {f.path for f in result.files}
    assert "redaction.yaml" in paths
    assert "redaction-review.md" in paths


def _audit_line(**overrides) -> str:
    report = {
        "service": "platform-service",
        "alert_type": "PostgresErrorsInLogs",
        "severity": "critical",
        "diagnosis": {
            "primary_hypothesis": {
                "cause": "PostgreSQL connection refused on port 5432"
            }
        },
        "evidence": {
            "metrics": {"platform-service": {"up": 0}},
            "error_log_sample": [
                '{"level":"ERROR","message":"Connection to postgres:5432 refused"}',
                '{"level":"ERROR","message":"HikariPool initialization failed for postgres"}',
                '{"level":"ERROR","message":"could not open JDBC connection"}',
            ],
        },
    }
    report.update(overrides)
    return json.dumps({"report": report, "llm_raw": "{}"})


def test_mine_eval_builds_grounded_cases(tmp_path):
    audit = tmp_path / "diagnostics-2026-08-30.jsonl"
    audit.write_text(_audit_line() + "\n", encoding="utf-8")

    result = mine_paths([tmp_path])
    assert len(result.cases) == 1
    case = result.cases[0].case
    assert case["alert"]["alertname"] == "PostgresErrorsInLogs"
    assert case["alert"]["service"] == "platform-service"
    assert len(case["logs"]) >= 2
    for token in case["expected"]["must_reference"]:
        joined = "\n".join(case["logs"]).lower()
        assert str(token).lower() in joined
    assert "5432" in case["expected"]["root_cause"] or "postgres" in case["expected"]["root_cause"].lower()


def test_mine_eval_scrubs_secrets_in_logs(tmp_path):
    line = _audit_line(
        evidence={
            "error_log_sample": [
                'login failed for ops@example.com on postgres',
                'retry ops@example.com against postgres:5432',
            ]
        }
    )
    # Inject an email that should be scrubbed even if audit missed it.
    path = tmp_path / "a.jsonl"
    path.write_text(line + "\n", encoding="utf-8")
    result = mine_paths([path])
    assert result.cases
    blob = "\n".join(result.cases[0].case["logs"])
    assert "ops@example.com" not in blob
    assert "[EMAIL-REDACTED]" in blob or "«email»" in blob or scrub_text("ops@example.com") in blob


def test_mine_eval_skips_short_and_duplicate(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text(
        "\n".join(
            [
                _audit_line(),
                _audit_line(),  # duplicate fingerprint
                json.dumps(
                    {
                        "report": {
                            "alert_type": "X",
                            "service": "s",
                            "diagnosis": {"primary_hypothesis": {"cause": "c"}},
                            "evidence": {"error_log_sample": ["only one"]},
                        }
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = mine_paths([path], min_logs=2)
    assert len(result.cases) == 1
    assert any("duplicate" in s for s in result.skipped)
    assert any("fewer than" in s for s in result.skipped)


def test_mine_eval_cli_writes_draft_and_refuses_clobber(tmp_path, capsys):
    from app.cli import main

    audits = tmp_path / "audit"
    audits.mkdir()
    (audits / "diagnostics-1.jsonl").write_text(_audit_line() + "\n", encoding="utf-8")
    out = tmp_path / "blind_eval.draft.yaml"

    assert main(["mine-eval", "--audits", str(audits), "-o", str(out)]) == 0
    assert out.is_file()
    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    assert data["cases"]

    assert main(["mine-eval", "--audits", str(audits), "-o", str(out)]) == 1
    assert "already exists" in capsys.readouterr().err

    assert main(["mine-eval", "--audits", str(audits), "-o", str(out), "--force"]) == 0


def test_render_dataset_marks_itself_as_draft():
    result = mine_records(
        [
            (
                "t:1",
                {
                    "report": {
                        "service": "app",
                        "alert_type": "HighErrorRate",
                        "severity": "warning",
                        "diagnosis": {
                            "primary_hypothesis": {"cause": "5xx spike on app"}
                        },
                        "evidence": {
                            "error_log_sample": [
                                "ERROR status=500 on app",
                                "ERROR upstream timeout on app",
                            ]
                        },
                    }
                },
            )
        ]
    )
    text = render_dataset(result)
    assert "Draft blind-eval" in text
    assert "cases:" in text
