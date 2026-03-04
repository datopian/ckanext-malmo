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
]


def _translate_fields(
    context, data_dict, fields_to_translate=["title", "notes"], translate_resources=True
):
    html_convert = html2text.HTML2Text()
    default_lang = config.get("ckan.locale_default", "sv").split("_")[0]

    internal_results = {}

    for field_name in fields_to_translate:
        val = data_dict.get(field_name, "")
        internal_results[f"{field_name}_translated-{default_lang}"] = val

    languages_offered = config.get("ckan.locales_offered", ["en", "da_DK"])
    target_languages = [
        lang.split("_")[0] for lang in languages_offered if lang != default_lang
    ]

    for language_code in target_languages:
        payload_to_translate = {}

        for field_name in fields_to_translate:
            source_text = data_dict.get(field_name, "")

            if source_text and str(source_text).strip():
                if field_name in ["notes", "description"]:
                    payload_to_translate[field_name] = markdown.markdown(source_text)
                else:
                    payload_to_translate[field_name] = source_text

        if not payload_to_translate:
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

            outputs = translation_response.get("output", {})

            for field_name, translated_val in outputs.items():
                if translated_val:
                    translated_val = translated_val.strip("\n")

                    if field_name in ["notes", "description"]:
                        translated_val = html_convert.handle(translated_val)

                    internal_results[f"{field_name}_translated-{language_code}"] = (
                        translated_val
                    )

        except Exception as error:
            log.debug(f"Translation failed for {language_code}: {error}")

    for field_name in fields_to_translate:
        field_map = {}

        for key, value in internal_results.items():
            if key.startswith(f"{field_name}_translated-"):
                lang_suffix = key.split("-")[-1]

                field_map[lang_suffix] = value if value else ""

        data_dict[f"{field_name}_translated"] = json.dumps(field_map)

    if translate_resources and "resources" in data_dict:
        data_dict = _translate_resources(context, data_dict)

    if "extras" in data_dict:
        data_dict["extras"] = [
            e for e in data_dict["extras"] if e.get("key") != "display_name"
        ]

    return data_dict


def _group_translate(data_dict):
    data_dict = _format_translated_fields(data_dict)
    group_title = data_dict.get("title_translated")

    if group_title:
        data_dict["display_name_translated"] = group_title

    for key in [k for k in data_dict.keys() if k.endswith("_translated")]:
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
    if context.get("ignore_search_translations"):
        return next_action(context, data_dict)

    search_results = next_action(context, data_dict)

    internal_context = context.copy()
    internal_context.update({"ignore_auth": True, "ignore_search_translations": True})

    organizations_data = _get_action("organization_list")(
        internal_context, {"all_fields": True, "include_extras": True}
    )
    groups_data = _get_action("group_list")(
        internal_context, {"all_fields": True, "include_extras": True}
    )

    organization_lookup = {}

    for organization in organizations_data:
        translated_fields = {
            k: v
            for k, v in organization.items()
            if k.endswith("_translated") or k in ["name", "id"]
        }
        organization_lookup[organization["id"]] = translated_fields
        organization_lookup[organization["name"]] = translated_fields

    group_lookup = {}

    for group in groups_data:
        translated_fields = {
            k: v
            for k, v in group.items()
            if k.endswith("_translated") or k in ["name", "id"]
        }
        group_lookup[group["id"]] = translated_fields
        group_lookup[group["name"]] = translated_fields

    for package in search_results.get("results", []):
        organization_id = package.get("organization", {}).get("id")

        if organization_id in organization_lookup:
            package["organization"].update(organization_lookup[organization_id])

        for group in package.get("groups", []):
            group_id = group.get("id")

            if group_id in group_lookup:
                group.update(group_lookup[group_id])

    search_facets = search_results.get("search_facets", {})
    current_lang = context.get(
        "lang", config.get("ckan.locale_default", "sv").split("_")[0]
    )

    for facet_type in ["organization", "groups"]:
        if facet_type not in search_facets:
            continue

        facet_group = search_facets[facet_type]
        lookup_map = (
            organization_lookup if facet_type == "organization" else group_lookup
        )

        for facet_item in facet_group.get("items", []):
            entity_name = facet_item.get("name")

            if entity_name in lookup_map:
                facet_item.update(lookup_map[entity_name])

                title_translations = lookup_map[entity_name].get("title_translated", {})

                if isinstance(title_translations, dict) and title_translations.get(
                    current_lang
                ):
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
