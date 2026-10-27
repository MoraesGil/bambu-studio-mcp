"""Read-only enumeration of Bambu Studio packages; never execute candidates."""
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

from ...domain.types.json import JsonObject
from .engine import Engine, _package_resources


def _candidates() -> Iterator[Path]:
    if sys.platform == 'darwin':
        for directory in (Path('/Applications'), Path.home() / 'Applications'):
            yield directory / 'BambuStudio.app'
            yield directory / 'Bambu Studio.app'
    elif sys.platform == 'win32':
        yield Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Bambu Studio/bambu-studio.exe'
    else:
        yield Path('/opt/bambu-studio/bin/BambuStudio')
        yield Path('/usr/local/bin/bambu-studio')
    for command in ('bambu-studio', 'BambuStudio', 'bambu-studio.exe'):
        located = shutil.which(command)
        if located:
            yield Path(located)


def discover_installations(paths: Iterable[Path] | None = None) -> list[JsonObject]:
    found: dict[Path, JsonObject] = {}
    for path in _candidates() if paths is None else paths:
        expanded = path.expanduser()
        executable = expanded / 'Contents/MacOS/BambuStudio' if expanded.suffix == '.app' else expanded
        try:
            executable = executable.resolve(strict=True)
            resources = _package_resources(executable, sys.platform, 'nt' if sys.platform == 'win32' else os.name)
            if executable.is_file() and os.access(executable, os.X_OK) and resources.is_dir():
                found.setdefault(executable, dict(executable=str(executable), resources=str(resources.resolve()),
                                                  repository=Engine.repository, bound=False))
        except (OSError, RuntimeError):
            continue
    return list(found.values())
