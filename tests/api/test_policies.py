import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from icloudpd_web.app import create_app
from icloudpd_web.auth import Authenticator

from .conftest import make_policy_body


FAKE = Path(__file__).resolve().parent.parent / "fixtures" / "fake_icloudpd.py"


def _fake_argv(argv_tail: list[str]) -> list[str]:
    return [sys.executable, str(FAKE), *argv_tail]


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(
        data_dir=tmp_path,
        authenticator=Authenticator(password_hash=Authenticator.hash("pw")),
        session_secret="s" * 32,
        icloudpd_argv=_fake_argv,
    )
    c = TestClient(app)
    c.post("/auth/login", json={"password": "pw"})
    return c


def _policy_body(name: str = "a") -> dict:
    return {
        "name": name,
        "username": "u@icloud.com",
        "directory": "/tmp/a",
        "cron": "0 * * * *",
        "enabled": True,
        "icloudpd": {"album": "All Photos"},
        "aws": None,
    }


def test_list_empty(client: TestClient) -> None:
    r = client.get("/policies")
    assert r.status_code == 200
    assert r.json() == []


def test_put_then_get(client: TestClient) -> None:
    r = client.put("/policies/a", json=_policy_body())
    assert r.status_code == 200
    r2 = client.get("/policies/a")
    assert r2.json()["username"] == "u@icloud.com"


def test_put_invalid_cron(client: TestClient) -> None:
    body = _policy_body()
    body["cron"] = "bogus"
    r = client.put("/policies/a", json=body)
    assert r.status_code == 422


def test_put_name_mismatch_rejected(client: TestClient) -> None:
    body = _policy_body(name="a")
    r = client.put("/policies/b", json=body)
    assert r.status_code == 422


def test_delete(client: TestClient) -> None:
    client.put("/policies/a", json=_policy_body())
    r = client.delete("/policies/a")
    assert r.status_code == 200
    assert client.get("/policies/a").status_code == 404


def test_set_password(client: TestClient) -> None:
    client.put("/policies/a", json=_policy_body())
    r = client.post("/policies/a/password", json={"password": "hunter2"})
    assert r.status_code == 204


# ── export / import ──────────────────────────────────────────────────────


def test_export_roundtrips(client: TestClient) -> None:
    client.put("/policies/a", json=_policy_body())
    client.put("/policies/b", json=_policy_body("b"))
    r = client.get("/policies/export")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/toml")
    text = r.text
    assert "[[policy]]" in text
    assert 'name = "a"' in text
    assert 'name = "b"' in text


def test_import_single_policy(client: TestClient) -> None:
    body = (
        'name = "newp"\n'
        'username = "u@icloud.com"\n'
        'directory = "/tmp/x"\n'
        'cron = "0 * * * *"\n'
        "enabled = true\n"
    )
    r = client.post("/policies/import", content=body)
    assert r.status_code == 200
    assert r.json() == {"created": ["newp"], "errors": []}
    assert client.get("/policies/newp").status_code == 200


def test_export_roundtrips_through_import(client: TestClient) -> None:
    """Export → (delete all) → import must reproduce the same policies.

    Guards against field renames or nesting changes in to_toml_dict that
    would silently break user backups.
    """
    # Seed two policies with non-default shape (filters, library_kind,
    # aws, icloudpd block) so we exercise all serialized fields.
    p1 = _policy_body("alpha")
    p1["library_kind"] = "personal"
    p1["filters"] = {
        "file_suffixes": [".jpg"],
        "match_patterns": [r"IMG_.*"],
        "device_makes": [],
        "device_models": [],
    }
    p1["icloudpd"] = {"album": "Selfies", "size": ["original"]}
    p2 = _policy_body("beta")
    p2["aws"] = {
        "enabled": True,
        "bucket": "my-bucket",
        "prefix": "photos",
        "region": "us-west-2",
    }
    client.put("/policies/alpha", json=p1)
    client.put("/policies/beta", json=p2)

    # Export
    exp = client.get("/policies/export")
    assert exp.status_code == 200
    assert exp.headers["content-type"].startswith("application/toml")
    toml_body = exp.text

    # Wipe state
    client.delete("/policies/alpha")
    client.delete("/policies/beta")
    assert client.get("/policies").json() == []

    # Import back
    imp = client.post("/policies/import", content=toml_body)
    assert imp.status_code == 200, imp.text
    assert set(imp.json()["created"]) == {"alpha", "beta"}
    assert imp.json()["errors"] == []

    # Round-tripped content matches
    got = {p["name"]: p for p in client.get("/policies").json()}
    assert got["alpha"]["library_kind"] == "personal"
    assert got["alpha"]["filters"]["file_suffixes"] == [".jpg"]
    assert got["alpha"]["filters"]["match_patterns"] == [r"IMG_.*"]
    assert got["alpha"]["icloudpd"]["album"] == "Selfies"
    assert got["beta"]["aws"]["bucket"] == "my-bucket"
    assert got["beta"]["aws"]["region"] == "us-west-2"
    assert got["beta"]["aws"]["enabled"] is True


def test_import_bundle(client: TestClient) -> None:
    body = (
        "[[policy]]\n"
        'name = "p1"\n'
        'username = "u@icloud.com"\n'
        'directory = "/tmp/p1"\n'
        'cron = "0 * * * *"\n'
        "[[policy]]\n"
        'name = "p2"\n'
        'username = "u@icloud.com"\n'
        'directory = "/tmp/p2"\n'
        'cron = "0 * * * *"\n'
    )
    r = client.post("/policies/import", content=body)
    assert r.status_code == 200
    assert set(r.json()["created"]) == {"p1", "p2"}


def test_import_ignores_illegal_fields(client: TestClient) -> None:
    body = (
        'name = "ok"\n'
        'username = "u@icloud.com"\n'
        'directory = "/tmp/ok"\n'
        'cron = "0 * * * *"\n'
        'nonexistent_top_level = "garbage"\n'
        "[icloudpd]\n"
        'device_make = "still-bogus"\n'  # dropped by validator
        'album = "Selfies"\n'
    )
    r = client.post("/policies/import", content=body)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == ["ok"]
    got = client.get("/policies/ok").json()
    assert "device_make" not in got["icloudpd"]
    assert got["icloudpd"]["album"] == "Selfies"


def test_import_rejects_existing_name(client: TestClient) -> None:
    client.put("/policies/dupe", json=_policy_body("dupe"))
    body = 'name = "dupe"\nusername = "x@icloud.com"\ndirectory = "/tmp/dupe"\ncron = "0 * * * *"\n'
    r = client.post("/policies/import", content=body)
    assert r.status_code == 200
    payload = r.json()
    assert payload["created"] == []
    assert payload["errors"] == [{"name": "dupe", "error": "already exists"}]


def test_import_rejects_invalid_toml(client: TestClient) -> None:
    r = client.post("/policies/import", content="not valid toml ====")
    assert r.status_code == 400


def test_import_rejects_empty_body(client: TestClient) -> None:
    r = client.post("/policies/import", content="")
    assert r.status_code == 400


def test_import_rejects_non_utf8(client: TestClient) -> None:
    r = client.post("/policies/import", content=b"\xff\xfe\xfd")
    assert r.status_code == 400


def test_import_skips_non_table_entry(client: TestClient) -> None:
    # policy = ["not a table"] — bundle-shaped but element is not a dict
    r = client.post("/policies/import", content='policy = ["bogus"]\n')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == []
    assert body["errors"] == [{"name": None, "error": "entry is not a table"}]


def test_requires_auth(tmp_path: Path) -> None:
    app = create_app(
        data_dir=tmp_path,
        authenticator=Authenticator(password_hash=Authenticator.hash("pw")),
        session_secret="s" * 32,
    )
    c = TestClient(app)
    r = c.get("/policies")
    assert r.status_code == 401


def test_smtp_credentials_redacted_in_get(app_factory: Callable[..., FastAPI]) -> None:
    app = app_factory()
    with TestClient(app) as client:
        client.post("/auth/login", json={"password": "pw"})
        body = make_policy_body("p")
        body["icloudpd"] = {
            "smtp_username": "mailer@example.com",
            "smtp_password": "hunter2",
            "smtp_host": "smtp.example.com",
        }
        r = client.put("/policies/p", json=body)
        assert r.status_code == 200
        # PUT response and both GET endpoints must not leak the secrets.
        assert r.json()["icloudpd"]["smtp_password"] == "__redacted__"
        assert r.json()["icloudpd"]["smtp_username"] == "__redacted__"
        for payload in (client.get("/policies").json()[0], client.get("/policies/p").json()):
            assert payload["icloudpd"]["smtp_password"] == "__redacted__"
            assert payload["icloudpd"]["smtp_username"] == "__redacted__"
            assert payload["icloudpd"]["smtp_host"] == "smtp.example.com"


def test_smtp_redaction_roundtrip_keeps_stored_secret(
    app_factory: Callable[..., FastAPI],
) -> None:
    """PUT with the redaction placeholder must keep the stored credential."""
    app = app_factory()
    with TestClient(app) as client:
        client.post("/auth/login", json={"password": "pw"})
        body = make_policy_body("p")
        body["icloudpd"] = {"smtp_username": "mailer@example.com", "smtp_password": "hunter2"}
        client.put("/policies/p", json=body)

        # Simulate the UI: GET (redacted), tweak something else, PUT back.
        fetched = client.get("/policies/p").json()
        update = make_policy_body("p")
        update["icloudpd"] = fetched["icloudpd"]
        update["icloudpd"]["smtp_host"] = "smtp.new.example.com"
        r = client.put("/policies/p", json=update)
        assert r.status_code == 200

        stored = app.state.policy_store.get("p")
        assert stored.icloudpd["smtp_password"] == "hunter2"
        assert stored.icloudpd["smtp_username"] == "mailer@example.com"
        assert stored.icloudpd["smtp_host"] == "smtp.new.example.com"


def test_export_omits_smtp_secrets_by_default(app_factory: Callable[..., FastAPI]) -> None:
    app = app_factory()
    with TestClient(app) as client:
        client.post("/auth/login", json={"password": "pw"})
        body = make_policy_body("p")
        body["icloudpd"] = {"smtp_username": "mailer@example.com", "smtp_password": "hunter2"}
        client.put("/policies/p", json=body)

        default = client.get("/policies/export").text
        assert "hunter2" not in default
        assert "mailer@example.com" not in default

        with_secrets = client.get("/policies/export?include_secrets=true").text
        assert "hunter2" in with_secrets
        assert "mailer@example.com" in with_secrets


def test_put_create_only_rejects_duplicate(client: TestClient) -> None:
    """PUT with If-None-Match:* (New Policy modal) must 409 on an existing
    name instead of silently overwriting it."""
    client.put("/policies/a", json=_policy_body())
    changed = _policy_body()
    changed["directory"] = "/tmp/other"
    r = client.put("/policies/a", json=changed, headers={"If-None-Match": "*"})
    assert r.status_code == 409
    assert r.json()["field"] == "name"
    # The original policy is untouched.
    assert client.get("/policies/a").json()["directory"] == "/tmp/a"
    # Create-only works when the name is free.
    r2 = client.put(
        "/policies/b",
        json=_policy_body("b") | {"directory": "/tmp/b"},
        headers={"If-None-Match": "*"},
    )
    assert r2.status_code == 200
    # Plain PUT (edit) still updates.
    r3 = client.put("/policies/a", json=changed)
    assert r3.status_code == 200
    assert client.get("/policies/a").json()["directory"] == "/tmp/other"
