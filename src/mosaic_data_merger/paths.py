"""Configuration-relative path handling and wildcard expansion."""

from __future__ import annotations

import glob
from pathlib import Path
from typing import Any

from .errors import ConfigError


def config_path(config: dict[str, Any], value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(config.get("_config_directory", Path.cwd())) / path


def expand_input_paths(config: dict[str, Any], value: str) -> list[Path]:
    """Resolve one input path or a wildcard pattern into sorted regular files."""
    pattern = config_path(config, value)
    pattern_text = str(pattern)
    if glob.has_magic(pattern_text):
        matches = sorted(
            (Path(match) for match in glob.glob(pattern_text, recursive=True) if Path(match).is_file()),
            key=lambda match: str(match),
        )
        if not matches:
            raise ConfigError(f"Input pattern matched no files: {value}")
        return matches
    if not pattern.is_file():
        raise ConfigError(f"Input file does not exist: {value}")
    return [pattern]
