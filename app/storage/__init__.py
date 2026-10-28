"""File storage for exported reports.

The rest of the application only calls ``save`` and ``response``; switching from the local
``exports/`` folder to Amazon S3 is a configuration change (STORAGE_BACKEND=s3).
"""
import re
from pathlib import Path
from typing import Protocol

from fastapi.responses import FileResponse, RedirectResponse, Response

from app.config import Settings

KEY_PATTERN = re.compile(r"^exports/[A-Za-z0-9._-]+$")
# Explicit, because the OS-level MIME registry differs (Windows maps .csv to ms-excel).
CONTENT_TYPES = {"csv": "text/csv", "json": "application/json"}


def content_type_for(key: str) -> str:
    return CONTENT_TYPES.get(key.rsplit(".", 1)[-1].lower(), "application/octet-stream")


class Storage(Protocol):
    def save(self, key: str, data: bytes, content_type: str) -> None: ...
    def response(self, key: str, download_name: str | None = None) -> Response: ...


class LocalStorage:
    def __init__(self, folder: str):
        self.root = Path(folder).resolve()

    def _path(self, key: str) -> Path:
        if not KEY_PATTERN.match(key):
            raise ValueError("Invalid storage key")
        path = (self.root / key).resolve()
        if self.root not in path.parents:
            raise ValueError("Invalid storage key")
        return path

    def save(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def response(self, key: str, download_name: str | None = None) -> Response:
        path = self._path(key)
        if not path.is_file():
            return Response("Not found", status_code=404)
        return FileResponse(path, media_type=content_type_for(key), filename=download_name or path.name)


class S3Storage:
    """Reports in a private, encrypted S3 bucket; downloads use short-lived signed URLs.

    On EC2 the IAM role needs s3:PutObject and s3:GetObject on the bucket prefix only.
    """

    def __init__(self, bucket: str, prefix: str = "", region: str | None = None,
                 expiry_seconds: int = 300, client=None):
        if client is None:
            import boto3  # lazy: local development does not need AWS

            client = boto3.client("s3", region_name=region)
        self.client, self.bucket = client, bucket
        self.prefix = prefix.strip("/")
        self.expiry = expiry_seconds

    def _object_key(self, key: str) -> str:
        if not KEY_PATTERN.match(key):
            raise ValueError("Invalid storage key")
        return f"{self.prefix}/{key}" if self.prefix else key

    def save(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(Bucket=self.bucket, Key=self._object_key(key), Body=data,
                               ContentType=content_type, ServerSideEncryption="AES256")

    def response(self, key: str, download_name: str | None = None) -> Response:
        params = {"Bucket": self.bucket, "Key": self._object_key(key)}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        url = self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=self.expiry)
        return RedirectResponse(url, status_code=302)


def build_storage(settings: Settings) -> Storage:
    if settings.storage_backend == "s3":
        return S3Storage(settings.s3_bucket, settings.s3_prefix, settings.aws_region,
                         settings.s3_url_expiry_seconds)
    return LocalStorage(settings.exports_dir)
