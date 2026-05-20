"""Regression tests for media metadata JSON serialization."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
import importlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.utils import media_helpers
from app.utils.media_helpers import extract_image_metadata, sanitize_metadata_for_json


class FakeRational:
    def __init__(self, numerator=1, denominator=2):
        self.numerator = numerator
        self.denominator = denominator

    def __float__(self):
        return self.numerator / self.denominator

    def __str__(self):
        return f"{self.numerator}/{self.denominator}"


class BadRational:
    numerator = 1
    denominator = 0

    def __float__(self):
        raise ZeroDivisionError("bad rational")

    def __str__(self):
        return "bad-rational"


class UnknownMetadataObject:
    def __str__(self):
        return "unknown-object"


class FakeImage:
    format = "JPEG"

    def __init__(self):
        self.info = {
            "ratio": FakeRational(3, 4),
            "raw": b"hello\xff",
            "nested": {
                "tuple": (FakeRational(1, 3), b"bytes"),
                "set": {FakeRational(2, 5)},
            },
        }

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def getexif(self):
        return {
            0x9286: b'{"prompt": "safe"}',
            0x010E: "plain description",
        }

    def _getexif(self):
        return {}


def test_sanitize_metadata_for_json_handles_nested_non_json_values():
    payload = {
        "primitive": "ok",
        b"bytes-key": b"byte-value",
        "rational": FakeRational(1, 2),
        "bad_rational": BadRational(),
        "nested": [
            {"tuple": (FakeRational(5, 2), UnknownMetadataObject())},
            {FakeRational(7, 4)},
        ],
        "unknown": UnknownMetadataObject(),
    }

    result = sanitize_metadata_for_json(payload)

    assert result["primitive"] == "ok"
    assert result["bytes-key"] == "byte-value"
    assert result["rational"] == 0.5
    assert result["bad_rational"] == "bad-rational"
    assert result["nested"][0]["tuple"] == [2.5, "unknown-object"]
    assert result["unknown"] == "unknown-object"
    json.dumps(result)


def test_sanitize_metadata_for_json_never_raises_on_recursive_containers():
    payload = {}
    payload["self"] = payload

    result = sanitize_metadata_for_json(payload)

    assert result == {"self": "[recursive]"}
    json.dumps(result)


def test_extract_image_metadata_returns_json_safe_values(monkeypatch, tmp_path):
    image_path = tmp_path / "metadata.jpg"
    image_path.write_bytes(b"fake image")
    monkeypatch.setattr(media_helpers.Image, "open", lambda _path: FakeImage())

    result = extract_image_metadata(image_path)

    assert result["ratio"] == 0.75
    assert result["nested"]["tuple"][0] == pytest.approx(1 / 3)
    assert result["parameters"] == {"prompt": "safe"}
    assert result["description"] == "plain description"
    json.dumps(result)


def test_extract_image_metadata_failure_returns_empty_safe_payload(monkeypatch, tmp_path):
    image_path = tmp_path / "broken.jpg"
    image_path.write_bytes(b"fake image")

    def raise_open(_path):
        raise RuntimeError("cannot read image")

    monkeypatch.setattr(media_helpers.Image, "open", raise_open)

    assert extract_image_metadata(image_path) == {}


def test_media_metadata_endpoint_returns_200_for_rational_metadata(monkeypatch, tmp_path):
    from app.routes import media as media_route

    image_path = tmp_path / "metadata.jpg"
    image_path.write_bytes(b"fake image")
    media = SimpleNamespace(id=123, path="media/original/metadata.jpg")

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = media
    mock_db = MagicMock()
    mock_db.query.return_value = mock_query

    test_app = FastAPI()
    test_app.include_router(media_route.router)
    test_app.dependency_overrides[media_route.get_db] = lambda: mock_db
    monkeypatch.setattr(media_route.settings, "resolve_storage_path", lambda _path: image_path)

    for helper_module_name in ("app.utils.media_helpers", "backend.app.utils.media_helpers"):
        try:
            helper_module = importlib.import_module(helper_module_name)
            monkeypatch.setattr(helper_module.Image, "open", lambda _path: FakeImage())
        except Exception:
            pass

    client = TestClient(test_app, raise_server_exceptions=False)
    response = client.get("/api/media/123/metadata")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ratio"] == 0.75
    assert payload["parameters"] == {"prompt": "safe"}
    json.dumps(payload)
