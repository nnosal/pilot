"""Tests for the admin S3 routes: bucket listing and Minio region detection."""

from __future__ import annotations

import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask

from admin.backend.api.v1.s3 import s3_bp


def client(bench_root: Path):
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(s3_bp, url_prefix="/api/v1/s3")
    return app.test_client()


def fake_s3(bucket_names: list[str]) -> MagicMock:
    s3 = MagicMock()
    s3.return_value.client.list_buckets.return_value = {"Buckets": [{"Name": name} for name in bucket_names]}
    return s3


def test_list_buckets_get_requires_saved_config(tmp_path: Path) -> None:
    response = client(tmp_path).get("/api/v1/s3/buckets")

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "s3_not_configured"


def test_list_buckets_with_posted_credentials_skips_saved_config(tmp_path: Path) -> None:
    with patch("pilot.integrations.s3.base.S3", fake_s3(["backups", "media"])):
        response = client(tmp_path).post(
            "/api/v1/s3/buckets",
            json={
                "access_key": "AKIA123",
                "secret_key": "secret",
                "provider": "minio",
                "endpoint": "http://minio.local:9000",
                "is_minio": True,
            },
        )

    assert response.status_code == 200
    assert [bucket["name"] for bucket in response.get_json()["buckets"]] == ["backups", "media"]


def test_list_buckets_post_without_credentials_requires_saved_config(tmp_path: Path) -> None:
    response = client(tmp_path).post("/api/v1/s3/buckets", json={"access_key": "AKIA123"})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "s3_not_configured"


def test_detect_region_treats_http_error_as_reachable(tmp_path: Path) -> None:
    headers = Message()
    headers["x-amz-bucket-region"] = "eu-west-3"
    http_error = urllib.error.HTTPError("http://minio.local", 401, "Unauthorized", headers, None)

    with patch("urllib.request.urlopen", side_effect=http_error):
        response = client(tmp_path).post("/api/v1/s3/detect-region", json={"endpoint": "http://minio.local"})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["success"] is True
    assert payload["region"] == "eu-west-3"


def test_detect_region_unreachable_endpoint_fails(tmp_path: Path) -> None:
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        response = client(tmp_path).post("/api/v1/s3/detect-region", json={"endpoint": "http://minio.local"})

    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "detection_failed"
