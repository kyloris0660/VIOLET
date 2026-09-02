"""Synthetic-only API acceptance for the PX3 Pixiv product integration."""

from __future__ import annotations

from pathlib import Path
import sys
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base  # noqa: E402
from app.models import SourceConceptProductRun  # noqa: E402
from app.routes.admin import pixiv_product_integration as routes  # noqa: E402


@pytest.fixture()
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("VIOLET_SKIP_DOTENV", "1")
    monkeypatch.setenv("VIOLET_ENV", "test")
    monkeypatch.setenv("VIOLET_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("VIOLET_TEST_STORAGE_ROOT", str(storage_root))
    monkeypatch.setenv("SCV2_PX3_PRODUCT_INTEGRATION_ENABLED", "1")
    monkeypatch.setenv("SCV2_PX3_PRODUCT_APPLY_ENABLED", "1")
    monkeypatch.setenv("SCV2_PX3_SYNTHETIC_UI_ENABLED", "1")

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/admin")
    app.dependency_overrides[routes.get_db] = lambda: session
    app.dependency_overrides[routes.require_admin_mode] = lambda: object()
    try:
        with TestClient(app) as client:
            yield client, session
    finally:
        app.dependency_overrides.clear()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_status_and_synthetic_dry_run_do_not_write(api_client) -> None:
    client, session = api_client
    status = client.get("/api/admin/pixiv-product-integration/status")
    assert status.status_code == 200
    assert status.json() == {
        "enabled": True,
        "apply_enabled": True,
        "synthetic_ui_enabled": True,
        "real_provider_execution_enabled": False,
        "run_count": 0,
        "active_run_count": 0,
        "latest_run": None,
        "owner_gates": {
            "controlled_provider_smoke": "not_authorized",
            "existing_database_canary": "not_authorized",
            "backup_restore": "not_authorized",
            "bounded_import_canary": "not_authorized",
        },
    }

    response = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={"mode": "dry_run"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "planned"
    assert body["counts"]["cluster_count"] == 20
    assert body["counts"]["candidate_disposition_count"] == 59
    assert body["counts"]["ambiguity_record_count"] == 29
    assert session.query(SourceConceptProductRun).count() == 0


def test_apply_query_rollback_and_reapply_are_auditable(api_client) -> None:
    client, session = api_client
    apply_response = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={
            "mode": "apply",
            "confirm": True,
            "confirm_phrase": "APPLY_SYNTHETIC_PIXIV_PRODUCT",
        },
    )
    assert apply_response.status_code == 200
    applied = apply_response.json()
    run_key = applied["run_key"]
    encoded_run_key = quote(run_key, safe="")
    assert applied["applied"] is True
    assert applied["operation_receipt"]["provider_network_activity"] == 0
    assert applied["operation_receipt"]["existing_database_or_app_storage_activity"] == 0

    replay = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={
            "mode": "apply",
            "confirm": True,
            "confirm_phrase": "APPLY_SYNTHETIC_PIXIV_PRODUCT",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["run_key"] == run_key

    listing = client.get("/api/admin/pixiv-product-integration/runs")
    assert listing.status_code == 200
    assert len(listing.json()["runs"]) == 1
    detail = client.get(
        f"/api/admin/pixiv-product-integration/runs/{encoded_run_key}"
    )
    assert detail.status_code == 200
    detail_body = detail.json()
    assert len(detail_body["clusters"]) == 20
    assert len(detail_body["candidate_dispositions"]) == 59
    assert len(detail_body["ambiguity_records"]) == 29

    invalid_rollback = client.post(
        f"/api/admin/pixiv-product-integration/runs/{encoded_run_key}/rollback",
        json={"confirm": True, "confirm_phrase": "wrong"},
    )
    assert invalid_rollback.status_code == 400
    rollback = client.post(
        f"/api/admin/pixiv-product-integration/runs/{encoded_run_key}/rollback",
        json={
            "confirm": True,
            "confirm_phrase": f"ROLLBACK_PIXIV_PRODUCT:{run_key}",
        },
    )
    assert rollback.status_code == 200
    assert rollback.json()["status"] == "rolled_back"
    assert rollback.json()["product_audit_rows_retained"] is True

    reapplied = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={
            "mode": "apply",
            "confirm": True,
            "confirm_phrase": "APPLY_SYNTHETIC_PIXIV_PRODUCT",
        },
    )
    assert reapplied.status_code == 200
    assert reapplied.json()["status"] == "active"
    assert session.query(SourceConceptProductRun).count() == 1


def test_independent_feature_flags_fail_closed(api_client, monkeypatch) -> None:
    client, _session = api_client
    monkeypatch.setenv("SCV2_PX3_PRODUCT_INTEGRATION_ENABLED", "0")
    disabled = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={"mode": "dry_run"},
    )
    assert disabled.status_code == 403
    assert disabled.json()["detail"] == "px3_product_integration_disabled"

    monkeypatch.setenv("SCV2_PX3_PRODUCT_INTEGRATION_ENABLED", "1")
    monkeypatch.setenv("SCV2_PX3_PRODUCT_APPLY_ENABLED", "0")
    apply_disabled = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={
            "mode": "apply",
            "confirm": True,
            "confirm_phrase": "APPLY_SYNTHETIC_PIXIV_PRODUCT",
        },
    )
    assert apply_disabled.status_code == 403
    assert apply_disabled.json()["detail"] == "px3_product_apply_disabled"

    monkeypatch.setenv("SCV2_PX3_SYNTHETIC_UI_ENABLED", "0")
    synthetic_disabled = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={"mode": "dry_run"},
    )
    assert synthetic_disabled.status_code == 403
    assert synthetic_disabled.json()["detail"] == "px3_synthetic_ui_disabled"


def test_canary_percentage_is_strict_and_not_accepted_by_synthetic_route(
    api_client,
) -> None:
    client, _session = api_client
    unbounded = client.post(
        "/api/admin/pixiv-product-integration/source-metadata/run",
        json={"mode": "dry_run"},
    )
    assert unbounded.status_code == 400
    assert (
        unbounded.json()["detail"]
        == "px3_existing_source_requires_bounded_canary"
    )
    bool_percent = client.post(
        "/api/admin/pixiv-product-integration/source-metadata/run",
        json={"mode": "dry_run", "canary_percent": True},
    )
    assert bool_percent.status_code == 422
    above_bound = client.post(
        "/api/admin/pixiv-product-integration/source-metadata/run",
        json={"mode": "dry_run", "canary_percent": 6},
    )
    assert above_bound.status_code == 409
    assert above_bound.json()["detail"] == "px3_canary_percentage_invalid"
    synthetic = client.post(
        "/api/admin/pixiv-product-integration/synthetic/run",
        json={"mode": "dry_run", "canary_percent": 1},
    )
    assert synthetic.status_code == 400
    assert synthetic.json()["detail"] == "px3_canary_only_for_existing_source"
