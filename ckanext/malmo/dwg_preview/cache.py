from __future__ import annotations

import hashlib
import os
import shutil


def file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source_file:
        for chunk in iter(lambda: source_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_cache_path(cache_dir: str, resource_id: str, source_hash: str) -> str:
    cache_key = hashlib.sha256(f"{resource_id}:{source_hash}".encode("utf-8")).hexdigest()
    return os.path.join(cache_dir, f"{cache_key}.png")


def is_cached_preview_valid(path: str, min_preview_bytes: int) -> bool:
    return os.path.exists(path) and os.path.getsize(path) >= min_preview_bytes


def store_cached_preview(source_path: str, cache_path: str) -> None:
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    temp_path = f"{cache_path}.tmp"
    shutil.copyfile(source_path, temp_path)
    os.replace(temp_path, cache_path)
