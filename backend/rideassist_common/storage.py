"""Encrypted document storage. Files are Fernet-encrypted *before* they leave the service, so the
storage backend (local volume, MinIO, S3) only ever holds ciphertext."""

import asyncio
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet

from rideassist_common.config import get_settings


class BlobStore(Protocol):
    async def put(self, key: str, data: bytes) -> None: ...
    async def get(self, key: str) -> bytes: ...


class LocalBlobStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root.resolve()):
            raise ValueError("invalid object key")
        return path

    async def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._path(key).read_bytes)


class S3BlobStore:
    def __init__(self) -> None:
        import boto3

        s = get_settings()
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint_url,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            region_name=s.s3_region,
        )

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data)

    async def get(self, key: str) -> bytes:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read)


class DocumentVault:
    def __init__(self, store: BlobStore, key: str) -> None:
        self.store = store
        self.fernet = Fernet(key.encode())

    async def save(self, key: str, plaintext: bytes) -> str:
        await self.store.put(key, self.fernet.encrypt(plaintext))
        return hashlib.sha256(plaintext).hexdigest()

    async def load(self, key: str) -> bytes:
        return self.fernet.decrypt(await self.store.get(key))


@lru_cache
def get_vault() -> DocumentVault:
    s = get_settings()
    store: BlobStore = (
        S3BlobStore() if s.storage_backend == "s3" else LocalBlobStore(s.local_storage_dir)
    )
    return DocumentVault(store, s.document_encryption_key)
