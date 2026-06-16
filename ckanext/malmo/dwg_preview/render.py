from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass
from typing import Any

import ckan.logic as logic

from .config import PreviewConfig

log = logging.getLogger(__name__)

ValidationError = logic.ValidationError


@dataclass(frozen=True)
class LayoutCandidate:
    name: str
    layout: Any
    kind: str
    entity_count: int
    viewport_count: int
    text_hint_count: int
    frame_hint_count: int


@dataclass(frozen=True)
class RenderedPreviewMetrics:
    coverage: float
    occupied_width: int
    occupied_height: int
    bbox: tuple[int, int, int, int] | None


def render_dxf_to_png(dxf_path: str, output_path: str, config: PreviewConfig) -> None:
    document = _load_dxf_document(dxf_path)
    last_error: ValidationError | None = None

    for candidate in _iter_layout_candidates(document):
        try:
            _render_layout(document, candidate, output_path, config)
            _validate_preview(output_path, candidate.name, config)
            log.info("Rendered preview accepted layout=%s bytes=%s", candidate.name, os.path.getsize(output_path))
            return
        except ValidationError as err:
            last_error = err
            log.warning("DWG preview layout render failed layout=%s error=%s", candidate.name, err.error_dict)

    if last_error is not None:
        raise last_error
    raise ValidationError({"conversion": ["Preview is currently unavailable for this drawing."]})


def _load_dxf_document(dxf_path: str) -> Any:
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError as err:
        raise ValidationError({"converter": [f"DXF renderer dependency is not installed: {err}"]})

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

    log.info(
        "Loaded DXF document path=%s auditor_errors=%s auditor_fixes=%s",
        dxf_path,
        len(getattr(auditor, "errors", [])),
        len(getattr(auditor, "fixes", [])),
    )
    return document


def _iter_layout_candidates(document: Any) -> list[LayoutCandidate]:
    candidates: list[LayoutCandidate] = []
    layout_names = getattr(document, "layout_names_in_taborder", None)
    modelspace_name = str(getattr(document.modelspace(), "name", "Model"))

    if callable(layout_names):
        for layout_name in layout_names():
            if str(layout_name).lower() == modelspace_name.lower():
                continue
            try:
                layout = document.paperspace(layout_name)
            except Exception as err:
                log.warning("Skipping paperspace layout=%s because it could not be loaded: %s", layout_name, err)
                continue
            candidate = _build_layout_candidate(str(layout_name), layout, "paperspace")
            if candidate.entity_count > 0:
                candidates.append(candidate)

    modelspace = document.modelspace()
    model_candidate = _build_layout_candidate(getattr(modelspace, "name", "Model"), modelspace, "modelspace")
    if model_candidate.entity_count > 0:
        candidates.append(model_candidate)
    return sorted(candidates, key=_layout_priority, reverse=True)


def _build_layout_candidate(layout_name: str, layout: Any, kind: str) -> LayoutCandidate:
    entity_types = [dxftype for dxftype in _iter_entity_types(layout)]
    viewport_count = entity_types.count("VIEWPORT")
    text_hint_count = sum(1 for dxftype in entity_types if dxftype in {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"})
    frame_hint_count = sum(1 for dxftype in entity_types if dxftype in {"LWPOLYLINE", "POLYLINE", "LINE"})
    return LayoutCandidate(
        name=layout_name,
        layout=layout,
        kind=kind,
        entity_count=len(entity_types),
        viewport_count=viewport_count,
        text_hint_count=text_hint_count,
        frame_hint_count=frame_hint_count,
    )


def _layout_priority(candidate: LayoutCandidate) -> tuple[int, int, int, int]:
    is_paperspace = 1 if candidate.kind == "paperspace" else 0
    return (
        is_paperspace,
        candidate.viewport_count,
        candidate.text_hint_count,
        candidate.entity_count,
    )


def _render_layout(document: Any, candidate: LayoutCandidate, output_path: str, config: PreviewConfig) -> None:
    layout = candidate.layout
    layout_name = candidate.name
    if candidate.entity_count <= 0:
        raise ValidationError({"conversion": [f'Layout "{layout_name}" does not contain drawable entities']})

    try:
        import matplotlib

        matplotlib.use("Agg")

        import matplotlib.pyplot as plt
        from ezdxf.addons.drawing import Frontend, RenderContext, config as drawing_config
        from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
        from ezdxf.addons.drawing.recorder import Recorder
    except ImportError as err:
        raise ValidationError({"converter": [f"PNG rendering dependency is not installed: {err}"]})

    dpi = 100
    figure = plt.figure(figsize=(config.image_width / dpi, config.image_height / dpi), dpi=dpi)
    axis = figure.add_axes([0, 0, 1, 1])
    axis.set_axis_off()
    axis.set_facecolor("white")
    figure.patch.set_facecolor("white")

    try:
        frontend_config = drawing_config.Configuration(
            background_policy=drawing_config.BackgroundPolicy.WHITE,
            color_policy=drawing_config.ColorPolicy.BLACK,
            lineweight_scaling=config.lineweight_scaling,
        )
        recorder = Recorder()
        frontend = Frontend(RenderContext(document), recorder, config=frontend_config)
        frontend.draw_layout(layout, finalize=True)
        player = recorder.player()
        content_bbox = _resolve_content_bbox(player, candidate, config)
        if not content_bbox.has_data:
            raise ValidationError({"conversion": [f'Layout "{layout_name}" does not contain visible drawable bounds']})

        backend = MatplotlibBackend(axis)
        player.replay(backend)
        _set_axis_limits(axis, content_bbox, config.render_margin)
        axis.set_aspect("equal", adjustable="datalim")
        figure.savefig(
            output_path,
            format="png",
            dpi=dpi,
            bbox_inches=None,
            pad_inches=0,
            facecolor="white",
            edgecolor="white",
        )
        metrics = _measure_rendered_preview(output_path)
        if not _is_preview_coverage_acceptable(metrics, config):
            if candidate.kind == "paperspace":
                tighter_bbox = _crop_bbox(content_bbox, config.retry_render_margin)
                axis.cla()
                axis.set_axis_off()
                axis.set_facecolor("white")
                backend = MatplotlibBackend(axis)
                player.replay(backend)
                _set_axis_limits(axis, tighter_bbox, config.retry_render_margin)
                axis.set_aspect("equal", adjustable="datalim")
                figure.savefig(
                    output_path,
                    format="png",
                    dpi=dpi,
                    bbox_inches=None,
                    pad_inches=0,
                    facecolor="white",
                    edgecolor="white",
                )
                metrics = _measure_rendered_preview(output_path)

        if not _is_preview_coverage_acceptable(metrics, config):
            raise ValidationError(
                {
                    "conversion": [f'Rendered preview for layout "{layout_name}" occupies too little of the image'],
                    "preview_reason": ["preview_too_sparse"],
                }
            )
    except ValidationError:
        raise
    except Exception as err:
        raise ValidationError({"conversion": [f'DXF raster rendering failed for layout "{layout_name}": {err}']})
    finally:
        plt.close(figure)


def _validate_preview(output_path: str, layout_name: str, config: PreviewConfig) -> None:
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise ValidationError({"conversion": [f'Renderer produced no output for layout "{layout_name}"']})
    if os.path.getsize(output_path) < config.min_preview_bytes:
        raise ValidationError(
            {"conversion": [f'Rendered preview for layout "{layout_name}" is too small to be trustworthy']}
        )


def _count_entities(layout: Any) -> int:
    try:
        return sum(1 for _entity in layout)
    except TypeError:
        return len(list(layout))


def _iter_entity_types(layout: Any) -> list[str]:
    types: list[str] = []
    for entity in layout:
        try:
            types.append(str(entity.dxftype()).upper())
        except Exception:
            continue
    return types


def _resolve_content_bbox(player: Any, candidate: LayoutCandidate, config: PreviewConfig) -> Any:
    full_bbox = player.bbox()
    if not full_bbox.has_data:
        return full_bbox

    if candidate.kind != "paperspace":
        return full_bbox

    width = max(float(full_bbox.extmax.x - full_bbox.extmin.x), 1.0)
    height = max(float(full_bbox.extmax.y - full_bbox.extmin.y), 1.0)
    area = width * height
    if area <= 0:
        return full_bbox

    # For paperspace layouts, reduce the chance of one stray entity making the
    # full sheet look empty by cropping lightly toward the center when the bbox
    # is unusually loose.
    cropped = _crop_bbox(full_bbox, config.retry_render_margin)
    cropped_width = max(float(cropped.extmax.x - cropped.extmin.x), 1.0)
    cropped_height = max(float(cropped.extmax.y - cropped.extmin.y), 1.0)
    cropped_area = cropped_width * cropped_height
    if cropped_area / area < config.max_initial_coverage:
        return cropped
    return full_bbox


def _set_axis_limits(axis: Any, content_bbox: Any, render_margin: float) -> None:
    extmin = content_bbox.extmin
    extmax = content_bbox.extmax
    width = max(float(extmax.x - extmin.x), 1.0)
    height = max(float(extmax.y - extmin.y), 1.0)
    pad_x = max(width * render_margin, 1.0)
    pad_y = max(height * render_margin, 1.0)

    axis.set_xlim(float(extmin.x - pad_x), float(extmax.x + pad_x))
    axis.set_ylim(float(extmin.y - pad_y), float(extmax.y + pad_y))


def _crop_bbox(content_bbox: Any, render_margin: float) -> Any:
    try:
        from ezdxf.math import BoundingBox2d
    except ImportError:
        return content_bbox

    extmin = content_bbox.extmin
    extmax = content_bbox.extmax
    width = max(float(extmax.x - extmin.x), 1.0)
    height = max(float(extmax.y - extmin.y), 1.0)
    shrink_x = width * min(max(render_margin, 0.0), 0.2)
    shrink_y = height * min(max(render_margin, 0.0), 0.2)
    return BoundingBox2d(
        [
            (float(extmin.x + shrink_x), float(extmin.y + shrink_y)),
            (float(extmax.x - shrink_x), float(extmax.y - shrink_y)),
        ]
    )


def _measure_rendered_preview(output_path: str) -> RenderedPreviewMetrics:
    try:
        from PIL import Image
    except ImportError as err:
        raise ValidationError({"converter": [f"Image validation dependency is not installed: {err}"]})

    with Image.open(output_path) as image:
        grayscale = image.convert("L")
        width, height = grayscale.size
        pixels = grayscale.load()
        occupied: list[tuple[int, int]] = []
        for y in range(height):
            for x in range(width):
                if pixels[x, y] < 245:
                    occupied.append((x, y))

    if not occupied:
        return RenderedPreviewMetrics(coverage=0.0, occupied_width=0, occupied_height=0, bbox=None)

    xs = [point[0] for point in occupied]
    ys = [point[1] for point in occupied]
    min_x = min(xs)
    max_x = max(xs)
    min_y = min(ys)
    max_y = max(ys)
    occupied_width = max_x - min_x + 1
    occupied_height = max_y - min_y + 1
    coverage = len(occupied) / float(width * height)

    return RenderedPreviewMetrics(
        coverage=coverage,
        occupied_width=occupied_width,
        occupied_height=occupied_height,
        bbox=(min_x, min_y, max_x, max_y),
    )


def _is_preview_coverage_acceptable(metrics: RenderedPreviewMetrics, config: PreviewConfig) -> bool:
    if metrics.coverage >= config.min_content_coverage:
        return True

    width_ratio = metrics.occupied_width / float(config.image_width) if config.image_width else 0.0
    height_ratio = metrics.occupied_height / float(config.image_height) if config.image_height else 0.0
    return (
        width_ratio >= config.min_occupied_width_ratio
        and height_ratio >= config.min_occupied_height_ratio
    )
