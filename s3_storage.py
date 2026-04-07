"""
s3_storage.py – S3-backed result storage with local filesystem fallback.

Set S3_BUCKET_NAME to enable S3 storage.  When unset, files are stored
locally under RESULT_STORAGE_DIR (default: /tmp).

Environment variables:
    S3_BUCKET_NAME       – S3 bucket name (omit for local-only)
    S3_PREFIX            – Key prefix inside the bucket (default: "batch-results/")
    S3_REGION            – AWS region (default: us-east-1)
    S3_ENDPOINT_URL      – Custom endpoint for S3-compatible stores (MinIO, R2, etc.)
    AWS_ACCESS_KEY_ID    – (standard AWS SDK env var)
    AWS_SECRET_ACCESS_KEY – (standard AWS SDK env var)
    RESULT_STORAGE_DIR   – Local directory for fallback (default: /tmp)
    S3_PRESIGNED_EXPIRY  – Presigned URL expiry in seconds (default: 3600)
"""

import io
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

S3_BUCKET = os.getenv("S3_BUCKET_NAME", "")
S3_PREFIX = os.getenv("S3_PREFIX", "batch-results/")
S3_REGION = os.getenv("S3_REGION", "us-east-1")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
RESULT_DIR = os.getenv("RESULT_STORAGE_DIR", "/tmp")
PRESIGNED_EXPIRY = int(os.getenv("S3_PRESIGNED_EXPIRY", "3600"))

_s3_client = None


def _get_s3_client():
    """Lazy-initialise the boto3 S3 client."""
    global _s3_client
    if _s3_client is None:
        import boto3
        kwargs = {"region_name": S3_REGION}
        if S3_ENDPOINT_URL:
            kwargs["endpoint_url"] = S3_ENDPOINT_URL
        _s3_client = boto3.client("s3", **kwargs)
    return _s3_client


def _s3_key(job_id: str) -> str:
    return f"{S3_PREFIX}{job_id}.zip"


def _local_path(job_id: str) -> str:
    path = os.path.join(RESULT_DIR, f"{job_id}.zip")
    real = os.path.realpath(path)
    if not real.startswith(os.path.realpath(RESULT_DIR)):
        raise ValueError("Invalid job_id: path traversal detected")
    return real


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def upload_result(job_id: str, zip_data: bytes) -> str:
    """Store a ZIP result and return its storage location.

    Returns an S3 key (``s3://bucket/key``) when S3 is configured,
    otherwise a local file path.
    """
    if S3_BUCKET:
        key = _s3_key(job_id)
        _get_s3_client().put_object(
            Bucket=S3_BUCKET,
            Key=key,
            Body=zip_data,
            ContentType="application/zip",
        )
        location = f"s3://{S3_BUCKET}/{key}"
        logger.info("Uploaded %s (%d bytes)", location, len(zip_data))
        return location

    # Local fallback
    path = _local_path(job_id)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(zip_data)
    logger.info("Saved locally %s (%d bytes)", path, len(zip_data))
    return path


def download_result(job_id: str) -> Optional[bytes]:
    """Retrieve a ZIP result as bytes, or None if not found."""
    if S3_BUCKET:
        try:
            resp = _get_s3_client().get_object(
                Bucket=S3_BUCKET, Key=_s3_key(job_id)
            )
            return resp["Body"].read()
        except _get_s3_client().exceptions.NoSuchKey:
            return None
        except Exception:
            logger.exception("S3 download failed for job %s", job_id)
            return None

    path = _local_path(job_id)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def get_presigned_url(job_id: str) -> Optional[str]:
    """Return a presigned download URL (S3 only).  Returns None for local."""
    if not S3_BUCKET:
        return None
    try:
        return _get_s3_client().generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": _s3_key(job_id)},
            ExpiresIn=PRESIGNED_EXPIRY,
        )
    except Exception:
        logger.exception("Failed to generate presigned URL for job %s", job_id)
        return None


def result_exists(job_id: str) -> bool:
    """Check whether a result ZIP exists."""
    if S3_BUCKET:
        try:
            _get_s3_client().head_object(
                Bucket=S3_BUCKET, Key=_s3_key(job_id)
            )
            return True
        except Exception:
            return False

    return os.path.exists(_local_path(job_id))


def delete_result(job_id: str) -> bool:
    """Delete a result ZIP.  Returns True if it existed."""
    if S3_BUCKET:
        try:
            _get_s3_client().delete_object(
                Bucket=S3_BUCKET, Key=_s3_key(job_id)
            )
            return True
        except Exception:
            return False

    path = _local_path(job_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False
