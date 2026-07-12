"""Pre-existing test, NOT in touched-set — references the touched
route via its HTTP path. Reviewer-phase partial-mirror wouldn't load
this test; v0.20.0's check-test-reach surfaces the cross-touch-set
reference at reviewing time."""


def test_audit_skips_endpoint_shape(client):
    resp = client.get("/audit/skips")
    assert resp.status_code == 200
    assert "skips" in resp.json()
