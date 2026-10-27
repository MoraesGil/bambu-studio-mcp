# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Discover immutable catalog files and publish changes only inside drafts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from ...application.contracts.presets import PresetRecord
from ...domain.settings.normalization import _IDS, _NATIVE_TYPES, _TYPES
from ...domain.settings.values import text
from ...domain.types.json import JsonObject, decode_json, json_object


class FilesystemPresetRepository:
    def __init__(self, resources: str | Path, extra_dirs: list[Path], drafts: Path):
        resource_root = Path(resources).expanduser().resolve()
        self.system = resource_root / 'profiles' / 'BBL'
        self.extra_dirs = list(dict.fromkeys(path.expanduser().resolve() for path in map(Path, extra_dirs)))
        self.drafts = Path(drafts).expanduser().resolve()
        self._lock = threading.RLock()
        for immutable in (resource_root, *self.extra_dirs):
            if self.drafts.is_relative_to(immutable) or immutable.is_relative_to(self.drafts):
                raise ValueError('Drafts cannot overlap read-only resources or preset sources')

    @staticmethod
    def _kind(document: JsonObject, relative: Path) -> str | None:
        declared = document.get('type')
        if declared is not None:
            return _TYPES.get(declared) if isinstance(declared, str) else None
        identified = [kind for kind, key in _IDS.items() if key in document]
        if identified:
            return identified[0]
        return next((_TYPES[part] for part in reversed(relative.parts[:-1]) if part in _TYPES), None)

    def records(self) -> list[PresetRecord]:
        output: list[PresetRecord] = []
        visited: set[Path] = set()
        sources = [(root, 'user') for root in self.extra_dirs]
        sources.extend(((self.system, 'system'), (self.drafts, 'draft')))
        for directory, source in sources:
            if not directory.exists():
                continue
            if not directory.is_dir():
                raise ValueError(f'Preset source is not a directory: {directory}')
            for entry in sorted(directory.rglob('*.json')):
                canonical = entry.resolve()
                if canonical in visited:
                    continue
                visited.add(canonical)
                try:
                    payload = entry.read_bytes()
                    parsed = decode_json(payload.decode('utf-8-sig'))
                except (OSError, ValueError) as error:
                    raise ValueError(f'Cannot read preset JSON: {entry}') from error
                if not isinstance(parsed, dict) or not isinstance(parsed.get('name'), str):
                    continue
                kind = self._kind(parsed, entry.relative_to(directory))
                if kind is None:
                    continue
                writable = source == 'draft' and not entry.is_symlink() and canonical.is_relative_to(self.drafts)
                output.append(PresetRecord(name=text(parsed['name']), type=kind, source=source,
                    path=str(canonical), sha256=hashlib.sha256(payload).hexdigest(), raw=parsed, read_only=not writable))
        return output

    def _draft_target(self, location: str | Path) -> Path:
        candidate = Path(location)
        if (self.drafts.resolve() != self.drafts or candidate.is_symlink()
                or not candidate.resolve().is_relative_to(self.drafts) or candidate.resolve() == self.drafts):
            raise ValueError('Refusing symlink or read-only draft path')
        return candidate

    def draft_path(self, kind: str, name: str) -> str:
        filename = hashlib.sha256(name.encode('utf-8')).hexdigest() + '.json'
        return str(self._draft_target(self.drafts / _NATIVE_TYPES[kind] / filename))

    @staticmethod
    def _unchanged(path: Path, expected: str, action: str) -> None:
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError(f'Draft changed during {action}; read it again') from error
        if actual != expected:
            raise ValueError(f'Draft changed during {action}; read it again')

    def write(self, location: str, doc: JsonObject, previous: PresetRecord | None = None) -> None:
        payload = (json.dumps(json_object(doc), ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
        with self._lock:
            target = self._draft_target(location)
            if previous is not None:
                if previous['read_only'] or previous['source'] != 'draft' or Path(previous['path']) != target:
                    raise ValueError('Cannot modify a read-only preset; use a draft')
                self._unchanged(target, previous['sha256'], 'editing')
            elif target.exists():
                raise ValueError('Draft path already exists')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.preset-', delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._draft_target(target)
                if previous is None:
                    # Atomic no-clobber publication, including concurrent creators.
                    try:
                        os.link(temporary, target)
                    except FileExistsError as error:
                        raise ValueError('Draft path already exists') from error
                else:
                    self._unchanged(target, previous['sha256'], 'editing')
                    os.replace(temporary, target)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def remove(self, rec: PresetRecord, rollback_path: str | None = None, action: str = 'delete') -> None:
        with self._lock:
            if rec['source'] != 'draft' or rec['read_only']:
                raise ValueError('Cannot remove a read-only preset; only drafts are mutable')
            target = Path(rec['path'])
            rollback = self._draft_target(rollback_path) if rollback_path is not None else None
            try:
                self._draft_target(target)
                self._unchanged(target, rec['sha256'], action)
                target.unlink()
            except (ValueError, OSError):
                if rollback is not None and rollback != target:
                    self._draft_target(rollback).unlink(missing_ok=True)
                raise
