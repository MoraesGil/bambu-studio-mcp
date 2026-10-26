"""Locate installed engines without executing, downloading or binding them."""
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path

from ...domain.types.json import JsonObject
from .engine import Engine


def discover_installations(paths: Iterable[Path] | None = None) -> list[JsonObject]:
    candidates: list[Path]
    if paths is not None:
        candidates = list(paths)
    elif sys.platform == 'darwin':
        candidates = [Path('/Applications/BambuStudio.app'), Path.home() / 'Applications/BambuStudio.app',
                      Path('/Applications/Bambu Studio.app')]
    elif sys.platform == 'win32':
        candidates = [Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Bambu Studio/bambu-studio.exe']
    else:
        candidates = [Path('/opt/bambu-studio/bin/BambuStudio'), Path('/usr/local/bin/bambu-studio')]
    if paths is None:
        for name in ('bambu-studio', 'BambuStudio', 'bambu-studio.exe'):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    results: list[JsonObject] = []
    seen: set[Path] = set()
    for candidate in candidates:
        binary = candidate / 'Contents/MacOS/BambuStudio' if candidate.suffix == '.app' else candidate
        binary = binary.expanduser().resolve()
        resources = (binary.parent.parent / 'Resources' if sys.platform == 'darwin' else
                     binary.parent / 'resources' if sys.platform == 'win32' else binary.parent.parent / 'resources')
        if sys.platform not in ('darwin', 'win32') and not resources.is_dir():
            resources = binary.parent.parent.parent / 'resources'
        if binary in seen or not binary.is_file() or not os.access(binary, os.X_OK) or not resources.is_dir():
            continue
        seen.add(binary)
        results.append({'executable': str(binary), 'resources': str(resources.resolve()),
                        'repository': Engine.repository, 'bound': False})
    return results
