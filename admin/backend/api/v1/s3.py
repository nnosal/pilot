from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response
from pilot.config import BenchConfig, S3Config

s3_bp = Blueprint("s3", __name__)


def _get_s3_config() -> S3Config | None:
    """Get current S3 configuration from bench."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    try:
        config = BenchConfig.read(bench_root, validate=False)
        if not config.s3.is_configured:
            return None
        return config.s3
    except Exception:
        return None


def _create_s3_client(config: S3Config):
    """Create S3 client from config for testing."""
    try:
        from pilot.integrations.s3.base import S3

        return S3(
            config.access_key,
            config.secret_key,
            region_name=config.region or "us-east-1",
            provider=config.provider,
            bucket_name=config.bucket,
            custom_endpoint=config.endpoint,
            is_minio=config.is_minio,
        )
    except Exception as e:
        raise ValueError(f"Failed to create S3 client: {e!s}") from e


def _client_from_credentials(data: dict):
    from pilot.integrations.s3.base import S3

    return S3(
        data["access_key"],
        data["secret_key"],
        region_name=data.get("region") or "us-east-1",
        provider=data.get("provider", ""),
        bucket_name=data.get("bucket", "test"),
        custom_endpoint=data.get("endpoint", ""),
        is_minio=data.get("is_minio", False),
    )


@s3_bp.get("/buckets")
@s3_bp.post("/buckets")
def list_buckets():
    """List buckets using POSTed credentials, or the saved config on GET."""
    data = request.get_json(silent=True) if request.method == "POST" else None
    has_credentials = isinstance(data, dict) and data.get("access_key") and data.get("secret_key")
    config = None if has_credentials else _get_s3_config()
    if not has_credentials and not config:
        return error_response("s3_not_configured", "S3 is not configured. Please configure S3 settings first.", 400)

    try:
        client = _client_from_credentials(data) if has_credentials else _create_s3_client(config)
        buckets = client.client.list_buckets()

        bucket_list = [
            {
                "name": bucket["Name"],
                "creation_date": bucket.get("CreationDate", ""),
            }
            for bucket in buckets.get("Buckets", [])
        ]

        return jsonify({"buckets": bucket_list})
    except Exception as e:
        return error_response("s3_list_failed", f"Failed to list buckets: {e!s}", 500)


@s3_bp.post("/test-connection")
def test_connection():
    """Test S3/Minio connection with provided credentials."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    required_fields = ["access_key", "secret_key"]
    if not data.get("is_minio"):
        required_fields.extend(["provider", "region"])
    else:
        required_fields.append("endpoint")

    missing = [field for field in required_fields if not data.get(field)]
    if missing:
        return error_response(
            "missing_fields",
            f"Missing required fields: {', '.join(missing)}",
            400,
        )

    try:
        from pilot.integrations.s3.base import S3

        client = S3(
            data["access_key"],
            data["secret_key"],
            region_name=data.get("region", "us-east-1"),
            provider=data.get("provider", ""),
            bucket_name=data.get("bucket", "test"),
            custom_endpoint=data.get("endpoint", ""),
            is_minio=data.get("is_minio", False),
        )

        # Test connection by listing buckets
        buckets = client.client.list_buckets()

        return jsonify({
            "success": True,
            "message": "Connection successful",
            "bucket_count": len(buckets.get("Buckets", [])),
        })

    except Exception as e:
        return error_response(
            "connection_failed",
            f"Connection failed: {e!s}",
            400,
        )


@s3_bp.post("/detect-region")
def detect_region():
    """Detect region from Minio endpoint."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)

    endpoint = data.get("endpoint", "")
    if not endpoint:
        return error_response("missing_endpoint", "Endpoint URL is required.", 400)

    try:
        import urllib.error
        import urllib.request

        try:
            response = urllib.request.urlopen(f"{endpoint}/minio/health/live", timeout=5)
            region = response.headers.get("x-amz-bucket-region", "us-east-1")
        except urllib.error.HTTPError as http_error:
            # Any HTTP response (401/403 included) proves the endpoint answers
            region = http_error.headers.get("x-amz-bucket-region", "us-east-1")

        return jsonify({
            "success": True,
            "region": region,
            "message": "Minio endpoint is reachable.",
        })

    except urllib.error.URLError as e:
        return error_response(
            "detection_failed",
            f"Failed to detect region: {e.reason!s}",
            400,
        )
    except Exception as e:
        return error_response(
            "detection_failed",
            f"Failed to detect region: {e!s}",
            400,
        )
