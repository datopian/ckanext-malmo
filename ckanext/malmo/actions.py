from __future__ import annotations

import logging
import markdown
import html2text
import json
from typing import Any

from ckan.common import config, _
import ckan.logic as logic

log = logging.getLogger(__name__)

# Action Shortcuts for standard CKAN logic patterns
_get_action = logic.get_action
ValidationError = logic.ValidationError
NotFound = logic.NotFound
NotAuthorized = logic.NotAuthorized
fresh_context = logic.fresh_context

# Target Fields defining which metadata attributes are eligible for translation
DATASET_FIELDS = ["title", "notes"]
RESOURCE_FIELDS = ["name", "description"]
GROUP_ORG_FIELDS = ["title", "description", "display_name"]


# HELPERS
# -------


def _prepare_metadata(raw_value: Any) -> Any:
    """
    Decodes JSON strings and peels double-encoding layers if present.

    Filters out dictionaries that contain only empty or whitespace-only
    translation values to ensure clean API outputs.
    """
    if not raw_value:
        return None

    processed_metadata = raw_value

    # Peel JSON layers if value is double-encoded as a string
    while isinstance(processed_metadata, str) and processed_metadata.strip().startswith(
        ("{", '"')
    ):
        try:
            decoded_metadata = json.loads(processed_metadata)

            if decoded_metadata == processed_metadata:
                break

            processed_metadata = decoded_metadata

        except (json.JSONDecodeError, TypeError):
            break

    if isinstance(processed_metadata, dict):
        # Filter out keys with empty or whitespace-only values
        cleaned_metadata = {
            lang: text
            for lang, text in processed_metadata.items()
            if isinstance(text, str) and text.strip()
        }

        return cleaned_metadata if cleaned_metadata else None

    return processed_metadata


def _get_all_group_translations(model):
    """
    Performs a direct database query to fetch all group/org translation extras.

    This bulk fetch is used to avoid N+1 query performance issues during
    search and list operations.
    """
    from ckan.model import GroupExtra

    translation_extras = (
        model.Session.query(GroupExtra)
        .filter(GroupExtra.key.contains("_translated"))
        .all()
    )

    translation_mapping = {}

    for extra in translation_extras:
        if extra.group_id not in translation_mapping:
            translation_mapping[extra.group_id] = {}

        translation_mapping[extra.group_id][extra.key] = extra.value

    return translation_mapping


# TRANSLATION
# -----------


def _translate_fields(
    context, data_dict, fields_to_translate=["title", "notes"], translate_resources=True
):
    """
    Coordinates the translation of specific fields via the 'translate' action
    from ckanext-translate.

    Handles HTML conversion for rich-text fields (notes/description) and
    manages resource-level translations if applicable.
    """
    html_to_text_converter = html2text.HTML2Text()
    default_language = config.get("ckan.locale_default", "sv").split("_")[0]

    # Initialize default language values in the data_dict
    for field in fields_to_translate:
        val = data_dict.get(field, "")
        data_dict["{}_translated-{}".format(field, default_language)] = val

    languages_offered = config.get("ckan.locales_offered", ["en", "da_DK"])
    target_languages = [
        lang_code.split("_")[0]
        for lang_code in languages_offered
        if lang_code != default_language
    ]

    for language_code in target_languages:
        try:
            # Prepare payload; notes/descriptions are converted to markdown
            translation_payload = {
                field_name: (
                    markdown.markdown(data_dict.get(field_name, ""))
                    if field_name == "notes" or field_name == "description"
                    else data_dict.get(field_name, "")
                )
                for field_name in fields_to_translate
            }

            translation_result = _get_action("translate")(
                context,
                {
                    "input": translation_payload,
                    "from": default_language,
                    "to": language_code,
                },
            )
            translated_outputs = translation_result.get("output", {})

            for field_name in fields_to_translate:
                translated_text = translated_outputs.get(field_name, "").strip("\n")

                # Convert translated markdown back to plain text/simple HTML
                if field_name in ["notes", "description"]:
                    translated_text = html_to_text_converter.handle(translated_text)

                data_dict["{}_translated-{}".format(field_name, language_code)] = (
                    translated_text
                )

        except Exception as error:
            log.debug(f"Translation failed for {language_code}: {error}")

    # Consolidate flat translation keys into JSON objects
    data_dict = _format_translated_fields(data_dict)

    if translate_resources and "resources" in data_dict:
        data_dict = _translate_resources(context, data_dict)

    return data_dict


def _format_translated_fields(data_dict):
    """
    Aggregates flat '<FIELD>_translated-<LANG>' keys into a single '<FIELD>_translated'
    JSON dictionary.
    """
    fields_to_format = {
        key.split("_translated-")[0]
        for key in data_dict.keys()
        if "_translated-" in key
    }

    for field_name in fields_to_format:
        language_map = {}
        matched_keys = [
            key
            for key in data_dict.keys()
            if key.startswith(f"{field_name}_translated-")
        ]

        for matched_key in matched_keys:
            lang_suffix = matched_key.split("-")[-1]
            language_map[lang_suffix] = data_dict.pop(matched_key)

        if language_map:
            data_dict[f"{field_name}_translated"] = json.dumps(language_map)

    return data_dict


def _translate_resources(context, data_dict):
    """
    Iteratively triggers field translation for all resources attached to
     a dataset.
    """
    if "resources" in data_dict and isinstance(data_dict["resources"], list):
        for resource in data_dict["resources"]:
            _translate_fields(
                context, resource, RESOURCE_FIELDS, translate_resources=False
            )

    return data_dict


# DATASET ACTIONS
# ---------------


@logic.chained_action
def package_create(next_action, context, data_dict):
    """Chained action to translate dataset fields on creation."""
    return next_action(context, _translate_fields(context, data_dict, DATASET_FIELDS))


@logic.chained_action
def package_update(next_action, context, data_dict):
    """Chained action to translate dataset fields on update."""
    return next_action(context, _translate_fields(context, data_dict, DATASET_FIELDS))


@logic.chained_action
def package_patch(next_action, context, data_dict):
    """Chained action to translate dataset fields on patch."""
    return next_action(context, _translate_fields(context, data_dict, DATASET_FIELDS))


@logic.side_effect_free
@logic.chained_action
def package_show(next_action, context, data_dict):
    """
    Chained action to inject cleaned translation metadata for Organizations
    and Groups into the dataset view.
    """
    package_dict = next_action(context, data_dict)
    all_group_translations = _get_all_group_translations(context["model"])

    def _inject_metadata(target_object):
        target_id = target_object.get("id")

        if target_id in all_group_translations:
            # Clean and peel the metadata for display
            cleaned_metadata = {
                key: _prepare_metadata(val)
                for key, val in all_group_translations[target_id].items()
                if _prepare_metadata(val)
            }
            target_object.update(cleaned_metadata)

            if "title_translated" in cleaned_metadata:
                target_object["display_name_translated"] = cleaned_metadata[
                    "title_translated"
                ]

    # Inject into owner organization
    if package_dict.get("organization"):
        _inject_metadata(package_dict["organization"])

    # Inject into associated groups
    for group_dict in package_dict.get("groups", []):
        _inject_metadata(group_dict)

    return package_dict


@logic.side_effect_free
@logic.chained_action
def package_search(next_action, context, data_dict):
    """
    Chained action to inject cleaned translations into search results
    and search facets.
    """
    search_results = next_action(context, data_dict)

    if context.get("ignore_search_translations"):
        return search_results

    # Use bulk database fetch for performance
    all_group_translations = _get_all_group_translations(context["model"])
    from ckan.model import Group

    # Create mapping of UUID to Name (slug)
    group_rows = context["model"].Session.query(Group.id, Group.name).all()
    id_to_name_map = {row.id: row.name for row in group_rows}

    translations_by_id = {}
    translations_by_name = {}

    # Pre-clean translation lookups to avoid repeated processing in loops
    for group_id, field_values in all_group_translations.items():
        cleaned_values = {
            key: _prepare_metadata(val)
            for key, val in field_values.items()
            if _prepare_metadata(val)
        }
        if cleaned_values:
            translations_by_id[group_id] = cleaned_values

            if group_id in id_to_name_map:
                translations_by_name[id_to_name_map[group_id]] = cleaned_values

    # Process search result items
    for package_dict in search_results.get("results", []):
        org_id = package_dict.get("organization", {}).get("id")

        if org_id in translations_by_id:
            package_dict["organization"].update(translations_by_id[org_id])
            package_dict["organization"]["display_name_translated"] = (
                translations_by_id[org_id].get("title_translated")
            )

        for group_dict in package_dict.get("groups", []):
            group_id = group_dict.get("id")

            if group_id in translations_by_id:
                group_dict.update(translations_by_id[group_id])
                group_dict["display_name_translated"] = translations_by_id[
                    group_id
                ].get("title_translated")

    # Sync facets with translation data
    current_language = context.get(
        "lang", config.get("ckan.locale_default", "sv")
    ).split("_")[0]
    search_facets = search_results.get("search_facets", {})

    for facet_type in ["organization", "groups"]:
        if facet_type in search_facets:
            for facet_item in search_facets[facet_type].get("items", []):
                entity_name = facet_item.get("name")

                if entity_name in translations_by_name:
                    translation_data = translations_by_name[entity_name]
                    facet_item.update(translation_data)

                    # Update display_name based on current session language
                    if "title_translated" in translation_data:
                        translated_title = translation_data["title_translated"].get(
                            current_language
                        )

                        if translated_title:
                            facet_item["display_name"] = translated_title

    return search_results


# RESOURCE ACTIONS
# ----------------


@logic.chained_action
def resource_create(next_action, context, data_dict):
    """Chained action to translate resource fields on creation."""
    return next_action(context, _translate_fields(context, data_dict, RESOURCE_FIELDS))


@logic.chained_action
def resource_update(next_action, context, data_dict):
    """Chained action to translate resource fields on update."""
    return next_action(context, _translate_fields(context, data_dict, RESOURCE_FIELDS))


@logic.chained_action
def resource_patch(next_action, context, data_dict):
    """Chained action to translate resource fields on patch."""
    return next_action(context, _translate_fields(context, data_dict, RESOURCE_FIELDS))


# ORGANIZATION ACTIONS
# --------------------


@logic.chained_action
def organization_create(next_action, context, data_dict):
    """Chained action to translate organization fields on creation."""
    return next_action(context, _translate_fields(context, data_dict, GROUP_ORG_FIELDS))


@logic.chained_action
def organization_update(next_action, context, data_dict):
    """Chained action to translate organization fields on update."""
    return next_action(context, _translate_fields(context, data_dict, GROUP_ORG_FIELDS))


@logic.chained_action
def organization_patch(next_action, context, data_dict):
    """Chained action to translate organization fields on patch."""
    return next_action(context, _translate_fields(context, data_dict, GROUP_ORG_FIELDS))


@logic.side_effect_free
@logic.chained_action
def organization_show(next_action, context, data_dict):
    """Chained action to clean translation metadata in organization views."""
    organization_dict = next_action(context, data_dict)

    # Clean translation dictionaries in the output for API/View consistency
    for key in list(organization_dict.keys()):
        if key.endswith("_translated"):
            organization_dict[key] = _prepare_metadata(organization_dict[key])

    return organization_dict


# GROUP ACTIONS
# -------------


@logic.chained_action
def group_create(next_action, context, data_dict):
    """Chained action to translate group fields on creation."""
    return next_action(context, _translate_fields(context, data_dict, GROUP_ORG_FIELDS))


@logic.chained_action
def group_update(next_action, context, data_dict):
    """Chained action to translate group fields on update."""
    return next_action(context, _translate_fields(context, data_dict, GROUP_ORG_FIELDS))


@logic.chained_action
def group_patch(next_action, context, data_dict):
    """Chained action to translate group fields on patch."""
    return next_action(context, _translate_fields(context, data_dict, GROUP_ORG_FIELDS))


@logic.side_effect_free
@logic.chained_action
def group_show(next_action, context, data_dict):
    """Chained action to clean translation metadata in group views."""
    group_dict = next_action(context, data_dict)

    # Clean translation dictionaries in the output for API/View consistency
    for key in list(group_dict.keys()):
        if key.endswith("_translated"):
            group_dict[key] = _prepare_metadata(group_dict[key])

    return group_dict
