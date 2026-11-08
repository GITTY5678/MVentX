"""GRUB Boot Menu Generator for MVentX.

Translates mventx.json configuration models into native GRUB 2 script syntax
(mventx_menu.cfg) safely, deterministically, and atomically.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.boot_config.recipes import (
    detect_recipe_from_hints,
    generate_loopback_boot_commands,
    get_recipe,
)
from core.boot_config.grub_capabilities import GRUBCapabilityRegistry


class ConfigValidationError(Exception):
    """Raised when the configuration data fails structural or semantic validation."""


class ConfigSecurityError(ConfigValidationError):
    """Raised when configuration values contain potentially unsafe or injection characters."""


# Regular expressions for strict validation of identifiers
_ENV_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")
_BOOT_METHOD_REGEX = re.compile(r"^[a-zA-Z0-9_]+$")
_VERSION_REGEX = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")

# Characters and patterns strictly forbidden in display names, ISO paths, or arguments to prevent GRUB script injection
_DANGEROUS_CHARS_REGEX = re.compile(r"[\r\n\x00-\x1f;{}`]")
_SHELL_EXPANSION_REGEX = re.compile(r"\$\(.*\)|`.*`|\$\{.*\}")


def validate_display_name(raw_name: str) -> str:
    """Validate that display name meets length and security requirements without escaping."""
    if not isinstance(raw_name, str):
        raise ConfigValidationError(f"display_name must be a string, got {type(raw_name).__name__}")

    stripped = raw_name.strip()
    if not stripped:
        raise ConfigValidationError("display_name cannot be empty or whitespace only")
    if len(stripped) > 100:
        raise ConfigValidationError(f"display_name exceeds 100 characters limit ({len(stripped)})")

    # Reject dangerous control characters, newlines, semicolons, braces, and backticks
    if _DANGEROUS_CHARS_REGEX.search(stripped):
        raise ConfigSecurityError(
            f"display_name contains forbidden characters (newlines, semicolons, or braces): {stripped!r}"
        )

    # Reject shell substitution patterns like $(cmd) or `cmd`
    if _SHELL_EXPANSION_REGEX.search(stripped):
        raise ConfigSecurityError(
            f"display_name contains forbidden command substitution patterns: {stripped!r}"
        )

    return stripped


def sanitize_display_name(raw_name: str) -> str:
    """Validate and safely escape a user-defined display name for GRUB menu entries.

    Raises:
        ConfigSecurityError: If display name contains forbidden control characters,
                             braces, semicolons, or execution syntax.
        ConfigValidationError: If display name is empty or too long.
    """
    validated = validate_display_name(raw_name)
    # Escape quotes, backslashes, and standalone dollar signs for GRUB string literal
    return validated.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$")


def sanitize_id(raw_id: str) -> str:
    """Validate environment identifier."""
    if not isinstance(raw_id, str):
        raise ConfigValidationError(f"id must be a string, got {type(raw_id).__name__}")
    if not _ENV_ID_REGEX.match(raw_id):
        raise ConfigSecurityError(
            f"id must contain only alphanumeric characters, dashes, or underscores: {raw_id!r}"
        )
    return raw_id


def sanitize_path(raw_path: Optional[str]) -> Optional[str]:
    """Validate a relative file or device path for ISO or chainloading target."""
    if raw_path is None:
        return None
    if not isinstance(raw_path, str):
        raise ConfigValidationError(f"path must be a string, got {type(raw_path).__name__}")

    stripped = raw_path.strip()
    if not stripped:
        return None

    if _DANGEROUS_CHARS_REGEX.search(stripped):
        raise ConfigSecurityError(f"path contains forbidden characters: {stripped!r}")

    if _SHELL_EXPANSION_REGEX.search(stripped):
        raise ConfigSecurityError(f"path contains forbidden command substitution: {stripped!r}")

    # Prevent directory traversal attacks
    normalized_parts = stripped.replace("\\", "/").split("/")
    if ".." in normalized_parts:
        raise ConfigSecurityError(f"path cannot contain directory traversal '..': {stripped!r}")

    # Escape double quotes and dollar signs
    return stripped.replace("\\", "/").replace('"', '\\"').replace("$", "\\$")


def sanitize_custom_args(raw_args: Optional[str]) -> Optional[str]:
    """Validate custom boot arguments."""
    if raw_args is None:
        return None
    if not isinstance(raw_args, str):
        raise ConfigValidationError(f"custom_args must be a string, got {type(raw_args).__name__}")

    stripped = raw_args.strip()
    if not stripped:
        return None

    if _DANGEROUS_CHARS_REGEX.search(stripped):
        raise ConfigSecurityError(f"custom_args contains forbidden characters: {stripped!r}")

    if _SHELL_EXPANSION_REGEX.search(stripped) or "$" in stripped:
        raise ConfigSecurityError(f"custom_args cannot contain variable or command expansion: {stripped!r}")

    return stripped.replace('"', '\\"')


# Recognized standard boot methods
KNOWN_BOOT_METHODS = {
    "casper",
    "linux_casper",
    "archiso",
    "linux_archiso",
    "arch_loopback",
    "live_boot",
    "linux_live_boot",
    "debian_live_loopback",
    "dracut",
    "linux_dracut",
    "opensuse",
    "mx_linux",
    "alpine",
    "systemrescue",
    "linux_generic_loopback",
    "chainload_efi",
    "windows_extracted_media",
    "windows_wimboot",
    "windows_host_chainload",
    "iso_loopback",
    "generic_uefi",
}


class GrubMenuGenerator:
    """Renders GRUB 2 menuentry blocks based on registered MVentX environments."""

    def __init__(self, templates_dir: Optional[Path] = None, data_dir: Optional[Path] = None) -> None:
        self.templates_dir = templates_dir
        self.data_dir = Path(data_dir).resolve() if data_dir else None

    def validate_config(self, config_data: Dict[str, Any]) -> None:
        """Validate top-level and environment-level configuration fields."""
        if not isinstance(config_data, dict):
            raise ConfigValidationError("Configuration root must be a JSON object")

        if "version" not in config_data:
            raise ConfigValidationError("Missing required configuration field: 'version'")
        if not isinstance(config_data["version"], str) or not _VERSION_REGEX.match(config_data["version"]):
            raise ConfigValidationError(f"Invalid version string: {config_data.get('version')!r}")

        if "meta" not in config_data or not isinstance(config_data["meta"], dict):
            raise ConfigValidationError("Missing or invalid 'meta' object in configuration")

        meta = config_data["meta"]
        if "title" in meta and not isinstance(meta["title"], str):
            raise ConfigValidationError("meta.title must be a string")
        if "timeout" in meta and (not isinstance(meta["timeout"], int) or meta["timeout"] < 0):
            raise ConfigValidationError("meta.timeout must be a non-negative integer")

        if "environments" not in config_data:
            raise ConfigValidationError("Missing required configuration field: 'environments'")
        if not isinstance(config_data["environments"], list):
            raise ConfigValidationError("'environments' must be a list")

        # Validate each environment entry
        seen_ids = set()
        for idx, entry in enumerate(config_data["environments"]):
            if not isinstance(entry, dict):
                raise ConfigValidationError(f"Environment entry at index {idx} must be a dictionary")

            for required_key in ("id", "display_name", "type", "boot_method", "enabled", "order"):
                if required_key not in entry:
                    raise ConfigValidationError(
                        f"Environment at index {idx} missing required field '{required_key}'"
                    )

            env_id = sanitize_id(entry["id"])
            if env_id in seen_ids:
                raise ConfigValidationError(f"Duplicate environment ID detected: {env_id!r}")
            seen_ids.add(env_id)

            sanitize_display_name(entry["display_name"])

            env_type = entry["type"]
            if env_type not in ("iso", "chainload", "windows"):
                raise ConfigValidationError(
                    f"Environment '{env_id}' has invalid type '{env_type}'; must be 'iso', 'chainload', or 'windows'"
                )

            boot_method = entry["boot_method"]
            if not isinstance(boot_method, str) or not _BOOT_METHOD_REGEX.match(boot_method):
                raise ConfigSecurityError(
                    f"Environment '{env_id}' has invalid boot_method: {boot_method!r}"
                )

            if not isinstance(entry["enabled"], bool):
                raise ConfigValidationError(f"Environment '{env_id}' field 'enabled' must be boolean")

            if not isinstance(entry["order"], int) or entry["order"] < 1:
                raise ConfigValidationError(f"Environment '{env_id}' field 'order' must be an integer >= 1")

            if env_type == "iso":
                iso_path = entry.get("iso_path")
                if not iso_path:
                    raise ConfigValidationError(f"Environment '{env_id}' of type 'iso' requires non-empty 'iso_path'")
                sanitize_path(iso_path)
            elif env_type == "chainload":
                target_path = entry.get("target_path")
                if target_path:
                    sanitize_path(target_path)

            if "custom_args" in entry and entry["custom_args"] is not None:
                sanitize_custom_args(entry["custom_args"])

            if "overlay_path" in entry and entry["overlay_path"] is not None:
                sanitize_path(entry["overlay_path"])

    def generate_menu_script(self, config_data: Dict[str, Any]) -> str:
        """Generate full GRUB 2 configuration script deterministically from configuration.

        Only environments with enabled=True will have menuentry blocks created.
        If no environments are enabled or present, a clean fallback entry is generated.
        """
        self.validate_config(config_data)

        meta = config_data.get("meta", {})
        title = meta.get("title", "MVentX Multiboot System")
        safe_title = sanitize_display_name(title)
        timeout = meta.get("timeout", 10)
        default_id = meta.get("default_entry_id")

        environments: List[Dict[str, Any]] = config_data.get("environments", [])

        # Filter only enabled environments
        enabled_entries = [e for e in environments if e.get("enabled") is True]

        # Sort deterministically: primary by order, secondary by id
        enabled_entries.sort(key=lambda x: (x.get("order", 1), x.get("id", "")))

        lines: List[str] = [
            "# ==============================================================================",
            "# MVentX Dynamic Menu Configuration (mventx_menu.cfg)",
            "# Automatically generated by MVentX Boot Configuration Generator.",
            "# DO NOT EDIT THIS FILE DIRECTLY - Changes will be overwritten.",
            "# ==============================================================================",
            "",
            f'set mventx_menu_title="{safe_title}"',
            f"set timeout={timeout}",
            "",
        ]

        if default_id:
            safe_default_id = sanitize_id(default_id)
            lines.append(f'set default="{safe_default_id}"')
            lines.append("")

        if not enabled_entries:
            lines.extend([
                "# ------------------------------------------------------------------------------",
                "# Fallback: No environments registered or enabled",
                "# ------------------------------------------------------------------------------",
                'menuentry "No environments registered or enabled" --id "mventx-empty" {',
                '    echo ""',
                '    echo "=========================================================="',
                '    echo "  MVentX Boot Menu: No active boot environments found.    "',
                '    echo "=========================================================="',
                '    echo ""',
                '    echo "  No environments are currently enabled in mventx.json."',
                '    echo "  Please use the MVentX Manager to register or enable ISOs."',
                '    echo ""',
                '    echo "  Press Enter or ESC to return to main menu."',
                "    sleep 5",
                "}",
                "",
            ])
        else:
            lines.extend([
                "# ------------------------------------------------------------------------------",
                "# Registered and Enabled Environments",
                "# ------------------------------------------------------------------------------",
                "",
            ])

            for entry in enabled_entries:
                env_id = sanitize_id(entry["id"])
                display_name = sanitize_display_name(entry["display_name"])
                env_type = entry["type"]
                boot_method = entry["boot_method"]

                lines.append(f'menuentry "{display_name}" --id "{env_id}" {{')
                lines.append('    echo ""')
                lines.append('    echo "=========================================================="')
                lines.append(f'    echo "  Environment: {display_name}"')
                lines.append('    echo "=========================================================="')
                lines.append(f'    echo "  ID:          {env_id}"')
                lines.append(f'    echo "  Type:        {env_type}"')
                lines.append(f'    echo "  Boot Method: {boot_method}"')

                if boot_method not in KNOWN_BOOT_METHODS:
                    lines.append(f'    echo "  [WARNING] Unrecognized boot method: {boot_method}"')

                if env_type == "iso":
                    iso_path = sanitize_path(entry.get("iso_path"))
                    lines.append(f'    echo "  ISO Path:    {iso_path}"')
                    if self.data_dir and iso_path:
                        full_iso = self.data_dir / iso_path
                        if not full_iso.is_file():
                            lines.append(f'    echo "  [WARNING] Registered ISO file missing from storage: {iso_path}"')
                elif env_type == "chainload":
                    target_path = sanitize_path(entry.get("target_path"))
                    if target_path:
                        lines.append(f'    echo "  Target:      {target_path}"')

                if entry.get("custom_args"):
                    custom_args = sanitize_custom_args(entry.get("custom_args"))
                    lines.append(f'    echo "  Arguments:   {custom_args}"')

                lines.append('    echo "  Note: Registration does not imply verified kernel bootability."')
                lines.append('    echo "=========================================================="')

                if env_type == "iso":
                    recipe_key = entry.get("recipe") or boot_method
                    recipe = get_recipe(recipe_key)
                    k_override = sanitize_path(entry.get("kernel_path"))
                    i_override = sanitize_path(entry.get("initrd_path"))
                    c_args = sanitize_custom_args(entry.get("custom_args"))
                    iso_clean = sanitize_path(entry.get("iso_path"))
                    overlay_clean = sanitize_path(entry.get("overlay_path")) if entry.get("overlay_path") else None
                    if iso_clean:
                        boot_cmds = generate_loopback_boot_commands(
                            recipe=recipe,
                            iso_path=iso_clean,
                            kernel_path_override=k_override,
                            initrd_path_override=i_override,
                            custom_args=c_args,
                            overlay_path=overlay_clean,
                        )
                        lines.extend(boot_cmds)
                    else:
                        lines.extend([
                            '    echo "Error: Missing ISO path for environment."',
                            '    sleep 5',
                        ])
                elif env_type == "chainload":
                    target_path = sanitize_path(entry.get("target_path"))
                    if target_path:
                        lines.extend([
                            f'    chainloader "{target_path}"',
                            '    boot',
                        ])
                    else:
                        lines.extend([
                            '    echo "Error: Missing target path for chainloading."',
                            '    sleep 5',
                        ])
                elif env_type == "windows" or boot_method.startswith("windows_"):
                    from core.boot_config.windows_boot_generator import generate_windows_boot_commands
                    from core.boot_config.windows_boot_strategy import WindowsBootStrategy

                    if boot_method == "windows_host_chainload":
                        strat = WindowsBootStrategy.HOST_CHAINLOAD
                    elif boot_method == "windows_wimboot":
                        strat = WindowsBootStrategy.WIMBOOT_PE
                    else:
                        strat = WindowsBootStrategy.EXTRACTED_MEDIA

                    bl_path = sanitize_path(entry.get("bootloader_path")) or "/efi/boot/bootx64.efi"
                    staged_sub = sanitize_path(entry.get("staged_subfolder"))
                    win_cmds = generate_windows_boot_commands(
                        strategy=strat,
                        bootloader_path=bl_path,
                        staged_subfolder=staged_sub,
                        custom_name=display_name,
                    )
                    lines.extend(win_cmds)
                else:
                    lines.extend([
                        '    echo "Unsupported environment type."',
                        '    sleep 5',
                    ])

                lines.extend([
                    "}",
                    "",
                ])

        # System Utilities Submenu
        registry = GRUBCapabilityRegistry()
        utils_script = registry.generate_system_utilities_script(
            supported_utilities=["fwsetup", "reboot", "halt", "exit"],
            include_heading=True,
        )
        lines.extend(utils_script)

        # Normalize every emitted line to guarantee single physical LF lines without CR
        normalized_lines: List[str] = []
        for raw_line in lines:
            for sub_line in str(raw_line).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                normalized_lines.append(sub_line)

        return "\n".join(normalized_lines)

    @classmethod
    def validate_grub_script_syntax(cls, script: str) -> List[str]:
        """Validate physical line structure, command syntax, and block balance of a GRUB script.

        Checks:
        1. No carriage return ('\\r') characters exist anywhere in the script.
        2. Balanced '{' and '}' blocks across menuentry/submenu definitions.
        3. Balanced 'if ...; then' / 'elif ...; then' / 'else' / 'fi' conditionals.
        4. Every 'initrd', 'linux', 'loopback', and 'search' command is complete on one physical line.
        5. Every '[ -f ... ]' or '[ ! -f ... ]' test is complete on one physical line with ']; then'.
        6. If '${mventx_boot_dev}' is referenced in an 'initrd' or '[ -f' test, 'search --no-floppy --label --set=mventx_boot_dev MVX_BOOT' appears earlier in the block.

        Returns:
            List of syntax error messages (empty list if valid).
        """
        errors: List[str] = []
        if "\r" in script:
            errors.append("Script contains Windows CR ('\\r') characters; GRUB 2 requires pure LF ('\\n') line endings.")

        raw_lines = script.split("\n")
        brace_depth = 0
        if_depth = 0
        seen_boot_dev_search_in_entry = False

        for line_no, raw_line in enumerate(raw_lines, start=1):
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue

            if stripped.startswith("menuentry ") or stripped.startswith("submenu "):
                if not stripped.endswith("{"):
                    errors.append(f"Line {line_no}: menuentry/submenu header does not end with '{{': {stripped!r}")
                brace_depth += 1
                if stripped.startswith("menuentry "):
                    seen_boot_dev_search_in_entry = False
                continue

            if stripped == "}":
                brace_depth -= 1
                if brace_depth < 0:
                    errors.append(f"Line {line_no}: unmatched closing brace '}}'")
                continue

            if "search --no-floppy --label --set=mventx_boot_dev MVX_BOOT" in stripped:
                seen_boot_dev_search_in_entry = True

            if stripped.startswith("if ") or stripped.startswith("elif "):
                if not stripped.endswith("; then"):
                    errors.append(f"Line {line_no}: conditional '{stripped}' must end with '; then' on the same physical line")
                if "[" in stripped and "]" not in stripped:
                    errors.append(f"Line {line_no}: test bracket '[' is split across physical lines: {stripped!r}")
                if stripped.startswith("if "):
                    if_depth += 1
                if "${mventx_boot_dev}" in stripped and "-f" in stripped and not seen_boot_dev_search_in_entry:
                    errors.append(
                        f"Line {line_no}: '${{mventx_boot_dev}}' used in file test before MVX_BOOT label search initialization"
                    )
                continue

            if stripped == "fi":
                if_depth -= 1
                if if_depth < 0:
                    errors.append(f"Line {line_no}: unmatched 'fi'")
                continue

            if stripped.startswith("initrd "):
                tokens = stripped.split()
                if len(tokens) < 2:
                    errors.append(f"Line {line_no}: 'initrd' command missing initramfs path argument")
                if "${mventx_boot_dev}" in stripped and not seen_boot_dev_search_in_entry:
                    errors.append(
                        f"Line {line_no}: '${{mventx_boot_dev}}' used in initrd command before MVX_BOOT label search initialization"
                    )

            # Detect orphaned path fragments that look like a wrapped initrd or test line
            if stripped.startswith("(${mventx_boot_dev})/") or stripped.startswith("(loop0)/"):
                errors.append(
                    f"Line {line_no}: orphaned device path on its own physical line (split command): {stripped!r}"
                )

        if brace_depth != 0:
            errors.append(f"Unbalanced '{{' / '}}' braces in script (net depth: {brace_depth})")
        if if_depth != 0:
            errors.append(f"Unbalanced 'if' / 'fi' blocks in script (net depth: {if_depth})")

        return errors

    def write_menu_file(self, config_data: Dict[str, Any], output_path: Path) -> None:
        """Generate and atomically write mventx_menu.cfg to the specified output path.

        Ensures atomic replacement and strict Unix LF ('\\n') line endings so that GRUB 2
        never encounters CR ('\\r') characters or partially written files.
        """
        content = self.generate_menu_script(config_data)
        syntax_errors = self.validate_grub_script_syntax(content)
        if syntax_errors:
            raise ConfigValidationError(
                f"Generated GRUB menu failed syntax validation: {'; '.join(syntax_errors)}"
            )

        output_path = Path(output_path).resolve()
        output_dir = output_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        # Write to temporary file in the same directory with explicit LF ('\n') line endings
        temp_file = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=output_dir,
                prefix=f".{output_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                temp_file = Path(f.name)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace (atomic on POSIX and supported on Windows with replace)
            temp_file.replace(output_path)
            temp_file = None
        finally:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                except OSError:
                    pass

    @classmethod
    def read_config_file(cls, config_path: Path) -> Dict[str, Any]:
        """Read and parse a JSON configuration file."""
        config_path = Path(config_path).resolve()
        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ConfigValidationError(f"Invalid JSON syntax in {config_path}: {exc}") from exc

        return data
