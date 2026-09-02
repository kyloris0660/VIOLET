"""Focused product, persistence, migration, and public-safety tests for PX3."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import (  # noqa: E402
    Base,
    migrate_add_source_concept_product_integration,
    migrate_add_source_concept_resolver_core,
)
from app.models import (  # noqa: E402
    SourceConceptCandidateDisposition,
    SourceConceptProductRun,
)
from app.services.pixiv_metadata_clustering_service import (  # noqa: E402
    build_pixiv_clustering,
    consume_px1_public_summary,
)
from app.services.pixiv_metadata_projection_service import (  # noqa: E402
    assert_public_safe_projection,
    canonical_fingerprint,
)
from app.services.pixiv_metadata_vertical_slice_service import (  # noqa: E402
    repository_synthetic_pixiv_fixture,
    run_synthetic_pixiv_vertical_slice,
)
from app.services.pixiv_product_integration_service import (  # noqa: E402
    PX3_ALLOWED_PRODUCT_TABLES,
    PX3_CONTRACT_ID,
    PX3_PUBLIC_SCHEMA,
    PixivProductIntegrationError,
    apply_pixiv_product_plan,
    build_pixiv_product_plan,
    get_pixiv_product_run,
    list_pixiv_product_runs,
    prove_task_owned_product_persistence,
    rollback_pixiv_product_run,
)
from app.services.source_concept_resolver_service import (  # noqa: E402
    build_source_concept_input_scope,
    persist_source_concept_resolution,
)


@pytest.fixture(scope="module")
def px3_clustering(tmp_path_factory: pytest.TempPathFactory):
    workspace = tmp_path_factory.mktemp("scv2-px3-input")
    runtime = tmp_path_factory.mktemp("scv2-px3-runtime")
    overrides = {
        "VIOLET_SKIP_DOTENV": "1",
        "VIOLET_ENV": "test",
        "POSTGRES_DB": "scv2_px3_test_temp",
        "TEST_DATABASE_URL": "",
        "VIOLET_STORAGE_ROOT": os.fspath(runtime),
        "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime),
    }
    previous = {key: os.environ.get(key) for key in overrides}
    os.environ.update(overrides)
    try:
        summary = run_synthetic_pixiv_vertical_slice(
            workspace=workspace,
            fixture=repository_synthetic_pixiv_fixture(),
        )
        yield build_pixiv_clustering(consume_px1_public_summary(summary))
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'product.sqlite3').as_posix()}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)()


def test_product_plan_reuses_px2_and_accounts_for_every_record(px3_clustering) -> None:
    plan = build_pixiv_product_plan(
        px3_clustering,
        scope_key="pixiv:repository-synthetic",
        source_mode="repository_synthetic",
    )
    assert plan["schema_version"] == PX3_PUBLIC_SCHEMA
    assert plan["contract_id"] == PX3_CONTRACT_ID
    assert plan["counts"] == {
        "cluster_count": 20,
        "member_signal_count": 34,
        "candidate_disposition_count": 59,
        "ambiguity_record_count": 29,
        "must_link_count": 52,
        "cannot_link_count": 4,
        "deferred_nonblocking_count": 3,
    }
    assert len({row["pair_key"] for row in plan["candidate_dispositions"]}) == 59
    assert len({row["record_key"] for row in plan["ambiguity_records"]}) == 29
    assert plan["product_result_fingerprint"] == canonical_fingerprint(
        {
            "scope_key": plan["scope_key"],
            "source_mode": plan["source_mode"],
            "px1_input_fingerprint": plan["px1_input_fingerprint"],
            "px2_business_projection_fingerprint": plan[
                "px2_business_projection_fingerprint"
            ],
            "resolver_version": plan["resolver_version"],
            "context_policy_version": "scv2_px2_pixiv_role_context_policy_v1",
            "candidate_policy_version": "scv2_px2_resolver_candidate_disposition_v1",
            "product_policy_version": plan["product_policy_version"],
            "clusters": plan["clusters"],
            "candidate_dispositions": plan["candidate_dispositions"],
            "ambiguity_records": plan["ambiguity_records"],
        }
    )


def test_dry_run_apply_replay_query_and_rollback_are_real_boundaries(
    tmp_path: Path, px3_clustering
) -> None:
    engine, db = _session(tmp_path)
    try:
        dry_run = apply_pixiv_product_plan(
            db,
            px3_clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=False,
        )
        assert dry_run["status"] == "planned"
        assert list_pixiv_product_runs(db) == []

        applied = apply_pixiv_product_plan(
            db,
            px3_clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        replay = apply_pixiv_product_plan(
            db,
            px3_clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        assert applied["applied"] is True
        assert replay["idempotent_replay"] is True
        assert replay["persistence"]["replay_row_delta_count"] == 0
        assert db.query(SourceConceptProductRun).count() == 1
        detail = get_pixiv_product_run(db, applied["run_key"])
        assert detail is not None
        assert len(detail["clusters"]) == 20
        assert len(detail["candidate_dispositions"]) == 59
        assert len(detail["ambiguity_records"]) == 29
        assert all("id" not in row for row in detail["clusters"])

        rollback = rollback_pixiv_product_run(db, applied["run_key"])
        rollback_replay = rollback_pixiv_product_run(db, applied["run_key"])
        assert rollback["rolled_back"] is True
        assert rollback["product_audit_rows_retained"] is True
        assert rollback_replay["idempotent_replay"] is True
        assert get_pixiv_product_run(db, applied["run_key"])["status"] == "rolled_back"

        reapplied = apply_pixiv_product_plan(
            db,
            px3_clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        assert reapplied["product_result_fingerprint"] == applied[
            "product_result_fingerprint"
        ]
        assert db.query(SourceConceptProductRun).count() == 1
    finally:
        db.close()
        engine.dispose()


def test_replay_detects_persisted_candidate_drift(
    tmp_path: Path, px3_clustering
) -> None:
    engine, db = _session(tmp_path)
    try:
        apply_pixiv_product_plan(
            db,
            px3_clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        row = db.query(SourceConceptCandidateDisposition).first()
        row.reason_code = "mutated_reason"
        db.commit()
        with pytest.raises(
            PixivProductIntegrationError, match="persisted_projection_drift"
        ):
            apply_pixiv_product_plan(
                db,
                px3_clustering,
                scope_key="pixiv:repository-synthetic",
                source_mode="repository_synthetic",
                apply=True,
            )
    finally:
        db.close()
        engine.dispose()


def test_rollback_refuses_preexisting_core_scope(
    tmp_path: Path, px3_clustering
) -> None:
    engine, db = _session(tmp_path)
    try:
        persist_source_concept_resolution(
            db,
            px3_clustering.resolution,
            apply=True,
            input_scope=build_source_concept_input_scope(
                px3_clustering.resolution.signals,
                source_run_ids=[px3_clustering.resolution.run_id],
            ),
            run_label="preexisting_scope",
        )
        applied = apply_pixiv_product_plan(
            db,
            px3_clustering,
            scope_key="pixiv:repository-synthetic",
            source_mode="repository_synthetic",
            apply=True,
        )
        assert applied["persistence"]["rollback_available"] is False
        with pytest.raises(
            PixivProductIntegrationError, match="rollback_guard_not_satisfied"
        ):
            rollback_pixiv_product_run(db, applied["run_key"])
    finally:
        db.close()
        engine.dispose()


def test_temporary_persistence_proof_covers_full_lifecycle(
    tmp_path: Path, px3_clustering
) -> None:
    proof = prove_task_owned_product_persistence(px3_clustering, workspace=tmp_path)
    assert proof["temporary_persistence_idempotent"] is True
    assert proof["dry_run_product_row_delta_count"] == 0
    assert proof["replay_row_delta_count"] == 0
    assert proof["query_projection_complete"] is True
    assert proof["rollback_succeeded"] is True
    assert proof["reapply_after_rollback_succeeded"] is True
    assert proof["product_run_count"] == 1
    assert proof["forbidden_truth_table_write_count"] == 0


def test_product_migration_is_additive_and_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'migration.sqlite3').as_posix()}")
    try:
        migrate_add_source_concept_resolver_core(engine, inspect(engine))
        migrate_add_source_concept_product_integration(engine, inspect(engine))
        migrate_add_source_concept_product_integration(engine, inspect(engine))
        tables = set(inspect(engine).get_table_names())
        assert set(PX3_ALLOWED_PRODUCT_TABLES) <= tables
        assert not {
            "blombooru_entities",
            "blombooru_media_tags",
            "blombooru_provider_cache",
        } & set(PX3_ALLOWED_PRODUCT_TABLES)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("scope_key", "source_mode"),
    [
        ("Pixiv:repository-synthetic", "repository_synthetic"),
        ("pixiv:repository synthetic", "repository_synthetic"),
        ("pixiv:repository-synthetic", "provider_network"),
    ],
)
def test_invalid_scope_or_source_mode_fails_closed(
    px3_clustering, scope_key: str, source_mode: str
) -> None:
    with pytest.raises(PixivProductIntegrationError):
        build_pixiv_product_plan(
            px3_clustering,
            scope_key=scope_key,
            source_mode=source_mode,
        )


def test_public_projection_excludes_private_paths_raw_payloads_and_row_ids(
    px3_clustering,
) -> None:
    plan = build_pixiv_product_plan(
        px3_clustering,
        scope_key="pixiv:repository-synthetic",
        source_mode="repository_synthetic",
    )
    assert_public_safe_projection(plan)
    encoded = json.dumps(plan, ensure_ascii=False, sort_keys=True).casefold()
    assert "c:\\" not in encoded
    assert "file://" not in encoded
    assert "password" not in encoded
    assert '"credential"' not in encoded
    assert '"credentials"' not in encoded
    assert "authorization: bearer" not in encoded
    assert "raw_metadata_json" not in encoded
    assert '"id"' not in encoded


def test_coordinated_projection_mutation_changes_product_identity(px3_clustering) -> None:
    plan = build_pixiv_product_plan(
        px3_clustering,
        scope_key="pixiv:repository-synthetic",
        source_mode="repository_synthetic",
    )
    mutated = copy.deepcopy(plan)
    mutated["candidate_dispositions"][0]["reason_code"] = "mutated"
    mutated["canonical_fingerprint"] = canonical_fingerprint(
        {
            key: value
            for key, value in mutated.items()
            if key != "canonical_fingerprint"
        }
    )
    assert mutated["canonical_fingerprint"] != plan["canonical_fingerprint"]
