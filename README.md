# ckanext-malmo

Customizations for the City of Malmö CKAN instance.

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
