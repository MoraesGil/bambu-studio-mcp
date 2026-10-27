# Portions derived from Max Ellis, orcaslicer-mcp@ccfacb43c2fd5a23973bfdabce89c74b2f6933fe.
# SPDX-License-Identifier: AGPL-3.0-only
"""Catalog queries and draft commands over injected repository capabilities."""
from __future__ import annotations

import copy
import threading
from collections import Counter

from ...domain.settings.normalization import (
    _IDS, _IDENTITY, _METADATA, _NATIVE_TYPES, _gcode, _placeholder, _redact,
    _transport, _type, normalize_filament_profile, validate_changes,
)
from ...domain.settings.values import record, text
from ...domain.types.json import JsonObject, JsonValue, json_object
from ..contracts.presets import EngineIdentity, PresetRecord, PresetRepository

def _descriptor(preset: PresetRecord) -> JsonObject:
    return {'name': preset['name'], 'type': preset['type'], 'source': preset['source'],
            'path': preset['path'], 'sha256': preset['sha256'], 'read_only': preset['read_only']}


class PresetService:
    def __init__(self, engine: EngineIdentity, repository: PresetRepository, schema: JsonObject):
        self.engine = engine
        self.repository = repository
        self.schema = schema
        self.settings = {key: json_object(schema[key]) for key in schema if not key.startswith('_')}
        self._lock = threading.RLock()

    def _schema_status(self) -> JsonObject:
        metadata = record(self.schema['_meta'])
        native = self.engine.identity()['source_commit']
        source = metadata['source_commit']
        return {'source_commit': source, 'engine_commit': native, 'matches_engine': native == source,
                'scope': 'Source metadata and file inheritance; not runtime defaults or final region configuration'}

    def _records(self) -> list[PresetRecord]:
        self.engine.verify()
        snapshot = self.repository.records()
        self.engine.verify()
        return snapshot

    @staticmethod
    def _find(records: list[PresetRecord], kind: str, name: str) -> PresetRecord:
        matches = [item for item in records if (item['type'], item['name']) == (kind, name)]
        if not matches:
            raise ValueError(f'Unknown preset or missing inheritance parent: {kind}/{name}')
        if len(matches) != 1:
            locations = ', '.join(item['path'] for item in matches)
            raise ValueError(f'Ambiguous preset name {name}: {locations}')
        return matches[0]

    def _resolve(self, records: list[PresetRecord], rec: PresetRecord,
                 trail: tuple[tuple[str, str], ...] = ()) -> tuple[JsonObject, list[JsonObject], JsonObject]:
        """Traverse first, then fold root-to-leaf, without recursion depth limits."""
        visited = set(trail)
        lineage: list[PresetRecord] = []
        node = rec
        while True:
            identity = node['type'], node['name']
            if identity in visited:
                raise ValueError(f"Preset inheritance cycle at {node['name']}")
            visited.add(identity)
            lineage.append(node)
            parent = node['raw'].get('inherits', '')
            if not isinstance(parent, str):
                raise ValueError(f"Invalid inheritance parent for {node['name']}")
            if parent == '':
                break
            node = self._find(records, node['type'], parent)
        configuration: JsonObject = {}
        metadata: JsonObject = {}
        chain: list[JsonObject] = []
        for ancestor in reversed(lineage):
            for key, value in ancestor['raw'].items():
                if key not in _METADATA:
                    configuration[key] = copy.deepcopy(value)
            if ancestor['type'] == 'filament' and 'filament_id' in ancestor['raw']:
                native_id = ancestor['raw']['filament_id']
                if not isinstance(native_id, str):
                    raise ValueError('Native filament_id metadata must be a string')
                metadata['filament_id'] = native_id
            chain.append(_descriptor(ancestor))
        return configuration, chain, metadata

    def _value(self, records: list[PresetRecord], rec: PresetRecord) -> JsonObject:
        configuration, inheritance, metadata = self._resolve(records, rec)
        key = _IDS[rec['type']]
        if key not in rec['raw']:
            configuration[key] = [rec['name']] if rec['type'] == 'filament' else rec['name']
        payload = _descriptor(rec)
        payload.update({'config': configuration, 'raw_config': copy.deepcopy(rec['raw']), 'effective_metadata': metadata,
                        'inherits': rec['raw'].get('inherits', ''), 'inheritance': [item for item in inheritance],
                        'engine': self.engine.identity(), 'schema': self._schema_status(),
                        'unknown_keys': [key for key in sorted(configuration.keys() - self.settings.keys())]})
        return record(_redact(payload))

    def list_presets(self, type: str | None = None, include_system: bool = False) -> JsonObject:
        kind = None if type is None else _type(type)
        records = self._records()
        counts = Counter((item['type'], item['name']) for item in records)
        presets: list[JsonValue] = []
        for item in records:
            if kind is not None and item['type'] != kind:
                continue
            if item['source'] == 'system' and not include_system:
                continue
            summary = _descriptor(item)
            summary.update({'inherits': item['raw'].get('inherits', ''),
                            'ambiguous': counts[(item['type'], item['name'])] > 1})
            presets.append(summary)
        return {'presets': presets, 'engine': self.engine.identity(), 'schema': self._schema_status()}

    def get_preset_config(self, type: str, name: str) -> JsonObject:
        snapshot = self._records()
        return self._value(snapshot, self._find(snapshot, _type(type), name))

    def _group_keys(self, records: list[PresetRecord], kind: str) -> set[str]:
        permitted = {_IDS[kind]}
        for preset in records:
            if preset['source'] == 'system' and preset['type'] == kind:
                permitted |= (preset['raw'].keys() & self.settings.keys()) - _METADATA
        return permitted

    def preset_keys(self, type: str) -> set[str]:
        return self._group_keys(self._records(), _type(type))

    def describe_setting(self, key: str) -> JsonObject | None:
        self.engine.verify()
        if key not in self.settings:
            return None
        return dict(copy.deepcopy(self.settings[key]), key=key, schema=self._schema_status())

    def search_settings(self, query: str, limit: int = 25) -> JsonObject:
        self.engine.verify()
        if type(limit) is not int or limit not in range(1, 101):
            raise ValueError('Setting search limit must be 1..100')
        needle = query.strip().lower()
        words = [word for word in needle.replace('_', ' ').split() if len(word) > 2]
        ranked: list[tuple[tuple[int, int, str], JsonValue]] = []
        for key, definition in self.settings.items():
            fields = (key.lower(), text(definition.get('label') or '').lower(), text(definition.get('tooltip') or '').lower())
            rank = next((index for index, value in enumerate(fields) if needle and needle in value), 3)
            points = 0
            for word in words:
                points += next((3 - index for index, value in enumerate(fields) if word in value), 0)
            if needle and (rank != 3 or points):
                summary: JsonObject = {'key': key, 'label': definition.get('label'), 'category': definition.get('category'),
                                       'tooltip': text(definition.get('tooltip') or '')[:160]}
                ranked.append(((-1 if key.lower() == needle else rank, -points, key), summary))
        ranked.sort(key=lambda pair: pair[0])
        return {'settings': [value for _, value in ranked[:limit]], 'schema': self._schema_status()}

    @staticmethod
    def _name(name: str) -> str:
        if isinstance(name, str) and 1 <= len(name) <= 256 and name.strip() and all(ord(char) >= 32 for char in name):
            return name
        raise ValueError('Preset name must be 1..256 printable characters')

    @staticmethod
    def _editable(rec: PresetRecord) -> None:
        if rec['read_only'] or rec['source'] != 'draft':
            raise ValueError('This preset is read-only; save_preset with another name in drafts')

    def _draft_path(self, kind: str, name: str) -> str:
        return self.repository.draft_path(kind=kind, name=name)

    def _write(self, path: str, doc: JsonObject, previous: PresetRecord | None = None) -> None:
        self.engine.verify()
        self.repository.write(location=path, doc=doc, previous=previous)

    def _baseline(self, records: list[PresetRecord], kind: str, name: str, raw: JsonObject,
                  parent: str, existing: PresetRecord | None) -> tuple[JsonObject, JsonObject]:
        if parent == name:
            raise ValueError('Preset inheritance cycle')
        if existing is not None:
            candidate = existing.copy()
            candidate['raw'] = {**raw, 'inherits': parent}
            baseline, _, metadata = self._resolve(records, candidate)
            return baseline, metadata
        if parent:
            baseline, chain, metadata = self._resolve(records, self._find(records, kind, parent))
            if any(ancestor['name'] == name for ancestor in chain):
                raise ValueError('Preset inheritance cycle')
            return baseline, metadata
        return {}, {}

    @staticmethod
    def _detached(configuration: JsonObject, metadata: JsonObject, keys: set[str], allow_gcode: bool) -> JsonObject:
        result = {key: copy.deepcopy(value) for key, value in configuration.items() if key in keys}
        result.update(metadata)
        for key, value in result.items():
            empty = value in ([], [''], '')
            if key == 'post_process' and not empty:
                raise ValueError('post_process: prohibited preset write')
            if _transport(key) and key not in _IDENTITY and not empty and value is not None:
                raise ValueError(f'{key}: transport cannot be copied into a detached draft')
            if _gcode(key) and not empty and not allow_gcode:
                raise ValueError(f'{key}: detaching G-code requires allow_custom_gcode=True')
        return result

    def save_preset(self, type: str, name: str, config: JsonObject, detach: bool = False,
                    allow_custom_gcode: bool = False) -> JsonObject:
        with self._lock:
            kind = _type(type)
            name = self._name(name)
            if not isinstance(config, dict):
                raise ValueError('Preset configuration must be an object')
            records = self._records()
            existing = self._find(records, kind, name) if any((item['type'], item['name']) == (kind, name) for item in records) else None
            if existing is not None:
                self._editable(existing)
            raw = {} if existing is None else copy.deepcopy(existing['raw'])
            parent = config.get('inherits', raw.get('inherits', ''))
            if not isinstance(parent, str):
                raise ValueError('Preset inherits must name one parent')
            baseline, metadata = self._baseline(records, kind, name, raw, parent, existing)
            if kind == 'filament':
                baseline = normalize_filament_profile(baseline, self.schema)
            supplied: JsonObject = {key: value for key, value in config.items() if key not in _METADATA}
            for key, value in supplied.items():
                if key == 'post_process' or (_transport(key) and baseline.get(key) != value):
                    raise ValueError(f'{key}: prohibited preset write')
                if key not in self.settings:
                    raise ValueError(f'Unknown native setting: {key}')
                if _placeholder(value):
                    raise ValueError(f'{key}: a redacted placeholder cannot be written')
            allowed = self._group_keys(records, kind)
            differences = {key: value for key, value in supplied.items() if key in allowed and value != baseline.get(key)}
            normalized = validate_changes(differences, baseline, self.schema, allow_custom_gcode)
            if detach:
                raw = self._detached({**baseline, **normalized}, metadata, allowed, allow_custom_gcode)
                parent = ''
            else:
                raw.update(normalized)
            identifier = supplied.get(_IDS[kind])
            if kind == 'filament' and isinstance(identifier, list) and len(identifier) != 1:
                raise ValueError('A filament preset ID must have one slot; project slots are not resized')
            raw.update({'name': name, 'type': _NATIVE_TYPES[kind], 'from': 'User', 'inherits': parent,
                        'version': raw.get('version', text(record(self.schema['_meta'])['native_version'])),
                        'instantiation': 'true', _IDS[kind]: [name] if kind == 'filament' else name})
            destination = existing['path'] if existing is not None else self._draft_path(kind, name)
            self._write(destination, raw, existing)
            return dict(self.get_preset_config(kind, name), saved=True, ignored_keys=[key for key in sorted(supplied.keys() - allowed)])

    def edit_preset(self, type: str, name: str, changes: JsonObject, allow_custom_gcode: bool = False) -> JsonObject:
        with self._lock:
            kind = _type(type)
            snapshot = self._records()
            self._editable(self._find(snapshot, kind, name))
            if not isinstance(changes, dict):
                raise ValueError('Preset changes must be an object')
            invalid = changes.keys() - self._group_keys(snapshot, kind)
            if invalid:
                raise ValueError('Settings do not belong to this preset group/type: ' + ', '.join(sorted(invalid)))
            return self.save_preset(kind, name, changes, allow_custom_gcode=allow_custom_gcode)

    @staticmethod
    def _unreferenced(records: list[PresetRecord], rec: PresetRecord) -> None:
        children = sorted(item['name'] for item in records if item['type'] == rec['type'] and item['raw'].get('inherits') == rec['name'])
        if children:
            raise ValueError('Preset is referenced by inheritance dependents: ' + ', '.join(children))

    def rename_preset(self, type: str, name: str, new_name: str) -> JsonObject:
        with self._lock:
            kind = _type(type)
            destination_name = self._name(new_name)
            snapshot = self._records()
            original = self._find(snapshot, kind, name)
            self._editable(original)
            if any((item['type'], item['name']) == (kind, destination_name) for item in snapshot):
                raise ValueError('Preset name already exists; choose another name')
            self._unreferenced(snapshot, original)
            renamed = copy.deepcopy(original['raw'])
            renamed['name'] = destination_name
            renamed[_IDS[kind]] = [destination_name] if kind == 'filament' else destination_name
            destination = self._draft_path(kind, destination_name)
            self._write(destination, renamed)
            self.repository.remove(original, rollback_path=destination, action='rename')
            return dict(self.get_preset_config(kind, destination_name), renamed_from=name)

    def delete_preset(self, type: str, name: str) -> JsonObject:
        with self._lock:
            snapshot = self._records()
            original = self._find(snapshot, _type(type), name)
            self._editable(original)
            self._unreferenced(snapshot, original)
            self.repository.remove(original, action='delete')
            return {'deleted': True, 'name': name, 'type': original['type'], 'sha256': original['sha256'], 'path': original['path']}
