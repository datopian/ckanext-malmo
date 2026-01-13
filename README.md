# ckanext-malmo

Organization masking for the City of Malmö CKAN instance.

## Overview

This extension is designed to mask the original organizations of datasets and expose them all as being owned by a single organization: **Malmö Stad** (slug: `malmo`).

This is achieved by intercepting the dataset data at the controller level, ensuring that both the Web UI and the API (`package_show`, `package_search`) report the masked organization.

## Features

- **Organization Masking**: Intercepts `IPackageController` hooks to replace the `organization` dictionary in dataset responses.
- **API Support**: Impacts `package_show` and `package_search` actions.
- **DCAT Integration**: Since it modifies the data dictionary, DCAT exports (via `ckanext-dcat`) also reflect the masked organization automatically.

## Requirements

- CKAN 2.10+ (tested on 2.11)

## Installation

To install `ckanext-malmo`:

1.  Clone this repository (or copy the extension files).
2.  Install the extension in your environment:
    ```bash
    pip install -e ckan/extensions/ckanext-malmo
    ```
3.  Add `malmo` to the `ckan.plugins` setting in your CKAN configuration file (`ckan.ini`):
    ```ini
    ckan.plugins = ... malmo
    ```

## Configuration

The extension uses the following defaults (hardcoded in `plugin.py` for robustness):

- **Organization Slug**: `malmo`
- **Organization Title**: `Malmö Stad`

Note: The organization `malmo` must exist in the CKAN database for links and relationships to resolve correctly. The project's `prerun.py` script is updated to create this organization automatically during startup.
