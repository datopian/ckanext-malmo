from __future__ import annotations

import logging
import markdown
import html2text
import json
from typing import Any

from ckan.common import config, asbool, _
import ckan.logic.validators
import ckan.lib.datapreview
import ckan.lib.dictization
import ckan.logic as logic
import ckan.logic.action
import ckan.logic.schema
import ckan.lib.dictization.model_dictize as model_dictize
import ckan.lib.navl.dictization_functions
import ckan.plugins as plugins
import ckan.lib.plugins as lib_plugins
from ckan.types import ActionResult, Context, DataDict, Schema


log = logging.getLogger(__name__)

# Define some shortcuts
# Ensure they are module-private so that they don't get loaded as available
# actions in the action API.
_get_action = logic.get_action
_check_access = logic.check_access
_get_or_bust = logic.get_or_bust
ValidationError = logic.ValidationError
NotFound = logic.NotFound
NotAuthorized = logic.NotAuthorized

fresh_context = logic.fresh_context


log = logging.getLogger(__name__)


DATASET_FIELDS = [
    "title",
    "notes",
]
RESOURCE_FIELDS = [
    "name",
    "description",
]
GROUP_ORG_FIELDS = [
    "title",
    "description",
    "display_name",
]


def _translate_fields(
    context, data_dict, fields_to_translate=["title", "notes"], translate_resources=True
):
    html_convert = html2text.HTML2Text()
    default_lang = config.get("ckan.locale_default", "sv").split("_")[0]

    for field in fields_to_translate:
        val = data_dict.get(field, "")
        data_dict["{}_translated-{}".format(field, default_lang)] = val

    languages_offered = config.get("ckan.locales_offered", ["en", "da_DK"])
    languages = [
        lang.split("_")[0] for lang in languages_offered if lang != default_lang
    ]

    for language_code in languages:
        payload_to_translate = {}

        for field_name in fields_to_translate:
            raw_value = data_dict.get(field_name)

            if raw_value and str(raw_value).strip():
                if field_name in ["notes", "description"]:
                    payload_to_translate[field_name] = markdown.markdown(raw_value)
                else:
                    payload_to_translate[field_name] = raw_value

        if not payload_to_translate:
            for field_name in fields_to_translate:
                data_dict[f"{field_name}_translated-{language_code}"] = data_dict.get(field_name, "")
            continue

        try:
            translation_response = _get_action("translate")(
                context,
                {
                    "input": payload_to_translate,
                    "from": default_lang,
                    "to": language_code,
                },
            )

            translated_outputs = translation_response.get("output", {})

            for field_name in fields_to_translate:
                if field_name in translated_outputs:
                    translated_text = translated_outputs[field_name]

                    if translated_text:
                        translated_text = translated_text.strip("\n")

                    if field_name == "notes" and translated_text:
                        translated_text = html_convert.handle(translated_text)

                    data_dict[f"{field_name}_translated-{language_code}"] = translated_text
                else:
                    data_dict[f"{field_name}_translated-{language_code}"] = data_dict.get(field_name, "")

        except Exception as error:
            log.error(f"Translation service error for {language_code}: {error}")

            for field_name in fields_to_translate:
                data_dict[f"{field_name}_translated-{language_code}"] = data_dict.get(field_name, "")

    for field in fields_to_translate:
        existing_translations = data_dict.get("{}_translated".format(field))

        if isinstance(existing_translations, dict):
            for lang in existing_translations.keys():
                flat_key = "{}_translated-{}".format(field, lang)
                if flat_key in data_dict:
                    data_dict["{}_translated".format(field)][lang] = data_dict[flat_key]

    data_dict = _format_translated_fields(data_dict)

    if translate_resources and "resources" in data_dict:
        data_dict = _translate_resources(context, data_dict)

    return data_dict


def _group_translate(data_dict):
    data_dict = _format_translated_fields(data_dict)
    group_title = data_dict.get("title_translated")

    if group_title:
        data_dict["display_name_translated"] = group_title

    for field in GROUP_ORG_FIELDS:
        key = f"{field}_translated"
        translated_field = data_dict.get(key)

        if translated_field and isinstance(translated_field, str):
            try:
                data_dict[key] = json.loads(translated_field)
            except (json.JSONDecodeError, TypeError):
                log.error(f"Unable to decode JSON for {key}: {translated_field}")
                data_dict[key] = {}

    return data_dict


def _group_or_org_show(
    context: Context, data_dict: DataDict, is_org: bool = False
) -> dict[str, Any]:
    model = context["model"]
    id = _get_or_bust(data_dict, "id")

    group = model.Group.get(id)

    if asbool(data_dict.get("include_datasets", False)):
        packages_field = "datasets"
    elif asbool(data_dict.get("include_dataset_count", True)):
        packages_field = "dataset_count"
    else:
        packages_field = None

    try:
        include_tags = asbool(data_dict.get("include_tags", True))
        if config.get("ckan.auth.public_user_details"):
            include_users = asbool(data_dict.get("include_users", True))
        else:
            include_users = asbool(data_dict.get("include_users", False))
        include_groups = asbool(data_dict.get("include_groups", True))
        include_extras = asbool(data_dict.get("include_extras", True))
        include_followers = asbool(data_dict.get("include_followers", True))
        include_member_count = asbool(data_dict.get("include_member_count", False))
    except ValueError:
        raise logic.ValidationError({"message": _("Parameter is not an bool")})

    if group is None:
        raise NotFound
    if is_org and not group.is_organization:
        raise NotFound
    if not is_org and group.is_organization:
        raise NotFound

    context["group"] = group

    if is_org:
        _check_access("organization_show", context, data_dict)
    else:
        _check_access("group_show", context, data_dict)

    group_dict = model_dictize.group_dictize(
        group,
        context,
        packages_field=packages_field,
        include_tags=include_tags,
        include_extras=include_extras,
        include_groups=include_groups,
        include_users=include_users,
        include_member_count=include_member_count,
    )

    if is_org:
        plugin_type = plugins.IOrganizationController
    else:
        plugin_type = plugins.IGroupController

    for item in plugins.PluginImplementations(plugin_type):
        item.read(group)

    group_plugin = lib_plugins.lookup_group_plugin(group_dict["type"])

    if context.get("schema"):
        schema: Schema = context["schema"]
    elif hasattr(group_plugin, "show_group_schema"):
        schema: Schema = group_plugin.show_group_schema()
    # TODO: remove these fallback deprecated methods in the next release
    elif hasattr(group_plugin, "db_to_form_schema_options"):
        schema: Schema = getattr(group_plugin, "db_to_form_schema_options")(
            {"type": "show", "api": "api_version" in context, "context": context}
        )
    else:
        schema: Schema = group_plugin.db_to_form_schema()

    if include_followers:
        context = fresh_context(context)
        group_dict["num_followers"] = _get_action("group_follower_count")(
            context, {"id": group_dict["id"]}
        )
    else:
        group_dict["num_followers"] = 0

    group_dict, _errors = lib_plugins.plugin_validate(
        group_plugin,
        context,
        group_dict,
        schema,
        "organization_show" if is_org else "group_show",
    )
    return group_dict


def _format_translated_fields(data_dict):
    fields_to_format = {
        k.split("_translated-")[0] for k in data_dict.keys() if "_translated-" in k
    }

    for field in fields_to_format:
        translated_map = {}
        matched_keys = [
            k for k in data_dict.keys() if k.startswith(f"{field}_translated-")
        ]

        for key in matched_keys:
            lang = key.split("-")[-1]
            translated_map[lang] = data_dict.pop(key)

        if translated_map:
            data_dict[f"{field}_translated"] = json.dumps(translated_map)

    return data_dict


def _translate_resources(context, data_dict):
    if "resources" in data_dict and isinstance(data_dict["resources"], list):
        for resource in data_dict["resources"]:
            _translate_fields(
                context, resource, RESOURCE_FIELDS, translate_resources=False
            )

    return data_dict


# Dataset Actions


@logic.chained_action
def package_update(next_action, context: Context, data_dict: DataDict) -> ActionResult:
    translated_data_dict = _translate_fields(context, data_dict, DATASET_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def package_create(next_action, context: Context, data_dict: DataDict) -> ActionResult:
    translated_data_dict = _translate_fields(context, data_dict, DATASET_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def package_patch(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, DATASET_FIELDS)

    return next_action(context, translated_data_dict)


@logic.side_effect_free
@logic.chained_action
def package_show(next_action, context, data_dict):
    package = next_action(context, data_dict)
    org_id = package.get("organization", {}).get("id")

    if org_id:
        org_dict = _get_action("organization_show")(context, {"id": org_id})
        package["organization"].update(
            {k: v for k, v in org_dict.items() if k.endswith("_translated")}
        )

    groups = package.get("groups", [])

    for group in groups:
        group_id = group.get("id")
        if group_id:
            group_dict = _get_action("group_show")(context, {"id": group_id})
            group.update(
                {k: v for k, v in group_dict.items() if k.endswith("_translated")}
            )

    return package


@logic.side_effect_free
@logic.chained_action
def package_search(next_action, context, data_dict):
    search_results = next_action(context, data_dict)
    search_facets = search_results.get("search_facets", {})
    translated_metadata_cache = {}

    for facet_type in ["organization", "groups"]:
        if facet_type not in search_facets:
            continue

        facet_group = search_facets[facet_type]
        for facet_item in facet_group.get("items", []):
            entity_id = facet_item.get("name")

            if entity_id not in translated_metadata_cache:
                try:
                    action_name = (
                        "organization_show"
                        if facet_type == "organization"
                        else "group_show"
                    )
                    entity_details = _get_action(action_name)(
                        context, {"id": entity_id}
                    )

                    translated_metadata_cache[entity_id] = {
                        key: value
                        for key, value in entity_details.items()
                        if key.endswith("_translated")
                    }
                except (NotAuthorized, NotFound):
                    translated_metadata_cache[entity_id] = {}

            if translated_metadata_cache[entity_id]:
                facet_item.update(translated_metadata_cache[entity_id])

                current_lang = context.get(
                    "lang", config.get("ckan.locale_default", "sv").split("_")[0]
                )
                title_translations = translated_metadata_cache[entity_id].get(
                    "title_translated"
                )
                if title_translations and current_lang in title_translations:
                    facet_item["display_name"] = title_translations[current_lang]

    return search_results


# Resource Actions


@logic.chained_action
def resource_create(next_action, context: Context, data_dict: DataDict) -> ActionResult:
    translated_data_dict = _translate_fields(context, data_dict, RESOURCE_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def resource_update(next_action, context: Context, data_dict: DataDict) -> ActionResult:
    translated_data_dict = _translate_fields(context, data_dict, RESOURCE_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def resource_patch(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, RESOURCE_FIELDS)

    return next_action(context, translated_data_dict)


# Organization Actions


@logic.chained_action
def organization_create(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, GROUP_ORG_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def organization_update(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, GROUP_ORG_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def organization_patch(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, GROUP_ORG_FIELDS)

    return next_action(context, translated_data_dict)


@logic.side_effect_free
def organization_show(context, data_dict):
    data_dict = _group_or_org_show(context, data_dict, is_org=True)
    translated_data_dict = _group_translate(data_dict)

    return translated_data_dict


# Group Actions


@logic.chained_action
def group_create(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, GROUP_ORG_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def group_update(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, GROUP_ORG_FIELDS)

    return next_action(context, translated_data_dict)


@logic.chained_action
def group_patch(next_action, context, data_dict):
    translated_data_dict = _translate_fields(context, data_dict, GROUP_ORG_FIELDS)

    return next_action(context, translated_data_dict)


@logic.side_effect_free
def group_show(context, data_dict):
    group_dict = _group_or_org_show(context, data_dict, is_org=False)
    translated_data_dict = _group_translate(group_dict)

    return translated_data_dict


@logic.side_effect_free
@logic.chained_action
def group_list(next_action, context, data_dict):
    group_list = next_action(context, data_dict)
    all_fields = data_dict.get("all_fields", False)

    if all_fields:
        for group in group_list:
            group_id = group.get("id")
            if group_id:
                try:
                    group_details = _get_action("group_show")(context, {"id": group_id})
                    group.update(
                        {
                            k: v
                            for k, v in group_details.items()
                            if k.endswith("_translated")
                        }
                    )
                except (NotAuthorized, NotFound):
                    continue

    return group_list
