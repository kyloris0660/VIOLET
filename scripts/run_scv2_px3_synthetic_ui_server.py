#!/usr/bin/env python3
"""Serve the real admin template against a task-owned PX3 SQLite database."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from fastapi import Request


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class Px3SyntheticServerError(RuntimeError):
    pass


def _task_workspace(path: Path) -> Path:
    root = path.resolve(strict=True)
    temp_root = Path(tempfile.gettempdir()).resolve(strict=True)
    try:
        root.relative_to(temp_root)
    except ValueError as exc:
        raise Px3SyntheticServerError("px3_server_workspace_not_task_temp") from exc
    if not root.is_dir():
        raise Px3SyntheticServerError("px3_server_workspace_invalid")
    return root


def create_app(workspace: Path):
    root = _task_workspace(workspace)
    database_path = root / "px3-synthetic-ui.sqlite3"
    runtime_storage = root / "runtime-storage"
    if database_path.exists():
        raise Px3SyntheticServerError("px3_server_database_already_exists")
    runtime_storage.mkdir(exist_ok=False)
    os.environ.update(
        {
            "VIOLET_SKIP_DOTENV": "1",
            "VIOLET_ENV": "test",
            "POSTGRES_DB": "scv2_px3_browser_temp",
            "TEST_DATABASE_URL": f"sqlite:///{database_path.as_posix()}",
            "VIOLET_STORAGE_ROOT": os.fspath(runtime_storage),
            "VIOLET_TEST_STORAGE_ROOT": os.fspath(runtime_storage),
            "SCV2_PX3_PRODUCT_INTEGRATION_ENABLED": "1",
            "SCV2_PX3_PRODUCT_APPLY_ENABLED": "1",
            "SCV2_PX3_SYNTHETIC_UI_ENABLED": "1",
        }
    )

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse, FileResponse
    from fastapi.staticfiles import StaticFiles
    from fastapi.templating import Jinja2Templates
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from app.auth import require_admin_mode, get_current_admin_user
    from app.config import APP_VERSION, settings
    from app.database import Base, get_db
    from app.routes import admin, search, source_concepts, source_assertions, media, system
    from app.models import Media
    from app.enums import FileTypeEnum
    from app.services.pixiv_metadata_vertical_slice_service import (
        repository_synthetic_pixiv_fixture, _queue_case, _apply_fixture_case,
    )
    from app.translations import language_registry, translation_helper

    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    # Materialize the same fictional PX1 inputs with actual local Media rows.
    # This server never imports normal main/lifespan or opens an existing DB.
    with Session() as db:
        cases = repository_synthetic_pixiv_fixture()['cases']
        import struct
        import zlib
        def chunk(kind, payload):
            return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload))
        png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 32, 32, 8, 2, 0, 0, 0))
               + chunk(b'IDAT', zlib.compress((b'\x00' + bytes([130, 80, 220]) * 32) * 32)) + chunk(b'IEND', b''))
        for case in cases:
            filename = f"synthetic-{case['media_id']}.png"
            path = runtime_storage / filename
            path.write_bytes(png)
            db.add(Media(id=case['media_id'], filename=filename, path=str(path),
                         thumbnail_path=str(path), hash=f"px3-browser-{case['media_id']}",
                         file_type=FileTypeEnum.image, mime_type='image/png', width=32, height=32, file_size=len(png)))
        db.flush()
        records = {case['case_id']: _queue_case(db, case) for case in cases}
        for case in cases:
            _apply_fixture_case(db, case, record=records[case['case_id']], rejected_cases=[])
        db.commit()
    app = FastAPI(title="VIOLET PX3 synthetic browser acceptance")
    app.mount(
        "/static",
        StaticFiles(directory=str(ROOT / "frontend" / "static")),
        name="static",
    )
    templates = Jinja2Templates(directory=str(ROOT / "frontend" / "templates"))
    templates.env.globals.update(
        {
            "app_version": APP_VERSION,
            "cache_buster": "px3-synthetic-e2e",
            "get_current_year": lambda: 2026,
            "t": lambda key, **kwargs: translation_helper.get(
                key, settings.CURRENT_LANGUAGE, **kwargs
            ),
            "get_translations_json": lambda: json.dumps(
                translation_helper.get_translations(settings.CURRENT_LANGUAGE)
            ),
            "current_language": lambda: settings.CURRENT_LANGUAGE,
            "available_languages": lambda: [
                language.to_dict() for language in language_registry.get_all_languages()
            ],
            "is_admin": lambda _request: True,
        }
    )

    def _session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _session
    app.dependency_overrides[require_admin_mode] = lambda: object()
    app.dependency_overrides[get_current_admin_user] = lambda: object()
    app.include_router(admin.router)
    app.include_router(search.router)
    app.include_router(source_concepts.router)
    app.include_router(source_assertions.router)
    app.include_router(media.router)
    app.include_router(system.router)

    @app.get('/', response_class=HTMLResponse)
    async def gallery_page(request: Request):
        return templates.TemplateResponse('index.html', {
            'request': request, 'app_name': 'VIOLET PX3 Synthetic',
            'default_sort': 'uploaded_at', 'default_order': 'desc',
            'sidebar_filter_mode': settings.SIDEBAR_FILTER_MODE,
            'sidebar_custom_buttons': settings.SIDEBAR_CUSTOM_BUTTONS,
        })

    @app.get('/media/{media_id}', response_class=HTMLResponse)
    async def media_page(request: Request, media_id: int):
        with Session() as db:
            return templates.TemplateResponse('media.html', {
                'request': request, 'app_name': 'VIOLET PX3 Synthetic',
                'media_id': media_id, 'media': db.get(Media, media_id), 'external_share_url': '',
            })

    @app.get('/api/albums')
    async def empty_albums():
        return {'items': [], 'total': 0, 'page': 1, 'pages': 0}

    @app.get('/sw.js')
    async def service_worker():
        return FileResponse(ROOT / 'frontend/static/sw.js', media_type='application/javascript')

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page(request: Request):
        return templates.TemplateResponse(
            "admin.html",
            {
                "request": request,
                "app_name": "V.I.O.L.E.T. PX3 Synthetic",
                "current_theme": None,
            },
        )

    # The real admin template initializes these unrelated read-only panels on
    # page load.  Keep the synthetic acceptance server quiet without mounting
    # credential-backed booru configuration or non-PX3 persistence routes.
    @app.get("/api/booru-config/")
    async def empty_booru_configs():
        return []

    @app.get("/api/tag-implications/")
    async def empty_tag_implications():
        return []

    @app.get("/__px3__/status")
    async def server_status():
        return {
            "ready": True,
            "database_scope": "task_owned_temporary_sqlite",
            "real_provider_network_activity": 0,
            "real_source_activity": 0,
            "existing_database_or_app_storage_activity": 0,
            "production_activity": 0,
        }

    app.state.px3_engine = engine
    app.state.px3_database_path = database_path
    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18183)
    args = parser.parse_args(argv)
    if not (1024 <= args.port <= 65535):
        raise Px3SyntheticServerError("px3_server_port_invalid")
    import uvicorn

    uvicorn.run(
        create_app(args.workspace),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
