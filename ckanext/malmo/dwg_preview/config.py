from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass

from ckan.plugins import toolkit

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 30
DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
DEFAULT_ODA_OUTPUT_VERSION = "ACAD2018"
DEFAULT_XVFB_SCREEN = "-screen 0 1600x1200x24"
DEFAULT_RENDER_MARGIN = 0.05
DEFAULT_IMAGE_WIDTH = 1600
DEFAULT_IMAGE_HEIGHT = 1200
DEFAULT_MIN_PREVIEW_BYTES = 1024
DEFAULT_CACHE_DIR = os.path.join(tempfile.gettempdir(), "ckan-dwg-preview-cache")
DEFAULT_MIN_CONTENT_COVERAGE = 0.002
DEFAULT_MAX_INITIAL_COVERAGE = 0.6
DEFAULT_RETRY_RENDER_MARGIN = 0.01
DEFAULT_LINEWEIGHT_SCALING = 1.5
DEFAULT_MIN_OCCUPIED_WIDTH_RATIO = 0.2
DEFAULT_MIN_OCCUPIED_HEIGHT_RATIO = 0.2


@dataclass(frozen=True)
class PreviewConfig:
    timeout: int
    download_timeout: int
    max_download_bytes: int
    oda_executable: str
    oda_output_version: str
    xvfb_screen: str
    render_margin: float
    image_width: int
    image_height: int
    min_preview_bytes: int
    cache_dir: str
    min_content_coverage: float
    max_initial_coverage: float
    retry_render_margin: float
    lineweight_scaling: float
    min_occupied_width_ratio: float
    min_occupied_height_ratio: float

    @classmethod
    def from_ckan_config(cls) -> "PreviewConfig":
        return cls(
            timeout=_get_int("ckanext.malmo.dwg_preview_timeout", DEFAULT_TIMEOUT_SECONDS, minimum=1),
            download_timeout=_get_int(
                "ckanext.malmo.dwg_preview_download_timeout",
                DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
                minimum=1,
            ),
            max_download_bytes=_get_int(
                "ckanext.malmo.dwg_preview_max_download_bytes",
                DEFAULT_MAX_DOWNLOAD_BYTES,
                minimum=1024,
            ),
            oda_executable=_get_string("ckanext.malmo.dwg_preview_oda_executable", "ODAFileConverter"),
            oda_output_version=_get_string(
                "ckanext.malmo.dwg_preview_oda_output_version",
                DEFAULT_ODA_OUTPUT_VERSION,
            ),
            xvfb_screen=_get_string(
                "ckanext.malmo.dwg_preview_xvfb_screen",
                DEFAULT_XVFB_SCREEN,
            ),
            render_margin=_get_float(
                "ckanext.malmo.dwg_preview_render_margin",
                DEFAULT_RENDER_MARGIN,
                minimum=0.0,
            ),
            image_width=_get_int(
                "ckanext.malmo.dwg_preview_image_width",
                DEFAULT_IMAGE_WIDTH,
                minimum=256,
            ),
            image_height=_get_int(
                "ckanext.malmo.dwg_preview_image_height",
                DEFAULT_IMAGE_HEIGHT,
                minimum=256,
            ),
            min_preview_bytes=_get_int(
                "ckanext.malmo.dwg_preview_min_preview_bytes",
                DEFAULT_MIN_PREVIEW_BYTES,
                minimum=1,
            ),
            cache_dir=_get_string("ckanext.malmo.dwg_preview_cache_dir", DEFAULT_CACHE_DIR),
            min_content_coverage=_get_float(
                "ckanext.malmo.dwg_preview_min_content_coverage",
                DEFAULT_MIN_CONTENT_COVERAGE,
                minimum=0.00001,
            ),
            max_initial_coverage=_get_float(
                "ckanext.malmo.dwg_preview_max_initial_coverage",
                DEFAULT_MAX_INITIAL_COVERAGE,
                minimum=0.001,
            ),
            retry_render_margin=_get_float(
                "ckanext.malmo.dwg_preview_retry_render_margin",
                DEFAULT_RETRY_RENDER_MARGIN,
                minimum=0.0,
            ),
            lineweight_scaling=_get_float(
                "ckanext.malmo.dwg_preview_lineweight_scaling",
                DEFAULT_LINEWEIGHT_SCALING,
                minimum=0.1,
            ),
            min_occupied_width_ratio=_get_float(
                "ckanext.malmo.dwg_preview_min_occupied_width_ratio",
                DEFAULT_MIN_OCCUPIED_WIDTH_RATIO,
                minimum=0.0,
            ),
            min_occupied_height_ratio=_get_float(
                "ckanext.malmo.dwg_preview_min_occupied_height_ratio",
                DEFAULT_MIN_OCCUPIED_HEIGHT_RATIO,
                minimum=0.0,
            ),
        )


def _get_string(config_key: str, default_value: str) -> str:
    raw_value = toolkit.config.get(config_key)
    if raw_value in (None, ""):
        return default_value
    value = str(raw_value).strip()
    return value or default_value


def _get_int(config_key: str, default_value: int, minimum: int | None = None) -> int:
    raw_value = toolkit.config.get(config_key)
    if raw_value in (None, ""):
        return default_value
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        log.warning("Invalid integer config %s=%r, using default %s", config_key, raw_value, default_value)
        return default_value
    if minimum is not None and value < minimum:
        log.warning("Config %s=%r is below minimum %s, using default %s", config_key, value, minimum, default_value)
        return default_value
    return value


def _get_float(config_key: str, default_value: float, minimum: float | None = None) -> float:
    raw_value = toolkit.config.get(config_key)
    if raw_value in (None, ""):
        return default_value
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        log.warning("Invalid float config %s=%r, using default %s", config_key, raw_value, default_value)
        return default_value
    if minimum is not None and value < minimum:
        log.warning("Config %s=%r is below minimum %s, using default %s", config_key, value, minimum, default_value)
        return default_value
    return value
