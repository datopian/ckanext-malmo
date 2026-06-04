from __future__ import annotations

import logging
import os
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
DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30
DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_ODA_OUTPUT_VERSION = "ACAD2018"
DEFAULT_RENDER_MARGIN_MM = 4
DEFAULT_RENDER_PAGE_SIZE_MM = 160
DEFAULT_MIN_PREVIEW_BYTES = 1024
DEFAULT_MAX_MODELSPACE_ENTITIES = 5000
DOWNLOAD_CHUNK_SIZE = 64 * 1024
PDF_MIMETYPE = "application/pdf"
PDF_EXTENSION = "pdf"


def build_preview_payload(context: dict[str, Any], data_dict: dict[str, Any]) -> dict[str, Any]:
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

    log.info("DWG preview requested for resource=%s format=pdf", resource_id)

    with TemporaryDirectory(prefix="ckan-dwg-preview-") as tmp_dir:
        source_path = _stage_resource_dwg(
            resource,
            tmp_dir,
            max_download_bytes=max_download_bytes,
            download_timeout=download_timeout,
        )
        preview_path = _build_preview_file(source_path, tmp_dir, timeout=conversion_timeout)
        with open(preview_path, "rb") as output_file:
            content = output_file.read()

    return {
        "content": content,
        "filename": _build_output_filename(resource),
        "mimetype": PDF_MIMETYPE,
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
        log.info("Preparing uploaded DWG resource=%s", resource.get("id"))
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
        log.info("Downloading external DWG resource=%s url=%s", resource.get("id"), resource_url)
        _download_to_path(
            resource_url,
            source_path,
            max_download_bytes=max_download_bytes,
            download_timeout=download_timeout,
            source_label="external DWG resource",
        )

    if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
        raise ValidationError({"resource_id": ["DWG source file could not be prepared"]})

    log.info(
        "Prepared DWG source resource=%s path=%s bytes=%s",
        resource.get("id"),
        source_path,
        os.path.getsize(source_path),
    )
    return source_path


def _copy_uploaded_resource(
    resource: dict[str, Any],
    destination_path: str,
    max_download_bytes: int,
    download_timeout: int,
) -> None:
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
        _copy_local_file(local_path, destination_path, max_download_bytes=max_download_bytes)
        return

    if all(
        hasattr(resource_upload, attribute)
        for attribute in ("bucket_name", "get_path", "get_signed_url_to_key")
    ):
        use_readonly_credentials = bool(
            getattr(resource_upload, "p_key_readonly", None)
            and getattr(resource_upload, "s_key_readonly", None)
        )
        remote_key = None
        try:
            remote_key = resource_upload.get_path(resource_id, resource_name)
            signed_url = resource_upload.get_signed_url_to_key(
                remote_key,
                read_only=use_readonly_credentials,
            )
        except Exception as err:
            if use_readonly_credentials and remote_key:
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

        log.info(
            "Downloading uploaded DWG from remote storage resource=%s key=%s",
            resource_id,
            remote_key,
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

    log.info(
        "Downloaded %s path=%s bytes=%s",
        source_label,
        destination_path,
        bytes_downloaded,
    )


def _build_preview_file(source_path: str, tmp_dir: str, timeout: int) -> str:
    dxf_path = _convert_dwg_to_dxf(source_path, tmp_dir, timeout=timeout)
    document = _load_dxf_document(dxf_path)
    preview_path = _render_best_layout_preview(document, tmp_dir)
    log.info("DWG preview generated format=pdf path=%s bytes=%s", preview_path, os.path.getsize(preview_path))
    return preview_path


def _convert_dwg_to_dxf(source_path: str, tmp_dir: str, timeout: int) -> str:
    executable = _resolve_oda_executable()
    output_version = str(
        toolkit.config.get("ckanext.malmo.dwg_preview_oda_output_version")
        or DEFAULT_ODA_OUTPUT_VERSION
    ).strip() or DEFAULT_ODA_OUTPUT_VERSION

    input_dir = os.path.join(tmp_dir, "oda-input")
    output_dir = os.path.join(tmp_dir, "oda-output")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    input_name = os.path.basename(source_path)
    staged_input_path = os.path.join(input_dir, input_name)
    shutil.copyfile(source_path, staged_input_path)

    command = _build_oda_command([
        executable,
        input_dir,
        output_dir,
        output_version,
        "DXF",
        "0",
        "1",
        "*.dwg",
    ])
    log.info("Running ODA File Converter command=%s", command)
    result = _run_subprocess(command, timeout=timeout)
    stderr = _decode_subprocess_output(result.stderr)
    stdout = _decode_subprocess_output(result.stdout)
    log.info(
        "ODA File Converter finished code=%s stdout=%s stderr=%s",
        result.returncode,
        stdout or "<empty>",
        stderr or "<empty>",
    )

    if result.returncode != 0:
        raise ValidationError(
            {
                "conversion": [
                    "DWG to DXF conversion failed"
                    + (f": {stderr}" if stderr else "")
                ]
            }
        )

    dxf_path = _find_generated_dxf(output_dir, input_name)
    if not dxf_path or not os.path.exists(dxf_path) or os.path.getsize(dxf_path) == 0:
        raise ValidationError(
            {"conversion": ["DWG to DXF conversion did not produce a usable DXF file"]}
        )

    log.info("Generated DXF path=%s bytes=%s", dxf_path, os.path.getsize(dxf_path))
    return dxf_path


def _build_oda_command(oda_arguments: list[str]) -> list[str]:
    xvfb_run = shutil.which("xvfb-run")
    if not xvfb_run:
        return oda_arguments

    screen_spec = str(
        toolkit.config.get("ckanext.malmo.dwg_preview_xvfb_screen")
        or "-screen 0 1024x768x24"
    ).strip() or "-screen 0 1024x768x24"
    return [xvfb_run, "-a", "-s", screen_spec, *oda_arguments]


def _find_generated_dxf(output_dir: str, input_name: str) -> str | None:
    expected_name = os.path.splitext(input_name)[0] + ".dxf"
    expected_path = os.path.join(output_dir, expected_name)
    if os.path.exists(expected_path):
        return expected_path

    for root, _dirs, files in os.walk(output_dir):
        for file_name in files:
            if file_name.lower().endswith(".dxf"):
                return os.path.join(root, file_name)
    return None


def _load_dxf_document(dxf_path: str) -> Any:
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as err:
        raise ValidationError(
            {"converter": [f"DXF renderer dependency is not installed: {err}"]}
        )

    try:
        document = ezdxf.readfile(dxf_path)
        log.info("Loaded DXF document path=%s using fast read path", dxf_path)
        return document
    except Exception as fast_err:
        log.warning("Fast DXF load failed for %s, retrying recovery path: %s", dxf_path, fast_err)

    try:
        document, auditor = recover.readfile(dxf_path)
    except Exception as err:
        raise ValidationError({"conversion": [f"Generated DXF could not be parsed: {err}"]})

    error_count = len(getattr(auditor, "errors", []))
    fixed_error_count = len(getattr(auditor, "fixes", []))
    log.info(
        "Loaded DXF document path=%s auditor_errors=%s auditor_fixes=%s",
        dxf_path,
        error_count,
        fixed_error_count,
    )
    return document


def _render_best_layout_preview(document: Any, tmp_dir: str) -> str:
    failed_errors: list[ValidationError] = []
    failed_layouts: list[str] = []

    for layout_name, layout_kind, layout, entity_count in _iter_layout_candidates(document):
        try:
            _guard_preview_complexity(layout_name, layout_kind, entity_count)
            return _render_layout_preview(document, layout, layout_name, entity_count, tmp_dir)
        except ValidationError as err:
            failed_errors.append(err)
            failed_layouts.append(f"{layout_name}: {err.error_dict}")
            log.warning("DWG preview layout render failed layout=%s kind=%s entities=%s error=%s", layout_name, layout_kind, entity_count, err.error_dict)

    if len(failed_errors) == 1:
        raise failed_errors[0]

    raise ValidationError(
        {
            "conversion": [
                "Preview is currently unavailable for this drawing."
            ],
            "preview_reason": ["preview_unavailable"],
        }
    )


def _iter_layout_candidates(document: Any) -> list[tuple[str, str, Any, int]]:
    candidates: list[tuple[str, str, Any, int]] = []

    layout_names_method = getattr(document, "layout_names_in_taborder", None)
    modelspace_name = str(getattr(document.modelspace(), "name", "Model"))
    if callable(layout_names_method):
        for layout_name in list(layout_names_method()):
            if str(layout_name).lower() == modelspace_name.lower():
                continue
            try:
                layout = document.paperspace(layout_name)
            except Exception as err:
                log.warning("Skipping paperspace layout=%s because it could not be loaded: %s", layout_name, err)
                continue
            entity_count = _count_layout_entities(layout)
            log.info("Found paperspace layout=%s entities=%s", layout_name, entity_count)
            if entity_count > 0:
                candidates.append((str(layout_name), "paperspace", layout, entity_count))

    modelspace = document.modelspace()
    modelspace_entity_count = _count_layout_entities(modelspace)
    log.info("Found modelspace layout=%s entities=%s", getattr(modelspace, "name", "Model"), modelspace_entity_count)
    candidates.append((getattr(modelspace, "name", "Model"), "modelspace", modelspace, modelspace_entity_count))
    return candidates


def _render_layout_preview(document: Any, layout: Any, layout_name: str, entity_count: int, tmp_dir: str) -> str:
    if entity_count <= 0:
        raise ValidationError(
            {"conversion": [f'Layout "{layout_name}" does not contain drawable entities']}
        )

    preview_path = os.path.join(
        tmp_dir,
        f"preview.{_sanitize_filename_component(layout_name)}.{PDF_EXTENSION}",
    )
    log.info("Rendering DXF layout=%s entities=%s target=%s", layout_name, entity_count, preview_path)
    _render_layout_to_pdf(document, layout, preview_path)
    _validate_rendered_preview(preview_path, layout_name)
    log.info("Rendered preview accepted layout=%s bytes=%s", layout_name, os.path.getsize(preview_path))
    return preview_path


def _render_layout_to_pdf(document: Any, layout: Any, output_path: str) -> None:
    try:
        from ezdxf.addons.drawing import Frontend, RenderContext, layout as drawing_layout, pymupdf
    except ImportError as err:
        raise ValidationError(
            {"converter": [f"PDF rendering dependency is not installed: {err}"]}
        )

    margin_mm = max(
        0.0,
        _get_float_config("ckanext.malmo.dwg_preview_render_margin_mm", DEFAULT_RENDER_MARGIN_MM),
    )

    try:
        context = RenderContext(document)
        backend = pymupdf.PyMuPdfBackend()
        frontend = Frontend(context, backend)
        frontend.draw_layout(layout, finalize=True)
        page = _build_preview_page(drawing_layout, margin_mm)
        pdf_bytes = backend.get_pdf_bytes(page)
        with open(output_path, "wb") as output_file:
            output_file.write(pdf_bytes)
    except Exception as err:
        raise ValidationError(
            {
                "conversion": [
                    f'DXF PDF rendering failed for layout "{getattr(layout, "name", "unknown")}": {err}'
                ]
            }
        )


def _build_preview_page(drawing_layout: Any, margin_mm: float) -> Any:
    page_size_mm = max(
        50.0,
        _get_float_config(
            "ckanext.malmo.dwg_preview_render_page_size_mm",
            DEFAULT_RENDER_PAGE_SIZE_MM,
        ),
    )
    return drawing_layout.Page(
        page_size_mm,
        page_size_mm,
        drawing_layout.Units.mm,
        margins=drawing_layout.Margins.all(margin_mm),
    )


def _validate_rendered_preview(preview_path: str, layout_name: str) -> None:
    if not os.path.exists(preview_path) or os.path.getsize(preview_path) == 0:
        raise ValidationError(
            {"conversion": [f'Renderer produced no output for layout "{layout_name}"']}
        )

    minimum_size_bytes = _get_int_config(
        "ckanext.malmo.dwg_preview_min_preview_bytes",
        DEFAULT_MIN_PREVIEW_BYTES,
    )
    if os.path.getsize(preview_path) < minimum_size_bytes:
        raise ValidationError(
            {"conversion": [f'Rendered preview for layout "{layout_name}" is too small to be trustworthy']}
        )


def _guard_preview_complexity(layout_name: str, layout_kind: str, entity_count: int) -> None:
    if layout_kind != "modelspace":
        return

    max_modelspace_entities = _get_int_config(
        "ckanext.malmo.dwg_preview_max_modelspace_entities",
        DEFAULT_MAX_MODELSPACE_ENTITIES,
    )
    if entity_count > max_modelspace_entities:
        raise ValidationError(
            {
                "conversion": [
                    "This drawing is too detailed to preview here."
                ],
                "preview_reason": ["modelspace_too_complex"],
            }
        )


def _count_layout_entities(layout: Any) -> int:
    try:
        return sum(1 for _entity in layout)
    except TypeError:
        return len(list(layout))


def _resolve_oda_executable() -> str:
    configured_path = str(
        toolkit.config.get("ckanext.malmo.dwg_preview_oda_executable") or "ODAFileConverter"
    ).strip() or "ODAFileConverter"
    if os.path.isabs(configured_path):
        if os.path.exists(configured_path) and os.access(configured_path, os.X_OK):
            return configured_path
        raise ValidationError(
            {
                "converter": [
                    f'Configured ODA File Converter is not executable: "{configured_path}"'
                ]
            }
        )

    resolved = shutil.which(configured_path)
    if resolved:
        return resolved

    raise ValidationError(
        {
            "converter": [
                'ODA File Converter is not installed. Configure `ckanext.malmo.dwg_preview_oda_executable` or add `ODAFileConverter` to PATH.'
            ]
        }
    )


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
    return f"{base_name}.{PDF_EXTENSION}"


def _sanitize_filename_component(value: str) -> str:
    sanitized = "".join(char if char.isalnum() or char in "._-" else "-" for char in value).strip("-")
    return sanitized or "layout"


def _get_int_config(config_key: str, default_value: int) -> int:
    raw_value = toolkit.config.get(config_key)
    if raw_value in (None, ""):
        return default_value
    try:
        return int(raw_value)
    except (TypeError, ValueError):
        log.warning("Invalid integer config %s=%r, using default %s", config_key, raw_value, default_value)
        return default_value


def _get_float_config(config_key: str, default_value: float) -> float:
    raw_value = toolkit.config.get(config_key)
    if raw_value in (None, ""):
        return default_value
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        log.warning("Invalid float config %s=%r, using default %s", config_key, raw_value, default_value)
        return default_value
