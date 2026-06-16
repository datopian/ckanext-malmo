from __future__ import annotations

import logging
import os
import shutil
import subprocess

import ckan.logic as logic

from .config import PreviewConfig

log = logging.getLogger(__name__)

ValidationError = logic.ValidationError


def convert_dwg_to_dxf(source_path: str, working_dir: str, config: PreviewConfig) -> str:
    executable = _resolve_oda_executable(config.oda_executable)
    input_dir = os.path.join(working_dir, "oda-input")
    output_dir = os.path.join(working_dir, "oda-output")
    os.makedirs(input_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)

    input_name = os.path.basename(source_path)
    staged_input_path = os.path.join(input_dir, input_name)
    shutil.copyfile(source_path, staged_input_path)

    command = _build_oda_command(
        [
            executable,
            input_dir,
            output_dir,
            config.oda_output_version,
            "DXF",
            "0",
            "1",
            "*.dwg",
        ],
        xvfb_screen=config.xvfb_screen,
    )
    log.info("Running ODA File Converter command=%s", command)
    result = _run_subprocess(command, timeout=config.timeout)
    log.info(
        "ODA File Converter finished code=%s stdout=%s stderr=%s",
        result.returncode,
        _decode_subprocess_output(result.stdout) or "<empty>",
        _decode_subprocess_output(result.stderr) or "<empty>",
    )

    if result.returncode != 0:
        raise ValidationError({"conversion": [_format_conversion_error("DWG to DXF conversion failed", result.stderr)]})

    dxf_path = _find_generated_dxf(output_dir, input_name)
    if not dxf_path or not os.path.exists(dxf_path) or os.path.getsize(dxf_path) == 0:
        raise ValidationError({"conversion": ["DWG to DXF conversion did not produce a usable DXF file"]})

    log.info("Generated DXF path=%s bytes=%s", dxf_path, os.path.getsize(dxf_path))
    return dxf_path


def _resolve_oda_executable(configured_path: str) -> str:
    if os.path.isabs(configured_path):
        if os.path.exists(configured_path) and os.access(configured_path, os.X_OK):
            return configured_path
        raise ValidationError({"converter": [f'Configured ODA File Converter is not executable: "{configured_path}"']})

    resolved = shutil.which(configured_path)
    if resolved:
        return resolved

    raise ValidationError(
        {
            "converter": [
                'ODA File Converter is not installed. Configure `ckanext.malmo.dwg_preview_oda_executable` or add `ODAFileConverter` to PATH.'
            ]
        }
    )


def _build_oda_command(oda_arguments: list[str], xvfb_screen: str) -> list[str]:
    xvfb_run = shutil.which("xvfb-run")
    if not xvfb_run:
        return oda_arguments
    return [xvfb_run, "-a", "-s", xvfb_screen, *oda_arguments]


def _run_subprocess(command: list[str], timeout: int) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise ValidationError({"conversion": [f"Conversion exceeded the timeout of {timeout} seconds"]})
    except OSError as err:
        raise ValidationError({"conversion": [f"Conversion process failed to start: {err}"]})


def _find_generated_dxf(output_dir: str, input_name: str) -> str | None:
    expected_name = os.path.splitext(input_name)[0] + ".dxf"
    expected_path = os.path.join(output_dir, expected_name)
    if os.path.exists(expected_path):
        return expected_path

    for root, _dirs, files in os.walk(output_dir):
        for file_name in files:
            if file_name.lower().endswith(".dxf"):
                return os.path.join(root, file_name)
    return None


def _decode_subprocess_output(output: bytes | None) -> str:
    if not output:
        return ""
    return output.decode("utf-8", errors="replace").strip().splitlines()[0][:400]


def _format_conversion_error(prefix: str, stderr: bytes | None) -> str:
    decoded_stderr = _decode_subprocess_output(stderr)
    return f"{prefix}: {decoded_stderr}" if decoded_stderr else prefix
