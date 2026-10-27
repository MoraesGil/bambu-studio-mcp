"""Explicit, measured identity for a selected Bambu Studio installation.

The source SHA is an operator assertion, not an authenticity attestation. Binary
and resource hashes bind that assertion to local assets; no update is installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.request import Request, urlopen

from ...domain.types.json import JsonObject, JsonValue, json_object


def _text(manifest: Mapping[str, JsonValue], key: str) -> str:
    field = manifest[key]
    if isinstance(field, str):
        return field
    raise ValueError(f'Engine manifest {key} must be a string')


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for child in sorted(root.rglob('*')):
        if child.is_file():
            digest.update(child.relative_to(root).as_posix().encode('utf-8'))
            digest.update(b'\x00')
            digest.update(bytes.fromhex(file_hash(child)))
    return digest.hexdigest()


def package_resources(executable: Path) -> Path:
    return _package_resources(executable, sys.platform, os.name)


def _package_resources(executable: Path, platform: str, operating_system: str) -> Path:
    """Resolve CLI::setup's package path, without relying on current cwd.

    BambuStudio.cpp:8302–8322: macOS Contents/Resources, Windows adjacent
    resources, portable Linux ../resources. Retain the previous deeper build
    layout as an explicit local compatibility path. Custom FHS is unsupported.
    """
    binary_directory = executable.parent
    if platform == 'darwin':
        return binary_directory.with_name('Resources')
    if operating_system == 'nt':
        return binary_directory / 'resources'
    portable = binary_directory.parent / 'resources'
    return portable if portable.exists() else binary_directory.parent.parent / 'resources'


class Engine:
    repository = 'bambulab/BambuStudio'

    @classmethod
    def bind(cls, executable: Path, source_commit: str, resources: Path) -> JsonObject:
        if not re.fullmatch('[0-9a-f]{40}', source_commit):
            raise ValueError('Specify the actual 40-character upstream master build commit')
        binary = executable.expanduser().resolve()
        assets = resources.expanduser().resolve()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise ValueError('Native Bambu Studio executable is unavailable')
        if not assets.is_dir():
            raise ValueError('The matching native resources directory is unavailable')
        if assets != package_resources(binary).resolve():
            raise ValueError('Declared resources do not match the native package layout; custom FHS builds are unsupported')
        return dict(repository=cls.repository, source_ref='master', source_commit=source_commit,
                    executable=str(binary), executable_sha256=file_hash(binary),
                    resources=str(assets), resources_sha256=tree_hash(assets),
                    provenance='operator-declared source commit; hashes measured locally')

    def __init__(self, manifest: Mapping[str, JsonValue]) -> None:
        if (manifest.get('repository'), manifest.get('source_ref')) != (self.repository, 'master'):
            raise ValueError('Only an official bambulab/BambuStudio master build can be bound')
        self.manifest = dict(manifest)
        self.executable = Path(_text(manifest, 'executable')).resolve()
        self.resources = Path(_text(manifest, 'resources')).resolve()
        signature = self._asset_signature()
        measured = type(self).bind(self.executable, _text(manifest, 'source_commit'), self.resources)
        changed = [field for field in ('executable_sha256', 'resources_sha256')
                   if measured[field] != manifest.get(field)]
        if changed:
            raise ValueError(f'Engine {changed[0]} changed; bind and validate the new build explicitly')
        if signature != self._asset_signature():
            raise ValueError('Engine assets changed during verification; bind a stable build explicitly')
        self._signature = signature
        self._upstream: str | None = None
        self._check = 'not_checked'

    def _asset_signature(self) -> tuple[tuple[str, int, int, int, int], ...]:
        def entry(path: Path) -> tuple[str, int, int, int, int]:
            current = path.stat()
            return str(path), current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns
        # File paths are part of the signature: additions and deletions matter.
        paths = [self.executable]
        paths.extend(sorted(path for path in self.resources.rglob('*') if path.is_file()))
        return tuple(map(entry, paths))

    def verify(self) -> None:
        try:
            if package_resources(self.executable).resolve() == self.resources:
                if self._asset_signature() == self._signature:
                    return
        except OSError:
            pass
        raise ValueError('Engine assets changed; bind and restart with the new build explicitly')

    def identity(self) -> JsonObject:
        fields = ('repository', 'source_ref', 'source_commit', 'executable_sha256', 'resources_sha256')
        return dict(zip(fields, map(self.manifest.__getitem__, fields)))

    def _remote_head(self) -> str:
        request = Request('https://api.github.com/repos/' + self.repository + '/commits/master',
                          headers={'User-Agent': 'bambu-studio-mcp', 'Accept': 'application/vnd.github+json'})
        with urlopen(request, timeout=3) as response:
            value: object = json.load(response)
        sha = json_object(value, 'Upstream response').get('sha')
        if isinstance(sha, str) and re.fullmatch('[0-9a-f]{40}', sha):
            return sha
        raise ValueError('Invalid upstream commit response')

    def status(self, check_remote: bool = False) -> JsonObject:
        self.verify()
        if check_remote:
            try:
                self._upstream = self._remote_head()
            except (OSError, ValueError, KeyError):
                self._check = 'unavailable'
            else:
                self._check = 'checked'
        available = None if self._upstream is None else self._upstream != self.manifest['source_commit']
        notices: list[JsonValue] = []
        if available:
            notices.append('A newer upstream master exists. Build/install and bind it explicitly to update the MCP engine; no automatic update was performed.')
        if self._check == 'unavailable':
            notices.append('Upstream update check is unavailable; the bound engine identity is unchanged.')
        return dict(self.identity(), upstream_commit=self._upstream, update_available=available,
                    update_check=self._check, notices=notices)
