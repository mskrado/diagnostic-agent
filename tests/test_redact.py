from app.delivery.redact import redact_text


def test_redacts_tenant_token():
    assert "tenant-42" not in redact_text("error for tenant-42 listing")


def test_redacts_tenant_id_kv():
    out = redact_text('{"tenantId":"acme-corp","level":"ERROR"}')
    assert "acme-corp" not in out
    assert "level" in out  # non-tenant fields preserved


def test_redacts_uuid():
    out = redact_text("user 550e8400-e29b-41d4-a716-446655440000 failed")
    assert "550e8400-e29b-41d4-a716-446655440000" not in out
