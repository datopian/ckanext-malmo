# ckanext-malmo

Customizations for the City of Malmö CKAN instance.

## Requirements

- CKAN 2.10+ (tested on 2.11)

## DWG Preview Requirements

This extension includes a DWG preview endpoint that converts DWG resources to SVG for browser preview.

Important:
- `dwg2SVG` is required at runtime
- `dwg2SVG` is provided by LibreDWG
- this is a system dependency, not a Python package dependency

That means installing the extension with `pip` is not enough by itself. The CKAN environment that runs this extension must also have LibreDWG installed and available on `PATH`.

In the local Docker-based development setup, LibreDWG is installed in the CKAN image build.

Tested runtime dependency:
- LibreDWG / `dwg2SVG` 0.13.x

## DWG Preview Configuration

The DWG preview code supports these CKAN config settings:

- `ckanext.malmo.dwg_preview_timeout`
  Conversion timeout in seconds.

- `ckanext.malmo.dwg_preview_download_timeout`
  Download timeout in seconds for remote DWG resources.

- `ckanext.malmo.dwg_preview_max_download_bytes`
  Maximum DWG download size in bytes.

- `ckanext.malmo.dwg_preview_stroke_min_width`
  Minimum stroke width (in px) enforced on generated SVG previews. Default: `1.4`.

- `ckanext.malmo.dwg_preview_stroke_color`
  Stroke color enforced on generated SVG previews. Default: `#111111`.

- `ckanext.malmo.dwg_preview_stroke_opacity`
  Stroke opacity enforced on generated SVG previews. Range: `0.0` to `1.0`. Default: `1.0`.

If these settings are not provided, the extension uses built-in defaults.

## Installation

To install `ckanext-malmo`:

1. Clone this repository (or copy the extension files).
2. Install the extension in your environment:
   ```bash
   pip install -e ckan/extensions/ckanext-malmo
   ```
3. Make sure LibreDWG / `dwg2SVG` is installed in the runtime environment.
4. Add `malmo` to the `ckan.plugins` setting in your CKAN configuration file (`ckan.ini`):
   ```ini
   ckan.plugins = ... malmo
   ```
