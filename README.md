# ckanext-malmo

Customizations for the City of Malm? CKAN instance.

## Requirements

- CKAN 2.10+ (tested on 2.11)

## DWG Preview Requirements

This extension includes a DWG preview endpoint that converts DWG resources into browser-previewable PDFs.

Important:
- ODA File Converter is required at runtime for DWG -> DXF conversion
- Python rendering dependencies are required at runtime for DXF -> PDF rendering
- these are runtime dependencies, not just Python package dependencies

The preview pipeline is:
1. stage the DWG resource into a temporary file
2. convert DWG -> DXF with ODA File Converter
3. render DXF -> PDF
4. return the generated PDF through the existing CKAN action and endpoint

That means installing the extension with `pip` is not enough by itself. The CKAN environment that runs this extension must also have ODA File Converter installed and available on `PATH`.

In the local Docker-based development setup, the CKAN image installs ODA File Converter automatically from the official ODA Linux AppImage by default. You can also override that by placing an official asset in `ckan/vendor/oda/`.

Python runtime dependencies:
- `ezdxf`
- `PyMuPDF`

System/runtime dependencies:
- ODA File Converter Linux asset (`.AppImage` or `.deb`)
- `xvfb` for headless execution of ODA File Converter when the Linux build only exposes the Qt `xcb` plugin

## DWG Preview Configuration

The DWG preview code supports these CKAN config settings:

- `ckanext.malmo.dwg_preview_timeout`
  Conversion timeout in seconds. Default: `45`.

- `ckanext.malmo.dwg_preview_download_timeout`
  Download timeout in seconds for remote DWG resources.

- `ckanext.malmo.dwg_preview_max_download_bytes`
  Maximum DWG download size in bytes.

- `ckanext.malmo.dwg_preview_oda_executable`
  Absolute path or executable name for ODA File Converter. Default: `ODAFileConverter`.

- `ckanext.malmo.dwg_preview_oda_output_version`
  DXF target version passed to ODA File Converter. Default: `ACAD2018`.

- `ckanext.malmo.dwg_preview_render_margin_mm`
  Extra page margin applied around rendered geometry to avoid edge clipping in previews. Default: `4`.

- `ckanext.malmo.dwg_preview_render_page_size_mm`
  Fixed square page size used for generated previews so large drawings are scaled down into a browser-friendly PDF. Default: `160`.

- `ckanext.malmo.dwg_preview_xvfb_screen`
  Screen configuration passed to `xvfb-run` when launching ODA File Converter in headless Docker environments. Default: `-screen 0 1024x768x24`.

- `ckanext.malmo.dwg_preview_min_preview_bytes`
  Minimum byte size for accepting a generated preview. Default: `1024`.

- `ckanext.malmo.dwg_preview_max_modelspace_entities`
  Maximum entity count allowed for a modelspace-only inline preview. Files above this limit fail fast with a download-first message. Default: `5000`.

If these settings are not provided, the extension uses built-in defaults.

## Docker Setup

By default, the Docker image downloads the ODA File Converter Linux AppImage from the official ODA site during build.

Optional local override directory:

```text
ckan/vendor/oda/
```

Supported local asset formats:
- `.AppImage`
- `.deb`

Default configured asset name:

```text
ODAFileConverter_QT6_lnxX64_8.3dll_27.1.AppImage
```

Build resolution order:
1. use the file named by `ODA_FILE_CONVERTER_ASSET` if it exists in `ckan/vendor/oda/`
2. otherwise download that filename from the official ODA site
3. if `ODA_FILE_CONVERTER_ASSET` is empty, discover the current AppImage filename from the official ODA catalog page and download it

## Installation

To install `ckanext-malmo`:

1. Clone this repository (or copy the extension files).
2. Install the extension in your environment:
   ```bash
   pip install -e ckan/extensions/ckanext-malmo
   ```
3. Install ODA File Converter and make sure `ODAFileConverter` is available in the runtime environment.
4. Add `malmo` to the `ckan.plugins` setting in your CKAN configuration file (`ckan.ini`):
   ```ini
   ckan.plugins = ... malmo
   ```
