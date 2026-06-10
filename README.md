# ckanext-malmo

Customizations for the City of Malmo CKAN instance.

## Requirements

- CKAN 2.10+ (tested on 2.11)

## DWG Preview

This extension exposes a binary preview action at:

```text
/api/3/action/convert_dwg?id=<resource-id>
```

The preview flow is:

1. stage the DWG resource into a temporary file
2. convert DWG -> DXF with ODA File Converter
3. render DXF -> PNG with `ezdxf` and `matplotlib`
4. cache the generated PNG by resource id + file hash
5. return the PNG directly from the CKAN endpoint

Important runtime requirements:

- ODA File Converter must be installed and available on `PATH`
- `xvfb` is used automatically when `xvfb-run` is available
- Python rendering dependencies must be installed in the CKAN runtime

Python runtime dependencies:

- `ezdxf`
- `matplotlib`

System/runtime dependencies:

- ODA File Converter Linux asset (`.AppImage` or `.deb`)
- `xvfb`

## DWG Preview Configuration

The DWG preview pipeline supports these CKAN config settings:

- `ckanext.malmo.dwg_preview_timeout`
  Conversion timeout in seconds. Default: `45`.

- `ckanext.malmo.dwg_preview_download_timeout`
  Download timeout in seconds for remote DWG resources. Default: `30`.

- `ckanext.malmo.dwg_preview_max_download_bytes`
  Maximum DWG download size in bytes. Default: `104857600`.

- `ckanext.malmo.dwg_preview_oda_executable`
  Absolute path or executable name for ODA File Converter. Default: `ODAFileConverter`.

- `ckanext.malmo.dwg_preview_oda_output_version`
  DXF target version passed to ODA File Converter. Default: `ACAD2018`.

- `ckanext.malmo.dwg_preview_xvfb_screen`
  Screen configuration passed to `xvfb-run` when launching ODA File Converter in headless Docker environments. Default: `-screen 0 1600x1200x24`.

- `ckanext.malmo.dwg_preview_render_margin`
  Extra margin applied around rendered geometry. Default: `0.05`.

- `ckanext.malmo.dwg_preview_image_width`
  Output preview width in pixels. Default: `1600`.

- `ckanext.malmo.dwg_preview_image_height`
  Output preview height in pixels. Default: `1200`.

- `ckanext.malmo.dwg_preview_min_preview_bytes`
  Minimum byte size for accepting a generated preview. Default: `1024`.

- `ckanext.malmo.dwg_preview_cache_dir`
  Directory used for cached PNG previews. Default: system temporary directory + `ckan-dwg-preview-cache`.

## Docker Setup

In the local development Docker setup:

- ODA File Converter is installed during image build
- the local `src/ckanext-malmo` extension is installed at container startup from the mounted workspace
- `xvfb` is installed for headless ODA execution

Optional local ODA asset override directory:

```text
ckan/vendor/oda/
```

Supported local asset formats:

- `.AppImage`
- `.deb`

## Installation

To install `ckanext-malmo`:

1. Install the extension in your environment.
2. Install ODA File Converter and make sure `ODAFileConverter` is available in the runtime environment.
3. Add `malmo` to the `ckan.plugins` setting in your CKAN configuration file.
