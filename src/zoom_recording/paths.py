"""Home directory for the tool's own browser profile, and the git-repository check."""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "ZOOM_REC_HOME"
DEFAULT_HOME = "~/.zoom-recording"


class ConfigError(Exception):
    pass


def enclosing_git_repo(path: Path) -> Path | None:
    """Return the first ancestor (or the path itself) that contains a .git entry."""
    p = path.expanduser().resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def resolve_home(cli_value: str | None = None) -> Path:
    return Path(cli_value or os.environ.get(ENV_HOME) or DEFAULT_HOME).expanduser()
