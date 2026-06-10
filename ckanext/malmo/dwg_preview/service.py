from __future__ import annotations

import logging
import os
import shutil
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import requests

import ckan.lib.uploader as uploader
import ckan.logic as logic
from ckan.plugins import toolkit

from .cache import build_cache_path, file_sha256, is_cached_preview_valid, store_cached_preview
from .config import PreviewConfig
from .oda import convert_dwg_to_dxf
from .render import render_dxf_to_png

log = logging.getLogger(__name__)

ValidationError = logic.ValidationError
NotAuthorized = logic.NotAuthorized
NotFound = logic.NotFound

DWG_MIME_TYPES = {
    "application/acad",
    "application/autocad_dwg",
    "application/dwg",
    "application/x-acad",
    "application/x-autocad",
    "application/x-dwg",
    "image/vnd.dwg",
    "image/x-dwg",
}
DOWNLOAD_CHUNK_SIZE = 64 * 1024
PNG_MIMETYPE = "image/png"
PNG_EXTENSION = "png"


def build_preview_payload(context: dict[str, Any], data_dict: dict[str, Any]) -> dict[str, Any]:
    resource_id = (data_dict or {}).get("id")
    if not resource_id:
        raise ValidationError({"id": ["Missing value"]})

    config = PreviewConfig.from_ckan_config()
    resource = _get_resource_for_preview(context, resource_id)
    if not _is_dwg_resource(resource):
        raise ValidationError({"id": ["Resource must be a DWG file to generate a preview"]})

    log.info("DWG preview requested for resource=%s format=png", resource_id)

    with TemporaryDirectory(prefix="ckan-dwg-preview-") as tmp_dir:
        source_path = _stage_resource_dwg(resource, tmp_dir, config)
        source_hash = file_sha256(source_path)
        cache_path = build_cache_path(config.cache_dir, resource_id, source_hash)

        if is_cached_preview_valid(cache_path, config.min_preview_bytes):
            log.info("Serving cached DWG preview resource=%s cache=%s", resource_id, cache_path)
            content = _read_file(cache_path)
        else:
            content = _generate_preview(resource_id, source_path, tmp_dir, cache_path, config)

    return {
        "content": content,
        "filename": _build_output_filename(resource),
        "mimetype": PNG_MIMETYPE,
        "resource_id": resource_id,
    }


def _generate_preview(
    resource_id: str,
    source_path: str,
    tmp_dir: str,
    cache_path: str,
    config: PreviewConfig,
) -> bytes:
    dxf_path = convert_dwg_to_dxf(source_path, tmp_dir, config)
    preview_path = os.path.join(tmp_dir, "preview.png")
    render_dxf_to_png(dxf_path, preview_path, config)
    store_cached_preview(preview_path, cache_path)
    log.info("DWG preview generated resource=%s path=%s cache=%s", resource_id, preview_path, cache_path)
    return _read_file(preview_path)


def _read_file(path: str) -> bytes:
    with open(path, "rb") as output_file:
        return output_file.read()


def _get_resource_for_preview(context: dict[str, Any], resource_id: str) -> dict[str, Any]:
    try:
        return toolkit.get_action("resource_show")(context, {"id": resource_id})
    except NotFound:
        raise ValidationError({"id": ["Resource does not exist"]})
    except NotAuthorized:
        raise ValidationError({"id": ["User cannot view this resource"]})


def _is_dwg_resource(resource: dict[str, Any]) -> bool:
    resource_format = str(resource.get("format") or "").strip().lower()
    if resource_format:
        normalized_format = resource_format.lstrip(".")
        if normalized_format == "dwg" or "dwg" in normalized_format:
            return True

    for mime_field in ("mimetype", "mimetype_inner"):
        mimetype_value = str(resource.get(mime_field) or "").strip().lower()
        if mimetype_value in DWG_MIME_TYPES or mimetype_value.endswith("/dwg"):
            return True

    for path_field in ("url", "name"):
        raw_value = str(resource.get(path_field) or "")
        extension = os.path.splitext(urlparse(raw_value).path)[1].lower()
        if extension == ".dwg":
            return True

    return False


def _stage_resource_dwg(resource: dict[str, Any], tmp_dir: str, config: PreviewConfig) -> str:
    source_path = os.path.join(tmp_dir, "source.dwg")

    if resource.get("url_type") == "upload":
        log.info("Preparing uploaded DWG resource=%s", resource.get("id"))
        _copy_uploaded_resource(resource, source_path, config)
    else:
        resource_url = str(resource.get("url") or "").strip()
        if not resource_url:
            raise ValidationError({"id": ["Resource does not have a downloadable URL"]})
        log.info("Downloading external DWG resource=%s url=%s", resource.get("id"), resource_url)
        _download_to_path(
            resource_url,
            source_path,
            max_download_bytes=config.max_download_bytes,
            download_timeout=config.download_timeout,
            source_label="external DWG resource",
        )

    if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
        raise ValidationError({"id": ["DWG source file could not be prepared"]})

    log.info(
        "Prepared DWG source resource=%s path=%s bytes=%s",
        resource.get("id"),
        source_path,
        os.path.getsize(source_path),
    )
    return source_path


def _copy_uploaded_resource(resource: dict[str, Any], destination_path: str, config: PreviewConfig) -> None:
    resource_upload = uploader.get_resource_uploader(dict(resource))
    resource_id = resource["id"]
    resource_name = os.path.basename(str(resource.get("url") or "")) or f"{resource_id}.dwg"

    local_path = None
    try:
        local_path = resource_upload.get_path(resource_id)
    except TypeError:
        local_path = None
    except Exception as err:
        log.debug("Failed to resolve local upload path for %s: %s", resource_id, err)

    if local_path and os.path.exists(local_path):
        log.info("Copying uploaded DWG from local storage resource=%s path=%s", resource_id, local_path)
        _copy_local_file(local_path, destination_path, config.max_download_bytes)
        return

    if all(hasattr(resource_upload, attribute) for attribute in ("bucket_name", "get_path", "get_signed_url_to_key")):
        _download_uploaded_resource_from_storage(resource_upload, resource_id, resource_name, destination_path, config)
        return

    raise ValidationError({"id": ["Uploaded resource storage backend is not supported by convert_dwg"]})


def _download_uploaded_resource_from_storage(
    resource_upload: Any,
    resource_id: str,
    resource_name: str,
    destination_path: str,
    config: PreviewConfig,
) -> None:
    use_readonly_credentials = bool(
        getattr(resource_upload, "p_key_readonly", None) and getattr(resource_upload, "s_key_readonly", None)
    )
    remote_key = None
    try:
        remote_key = resource_upload.get_path(resource_id, resource_name)
        signed_url = resource_upload.get_signed_url_to_key(remote_key, read_only=use_readonly_credentials)
    except Exception as err:
        if use_readonly_credentials and remote_key:
            try:
                signed_url = resource_upload.get_signed_url_to_key(remote_key, read_only=False)
            except Exception:
                log.exception("Failed to resolve uploaded resource %s from remote storage", resource_id)
                raise ValidationError({"id": [f"Could not resolve uploaded resource: {err}"]})
        else:
            log.exception("Failed to resolve uploaded resource %s from remote storage", resource_id)
            raise ValidationError({"id": [f"Could not resolve uploaded resource: {err}"]})

    log.info("Downloading uploaded DWG from remote storage resource=%s key=%s", resource_id, remote_key)
    _download_to_path(
        signed_url,
        destination_path,
        max_download_bytes=config.max_download_bytes,
        download_timeout=config.download_timeout,
        source_label="uploaded DWG resource",
    )


def _copy_local_file(source_path: str, destination_path: str, max_download_bytes: int) -> None:
    file_size = os.path.getsize(source_path)
    if file_size > max_download_bytes:
        raise ValidationError(
            {"id": [f"DWG source file exceeds the maximum allowed size of {max_download_bytes} bytes"]}
        )
    shutil.copyfile(source_path, destination_path)


def _download_to_path(
    url: str,
    destination_path: str,
    max_download_bytes: int,
    download_timeout: int,
    source_label: str,
) -> None:
    parsed_url = urlparse(url)
    if parsed_url.scheme.lower() not in {"http", "https"}:
        raise ValidationError({"id": [f"Unsupported URL scheme for {source_label}"]})

    bytes_downloaded = 0
    try:
        with requests.get(url, stream=True, timeout=(10, download_timeout), allow_redirects=True) as response:
            response.raise_for_status()
            with open(destination_path, "wb") as destination_file:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded > max_download_bytes:
                        raise ValidationError(
                            {"id": [f"{source_label.capitalize()} exceeds the maximum allowed size of {max_download_bytes} bytes"]}
                        )
                    destination_file.write(chunk)
    except ValidationError:
        if os.path.exists(destination_path):
            os.remove(destination_path)
        raise
    except requests.RequestException as err:
        if os.path.exists(destination_path):
            os.remove(destination_path)
        raise ValidationError({"id": [f"Could not download {source_label}: {err}"]})

    log.info("Downloaded %s path=%s bytes=%s", source_label, destination_path, bytes_downloaded)


def _build_output_filename(resource: dict[str, Any]) -> str:
    raw_name = (
        str(resource.get("name") or "").strip()
        or os.path.basename(str(resource.get("url") or "").strip())
        or resource["id"]
    )
    base_name = os.path.splitext(raw_name)[0] or resource["id"]
    return f"{base_name}.{PNG_EXTENSION}"
