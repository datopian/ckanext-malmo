from __future__ import annotations

from ckan.plugins import toolkit

from ckanext.malmo import dwg_preview


@toolkit.side_effect_free
def convert_dwg(context, data_dict):
    """
    Convert a DWG resource into a previewable PNG payload.

    This action returns a Python dictionary containing binary bytes for
    internal callers. The public HTTP endpoint is exposed via a Flask
    blueprint at /api/3/action/convert_dwg so CKAN can return the
    preview directly instead of JSON-wrapping the response.
    """
    return dwg_preview.build_preview_payload(context, data_dict)
