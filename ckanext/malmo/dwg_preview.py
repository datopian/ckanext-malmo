from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import urlparse

import requests

import ckan.lib.uploader as uploader
import ckan.logic as logic
from ckan.plugins import toolkit

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
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30
DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DOWNLOAD_CHUNK_SIZE = 64 * 1024
SVG_VIEWBOX_RE = re.compile(
    r'viewBox="(?P<min_x>-?\d+(?:\.\d+)?)\s+'
    r'(?P<min_y>-?\d+(?:\.\d+)?)\s+'
    r'(?P<width>\d+(?:\.\d+)?)\s+'
    r'(?P<height>\d+(?:\.\d+)?)"'
)
SVG_ROOT_TAG_RE = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
SVG_WIDTH_ATTR_RE = re.compile(r'width="[^"]*"', re.IGNORECASE)
SVG_HEIGHT_ATTR_RE = re.compile(r'height="[^"]*"', re.IGNORECASE)
SVG_DRAWABLE_TAG_RE = re.compile(
    r"<(?:use|path|line|polyline|polygon|circle|ellipse|text)\b",
    re.IGNORECASE,
)


def build_preview_payload(context: dict[str, Any], data_dict: dict[str, Any]) -> dict[str, Any]:
    """
    Build a binary preview payload from a DWG resource.

    The returned dictionary is meant for internal Python callers. The Flask
    route turns this payload into an HTTP response with the correct mimetype.
    """
    resource_id = (data_dict or {}).get("resource_id")
    if not resource_id:
        raise ValidationError({"resource_id": ["Missing value"]})

    resource = _get_resource_for_preview(context, resource_id)
    if not _is_dwg_resource(resource):
        raise ValidationError(
            {"resource_id": ["Resource must be a DWG file to generate a preview"]}
        )

    conversion_timeout = _get_int_config(
        "ckanext.malmo.dwg_preview_timeout",
        DEFAULT_TIMEOUT_SECONDS,
    )
    download_timeout = _get_int_config(
        "ckanext.malmo.dwg_preview_download_timeout",
        DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    )
    max_download_bytes = _get_int_config(
        "ckanext.malmo.dwg_preview_max_download_bytes",
        DEFAULT_MAX_DOWNLOAD_BYTES,
    )

    with TemporaryDirectory(prefix="ckan-dwg-preview-") as tmp_dir:
        source_path = _stage_resource_dwg(
            resource,
            tmp_dir,
            max_download_bytes=max_download_bytes,
            download_timeout=download_timeout,
        )
        output_path = _convert_dwg_to_best_svg(
            source_path,
            tmp_dir,
            timeout=conversion_timeout,
        )
        with open(output_path, "rb") as output_file:
            content = output_file.read()

    return {
        "content": content,
        "filename": _build_output_filename(resource),
        "mimetype": "image/svg+xml",
        "resource_id": resource_id,
    }


def _get_resource_for_preview(context: dict[str, Any], resource_id: str) -> dict[str, Any]:
    try:
        return toolkit.get_action("resource_show")(context, {"id": resource_id})
    except NotFound:
        raise ValidationError({"resource_id": ["Resource does not exist"]})
    except NotAuthorized:
        raise ValidationError({"resource_id": ["User cannot view this resource"]})


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


def _stage_resource_dwg(
    resource: dict[str, Any],
    tmp_dir: str,
    max_download_bytes: int,
    download_timeout: int,
) -> str:
    source_path = os.path.join(tmp_dir, "source.dwg")

    if resource.get("url_type") == "upload":
        _copy_uploaded_resource(
            resource,
            source_path,
            max_download_bytes=max_download_bytes,
            download_timeout=download_timeout,
        )
    else:
        resource_url = str(resource.get("url") or "").strip()
        if not resource_url:
            raise ValidationError({"resource_id": ["Resource does not have a downloadable URL"]})
        _download_to_path(
            resource_url,
            source_path,
            max_download_bytes=max_download_bytes,
            download_timeout=download_timeout,
            source_label="external DWG resource",
        )

    if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
        raise ValidationError({"resource_id": ["DWG source file could not be prepared"]})

    return source_path


def _copy_uploaded_resource(
    resource: dict[str, Any],
    destination_path: str,
    max_download_bytes: int,
    download_timeout: int,
) -> None:
    """
    Resolve a CKAN-uploaded file into a temp file.

    The filesystem branch matches the default CKAN storage backend. The signed
    URL branch is an adaptation point for storage backends such as
    ckanext-s3filestore, which this repository currently enables.
    """
    resource_upload = uploader.get_resource_uploader(dict(resource))
    resource_id = resource["id"]
    resource_name = os.path.basename(str(resource.get("url") or "")) or f"{resource_id}.dwg"

    local_path = None
    try:
        local_path = resource_upload.get_path(resource_id)
    except TypeError:
        # Some backends, such as s3filestore, require the stored filename.
        local_path = None
    except Exception as err:
        log.debug("Failed to resolve local upload path for %s: %s", resource_id, err)

    if local_path and os.path.exists(local_path):
        _copy_local_file(
            local_path,
            destination_path,
            max_download_bytes=max_download_bytes,
        )
        return

    if all(
        hasattr(resource_upload, attribute)
        for attribute in ("bucket_name", "get_path", "get_signed_url_to_key")
    ):
        use_readonly_credentials = bool(
            getattr(resource_upload, "p_key_readonly", None)
            and getattr(resource_upload, "s_key_readonly", None)
        )
        try:
            remote_key = resource_upload.get_path(resource_id, resource_name)
            signed_url = resource_upload.get_signed_url_to_key(
                remote_key,
                read_only=use_readonly_credentials,
            )
        except Exception as err:
            if use_readonly_credentials:
                try:
                    signed_url = resource_upload.get_signed_url_to_key(
                        remote_key,
                        read_only=False,
                    )
                except Exception:
                    log.exception(
                        "Failed to resolve uploaded resource %s from remote storage",
                        resource_id,
                    )
                    raise ValidationError(
                        {"resource_id": [f"Could not resolve uploaded resource: {err}"]}
                    )
            else:
                log.exception(
                    "Failed to resolve uploaded resource %s from remote storage",
                    resource_id,
                )
                raise ValidationError(
                    {"resource_id": [f"Could not resolve uploaded resource: {err}"]}
                )

        _download_to_path(
            signed_url,
            destination_path,
            max_download_bytes=max_download_bytes,
            download_timeout=download_timeout,
            source_label="uploaded DWG resource",
        )
        return

    raise ValidationError(
        {
            "resource_id": [
                "Uploaded resource storage backend is not supported by dwg_preview_convert"
            ]
        }
    )


def _copy_local_file(source_path: str, destination_path: str, max_download_bytes: int) -> None:
    file_size = os.path.getsize(source_path)
    if file_size > max_download_bytes:
        raise ValidationError(
            {
                "resource_id": [
                    f"DWG source file exceeds the maximum allowed size of {max_download_bytes} bytes"
                ]
            }
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
        raise ValidationError({"resource_id": [f"Unsupported URL scheme for {source_label}"]})

    bytes_downloaded = 0
    try:
        with requests.get(
            url,
            stream=True,
            timeout=(10, download_timeout),
            allow_redirects=True,
        ) as response:
            response.raise_for_status()
            with open(destination_path, "wb") as destination_file:
                for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if not chunk:
                        continue
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded > max_download_bytes:
                        raise ValidationError(
                            {
                                "resource_id": [
                                    f"{source_label.capitalize()} exceeds the maximum allowed size of {max_download_bytes} bytes"
                                ]
                            }
                        )
                    destination_file.write(chunk)
    except ValidationError:
        if os.path.exists(destination_path):
            os.remove(destination_path)
        raise
    except requests.RequestException as err:
        if os.path.exists(destination_path):
            os.remove(destination_path)
        raise ValidationError({"resource_id": [f"Could not download {source_label}: {err}"]})


def _convert_dwg_to_best_svg(
    source_path: str,
    tmp_dir: str,
    timeout: int,
) -> str:
    default_variant = _attempt_svg_conversion(
        source_path,
        os.path.join(tmp_dir, "preview.default.svg"),
        timeout=timeout,
        mspace_only=False,
        mode_label="default",
    )
    mspace_variant = _attempt_svg_conversion(
        source_path,
        os.path.join(tmp_dir, "preview.mspace.svg"),
        timeout=timeout,
        mspace_only=True,
        mode_label="mspace",
    )
    selected_variant = _select_best_svg_variant(default_variant, mspace_variant)
    log.debug(
        "DWG preview chose %s conversion (score=%s, bytes=%s, drawables=%s)",
        selected_variant["mode"],
        selected_variant["score"],
        selected_variant["size_bytes"],
        selected_variant["drawable_count"],
    )
    return selected_variant["path"]


def _attempt_svg_conversion(
    source_path: str,
    svg_path: str,
    timeout: int,
    mspace_only: bool,
    mode_label: str,
) -> dict[str, Any]:
    try:
        _run_dwg_to_svg(source_path, svg_path, timeout, mspace_only=mspace_only)
        _normalize_svg_viewbox(svg_path)
        drawable_count, size_bytes = _measure_svg_preview(svg_path)
        return {
            "mode": mode_label,
            "path": svg_path,
            "score": drawable_count * 1000 + size_bytes,
            "drawable_count": drawable_count,
            "size_bytes": size_bytes,
        }
    except ValidationError as err:
        log.warning("DWG preview %s conversion failed: %s", mode_label, err.error_dict)
        return {"mode": mode_label, "error": err}


def _select_best_svg_variant(*variants: dict[str, Any]) -> dict[str, Any]:
    successful_variants = [variant for variant in variants if "error" not in variant]
    if successful_variants:
        return max(
            successful_variants,
            key=lambda variant: (
                variant["score"],
                variant["drawable_count"],
                variant["size_bytes"],
            ),
        )

    error_messages = []
    for variant in variants:
        error = variant.get("error")
        if not error:
            continue
        error_messages.append(f'{variant["mode"]}: {error.error_dict}')

    raise ValidationError(
        {
            "conversion": [
                "DWG conversion failed for all modes"
                + (f" ({'; '.join(error_messages)})" if error_messages else "")
            ]
        }
    )


def _measure_svg_preview(svg_path: str) -> tuple[int, int]:
    try:
        with open(svg_path, "r", encoding="utf-8") as svg_file:
            svg_text = svg_file.read()
    except OSError as err:
        raise ValidationError({"conversion": [f"Could not read generated SVG: {err}"]})

    drawable_count = len(SVG_DRAWABLE_TAG_RE.findall(svg_text))
    size_bytes = len(svg_text.encode("utf-8"))
    return drawable_count, size_bytes


def _run_dwg_to_svg(
    source_path: str,
    svg_path: str,
    timeout: int,
    mspace_only: bool = False,
) -> None:
    _require_command("dwg2SVG", "libredwg-tools")
    command = ["dwg2SVG"]
    if mspace_only:
        command.append("--mspace")
    command.append(source_path)
    with open(svg_path, "wb") as svg_file:
        result = _run_subprocess(
            command,
            stdout=svg_file,
            timeout=timeout,
        )

    if result.returncode != 0 or not os.path.exists(svg_path) or os.path.getsize(svg_path) == 0:
        stderr = _decode_subprocess_output(result.stderr)
        raise ValidationError(
            {
                "conversion": [
                    "DWG to SVG conversion failed"
                    + (f": {stderr}" if stderr else "")
                ]
            }
        )

def _normalize_svg_viewbox(svg_path: str) -> None:
    """
    Rebase libredwg SVG output when the viewBox origin is left in world coords.

    Some DWG files are emitted with a large absolute viewBox origin while the
    visible geometry is already shifted near 0,0. That mismatch causes
    rasterization to render an empty transparent image.
    """
    try:
        with open(svg_path, "r", encoding="utf-8") as svg_file:
            svg_text = svg_file.read()
    except OSError as err:
        raise ValidationError({"conversion": [f"Could not read generated SVG: {err}"]})

    match = SVG_VIEWBOX_RE.search(svg_text)
    if not match:
        return

    min_x = float(match.group("min_x"))
    min_y = float(match.group("min_y"))
    width = match.group("width")
    height = match.group("height")
    normalized_svg = svg_text

    if min_x != 0 or min_y != 0:
        normalized_viewbox = f'viewBox="0 0 {width} {height}"'
        normalized_svg = SVG_VIEWBOX_RE.sub(normalized_viewbox, normalized_svg, count=1)

    normalized_svg = _normalize_svg_root_size(normalized_svg, width=width, height=height)

    if normalized_svg == svg_text:
        return

    try:
        with open(svg_path, "w", encoding="utf-8") as svg_file:
            svg_file.write(normalized_svg)
    except OSError as err:
        raise ValidationError({"conversion": [f"Could not normalize generated SVG: {err}"]})


def _normalize_svg_root_size(svg_text: str, width: str, height: str) -> str:
    """
    Ensure generated SVGs have intrinsic dimensions when embedded as images.

    libredwg emits root SVG tags with width/height set to 100%, which renders
    fine in a browser tab but can collapse or scale unpredictably when the SVG
    is used as an <img> source. Replacing those root dimensions with concrete
    values derived from the viewBox gives the browser a stable intrinsic size.
    """
    root_tag_match = SVG_ROOT_TAG_RE.search(svg_text)
    if not root_tag_match:
        return svg_text

    root_tag = root_tag_match.group(0)
    normalized_root_tag = SVG_WIDTH_ATTR_RE.sub(f'width="{width}"', root_tag, count=1)
    normalized_root_tag = SVG_HEIGHT_ATTR_RE.sub(
        f'height="{height}"',
        normalized_root_tag,
        count=1,
    )

    if normalized_root_tag == root_tag:
        return svg_text

    start, end = root_tag_match.span()
    return svg_text[:start] + normalized_root_tag + svg_text[end:]


def _run_subprocess(
    command: list[str],
    timeout: int,
    stdout: Any | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=stdout if stdout is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ValidationError(
            {"conversion": [f"Conversion exceeded the timeout of {timeout} seconds"]}
        )
    except OSError as err:
        raise ValidationError({"conversion": [f"Conversion process failed to start: {err}"]})


def _require_command(command_name: str, package_name: str) -> None:
    if shutil.which(command_name):
        return
    raise ValidationError(
        {
            "converter": [
                f'{command_name} is not installed. Install the "{package_name}" package.'
            ]
        }
    )


def _decode_subprocess_output(output: bytes | None) -> str:
    if not output:
        return ""
    return output.decode("utf-8", errors="replace").strip().splitlines()[0][:400]


def _build_output_filename(resource: dict[str, Any]) -> str:
    raw_name = (
        str(resource.get("name") or "").strip()
        or os.path.basename(str(resource.get("url") or "").strip())
        or resource["id"]
    )
    base_name = os.path.splitext(raw_name)[0] or resource["id"]
    return f"{base_name}.svg"


def _get_int_config(config_key: str, default_value: int) -> int:
    raw_value = toolkit.config.get(config_key)
    if raw_value in (None, ""):
        return default_value
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        log.warning("Invalid integer config for %s=%r, using default %s", config_key, raw_value, default_value)
        return default_value
