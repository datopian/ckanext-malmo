from __future__ import annotations

import logging
from typing import Any

import flask
from flask_login import current_user

import ckan.logic as logic
import ckan.model as model
from ckan.plugins import toolkit

log = logging.getLogger(__name__)

dwg_preview_blueprint = flask.Blueprint("malmo_dwg_preview", __name__)

ValidationError = logic.ValidationError


@dwg_preview_blueprint.route("/api/3/action/convert_dwg", methods=["GET", "POST"])
def convert_dwg() -> flask.Response:
    """
    Binary endpoint that mirrors an action URL.

    CKAN 2.11 wraps normal action responses in JSON, so this blueprint exposes
    the same action name as a concrete Flask route and returns the preview bytes
    directly.
    """
    data_dict = _get_request_data()
    context = _build_context()

    try:
        payload = toolkit.get_action("convert_dwg")(context, data_dict)
    except ValidationError as err:
        return _validation_error_response(err)
    except Exception:
        log.exception("Unexpected error while generating DWG preview")
        return flask.jsonify(
            {
                "help": _help_url(),
                "success": False,
                "error": {
                    "__type": "Internal Server Error",
                    "message": "Internal Server Error",
                },
            }
        ), 500

    response = flask.Response(payload["content"], mimetype=payload["mimetype"])
    response.headers["Content-Disposition"] = f'inline; filename="{payload["filename"]}"'
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    return response


def get_blueprints():
    return [dwg_preview_blueprint]


def _build_context() -> dict[str, Any]:
    is_authenticated = bool(getattr(current_user, "is_authenticated", False))
    return {
        "model": model,
        "session": model.Session,
        "user": current_user.name if is_authenticated else "",
        "auth_user_obj": current_user if is_authenticated else None,
    }


def _get_request_data() -> dict[str, Any]:
    if flask.request.method == "GET":
        return flask.request.args.to_dict(flat=True)

    if flask.request.is_json:
        payload = flask.request.get_json(silent=True)
        if isinstance(payload, dict):
            return payload

    return flask.request.form.to_dict(flat=True)


def _validation_error_response(error: ValidationError) -> tuple[flask.Response, int]:
    error_dict = dict(error.error_dict)
    error_dict["__type"] = "Validation Error"
    return (
        flask.jsonify(
            {
                "help": _help_url(),
                "success": False,
                "error": error_dict,
            }
        ),
        409,
    )


def _help_url() -> str:
    return toolkit.url_for(
        "api.action",
        logic_function="help_show",
        ver=3,
        name="convert_dwg",
        _external=True,
    )
