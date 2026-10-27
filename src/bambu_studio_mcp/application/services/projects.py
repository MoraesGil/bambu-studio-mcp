"""Project commands and material selection over storage and native ports."""
from __future__ import annotations

import copy

from ...domain.settings.filament import (
    FilamentLayout, VOLUME_TYPES, _filament_layout, _flow_index, _mode_name,
    _native_default_vector, _profile_index, _project_vector, _public, _validate_flow_vectors,
)
from ...domain.settings.normalization import _IDS, _VECTORS, _transport, _type, normalize_filament_profile, validate_changes
from ...domain.settings.values import array, integer, record, strings, text
from ...domain.types.json import JsonObject, JsonValue
from ..contracts.presets import PresetCatalog
from ..contracts.projects import NativeEditing, ProjectDocument, ProjectSlicer, RevisionRepository

_PRESET_COMPATIBILITY = frozenset({'compatible_printers', 'compatible_prints',
                                  'compatible_printers_condition', 'compatible_prints_condition'})
_PROJECT_FILAMENT_KEYS = frozenset({'filament_ids', 'filament_self_index', 'filament_map', 'filament_map_2',
    'filament_volume_map', 'filament_nozzle_map', 'physical_extruder_map'})
_DOCUMENT_OPERATIONS = frozenset({'add_modifier', 'duplicate_object', 'delete_object', 'transform_object',
    'add_plate', 'delete_plate', 'move_object_to_plate', 'set_height_range', 'reset_layer_height'})
_NATIVE_OPERATIONS = frozenset({'arrange_plate', 'auto_orient', 'set_layer_height'})


class ProjectService:
    def __init__(self, slicer: ProjectSlicer, repository: RevisionRepository,
                 catalog: PresetCatalog, native: NativeEditing, schema: JsonObject) -> None:
        self.slicer = slicer
        self.repository = repository
        self.catalog = catalog
        self.native = native
        self.schema = schema
        self._settings = {key: record(value) for key, value in schema.items() if not key.startswith('_')}

    def _state(self, project_id: str) -> JsonObject:
        return self.repository.state(project_id)

    @staticmethod
    def _expect(state: JsonObject, expected_sha256: str | None) -> None:
        if expected_sha256 is not None and state['sha256'] != expected_sha256:
            raise ValueError('Stale project revision; reload the current sha256')

    def load_model(self, path: str) -> JsonObject:
        with self.repository.transaction():
            identity = self.slicer.engine.identity()
            return self.repository.load(path=path, identity=identity)

    def _read(self, project_id: str, capability: str, keys: list[str] | None = None) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            document = self.repository.open(state)
            if capability == 'project':
                value: JsonValue = document.describe()
            elif capability == 'config':
                value = _public(document.get_config(keys))
            elif capability == 'objects':
                value = document.list_objects()
            else:
                value = document.list_plates()
            return {**state, capability: value}

    def get_status(self, project_id: str) -> JsonObject:
        return self._read(project_id, 'project')

    def get_config(self, project_id: str, keys: list[str] | None = None) -> JsonObject:
        return self._read(project_id, 'config', keys)

    def list_objects(self, project_id: str) -> JsonObject:
        return self._read(project_id, 'objects')

    def list_plates(self, project_id: str) -> JsonObject:
        return self._read(project_id, 'plates')

    def select_plate(self, project_id: str, plate: int) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            plates = self.repository.open(state).list_plates()
            if type(plate) is not int or plate not in range(1, len(plates) + 1):
                raise ValueError('plate must identify an existing plate (1-based)')
            return self.repository.select_plate(state=state, plate=plate)

    def _apply(self, document: ProjectDocument, state: JsonObject, requested: JsonObject, allow_gcode: bool) -> JsonObject:
        arguments = copy.deepcopy(requested)
        operation = arguments.pop('operation', None)
        if not isinstance(operation, str):
            raise ValueError(f'Unknown editing operation: {operation}')
        if operation in ('set_config', 'set_object_config'):
            configuration = arguments.pop('changes', None)
            validated = validate_changes(configuration, document.config, self.schema, allow_gcode)
            if operation == 'set_config' and arguments:
                raise ValueError(f'Unexpected set_config arguments: {sorted(arguments)}')
            result = document.execute(operation, {**arguments, 'changes': validated})
        elif operation in _DOCUMENT_OPERATIONS:
            result = document.execute(operation, arguments)
        elif operation in _NATIVE_OPERATIONS:
            result = self.native.execute(document, operation, arguments, state)
        else:
            raise ValueError(f'Unknown editing operation: {operation}')
        return {'operation': operation, 'result': result}

    def edit_project(self, project_id: str, operations: list[JsonObject], expected_sha256: str | None = None,
                     allow_custom_gcode: bool = False) -> JsonObject:
        if not isinstance(operations, list) or len(operations) == 0:
            raise ValueError('operations must be a nonempty list')
        with self.repository.transaction():
            self.slicer.engine.verify()
            state = self._state(project_id)
            self._expect(state, expected_sha256)
            document = self.repository.open(state)
            results: list[JsonValue] = []
            for operation in operations:
                if not isinstance(operation, dict):
                    raise ValueError('Each operation must be an object')
                results.append(self._apply(document, state, operation, allow_custom_gcode))
            return self.repository.commit(state, document, operations, results, self.slicer.engine)

    def set_config(self, project_id: str, changes: JsonObject, expected_sha256: str | None = None,
                   allow_custom_gcode: bool = False) -> JsonObject:
        commands: list[JsonObject] = [dict(operation='set_config', changes=changes)]
        return self.edit_project(project_id, commands, expected_sha256, allow_custom_gcode)

    def add_modifier(self, project_id: str, object_id: int, center: list[float], dimensions: list[float],
                     changes: JsonObject, name: str = 'Modifier', expected_sha256: str | None = None) -> JsonObject:
        command: JsonObject = {'operation': 'add_modifier', 'object_id': object_id, 'center': list(center),
                               'dimensions': list(dimensions), 'changes': changes, 'name': name}
        return self.edit_project(project_id, [command], expected_sha256)

    def _selectable(self, preset: JsonObject, current: JsonObject, kind: str) -> tuple[JsonObject, list[str], list[str]]:
        source = record(preset['config'])
        ignored = set(source) - self._settings.keys()
        changes: JsonObject = {}
        expanded: list[str] = []
        for key in source:
            if key not in self._settings:
                continue
            value = copy.deepcopy(source[key])
            if key in ('printer_model', 'printer_technology'):
                existing = current.get(key, 'FFF' if key == 'printer_technology' else None)
                if value != existing:
                    raise ValueError('Selected preset changes the target machine; use a matching saved project')
            if key == 'post_process' or _transport(key) or key in _PRESET_COMPATIBILITY:
                if key == 'post_process' and value not in ('', [], ['']):
                    raise ValueError('Selected preset contains unsupported post-processing scripts')
                ignored.add(key)
                continue
            vector_type = self._settings[key]['type']
            if vector_type in _VECTORS and not isinstance(value, list):
                vector: list[JsonValue] = list(value.split(',')) if isinstance(value, str) and vector_type != 'coStrings' else [value]
                previous = current.get(key)
                if kind != 'filament' and len(vector) == 1 and isinstance(previous, list):
                    vector = [vector[0] for _ in previous]
                value = vector
                expanded.append(key)
            changes[key] = value
        return changes, sorted(ignored), expanded

    @staticmethod
    def _slot(layout: FilamentLayout, slot: int) -> None:
        if type(slot) is not int or slot not in range(layout['count']):
            raise ValueError('filament_slot must identify an existing slot (0-based)')

    def _select_material(self, current: JsonObject, preset: JsonObject, changes: JsonObject,
                         name: str, slot: int, mode: str) -> tuple[JsonObject, JsonObject, list[JsonValue]]:
        layout = _filament_layout(current)
        self._slot(layout, slot)
        _validate_flow_vectors(current, self._settings, layout)
        volume = _mode_name(mode)
        target = _flow_index(layout, slot, volume)
        source = {key: value for key, value in changes.items() if key not in _PROJECT_FILAMENT_KEYS}
        profile = normalize_filament_profile(source, self.schema)
        profile_index = _profile_index(profile, layout['extruder_types'][slot], volume)
        variants = strings(profile.get('filament_extruder_variant', ['Direct Drive Standard']))
        defaults: list[JsonValue] = []
        selected: JsonObject = {}
        for key, value in profile.items():
            if key in ('filament_extruder_variant', 'filament_settings_id'):
                continue
            if not isinstance(value, list):
                selected[key] = value
                continue
            if len(value) == 0:
                continue
            per_variant = self._settings[key].get('variant_domain') == 'filament'
            cardinality = layout['total'] if per_variant else layout['count']
            if key in current:
                vector = _project_vector(current, key, cardinality)
            else:
                status = record(preset['schema'])
                vector = _native_default_vector(key, cardinality, self._settings[key], status, self.schema)
                defaults.append({'key': key, 'default': vector[0], 'cardinality': cardinality,
                    'source': {'repository': self.slicer.engine.identity()['repository'],
                               'path': 'src/libslic3r/PrintConfig.cpp', 'source_commit': status['source_commit']}})
            vector[target if per_variant else slot] = copy.deepcopy(value[profile_index if per_variant else 0])
            selected[key] = vector
        names = array(current['filament_settings_id']).copy()
        names[slot] = name
        ids: list[JsonValue] = _project_vector(current, 'filament_ids', layout['count']) if 'filament_ids' in current else ['' for _ in range(layout['count'])]
        ids[slot] = text(record(preset.get('effective_metadata', {})).get('filament_id', ''))
        volume_ids = {value: str(key) for key, value in VOLUME_TYPES.items()}
        volumes: list[JsonValue] = [volume_ids[value] for value in layout['volume_types']]
        volumes[slot] = volume_ids[volume]
        selected.update({'filament_settings_id': names, 'filament_ids': ids, 'filament_volume_map': volumes})
        wanted = f"{layout['extruder_types'][slot]} {volume}"
        trace: JsonObject = {'project_flow_index': target, 'preset_flow_index': profile_index,
                             'project_flow_layout': 'bambu_variants', 'preset_flow_mode': variants[profile_index],
                             'preset_flow_fallback': wanted not in variants}
        return selected, trace, defaults

    def select_preset(self, project_id: str, type: str, name: str, filament_slot: int = 0,
                      flow_mode: str = 'standard', expected_sha256: str | None = None) -> JsonObject:
        kind = _type(type)
        with self.repository.transaction():
            state = self._state(project_id)
            self._expect(state, expected_sha256)
            current = self.repository.open(state).config
            preset = self.catalog.get_preset_config(type, name)
            changes, ignored, expanded = self._selectable(preset, current, kind)
            trace: JsonObject = {}
            defaults: list[JsonValue] = []
            if kind == 'filament':
                changes, trace, defaults = self._select_material(current, preset, changes, name, filament_slot, flow_mode)
            else:
                changes[_IDS[kind]] = name
            edited = self.set_config(project_id, changes, expected_sha256, allow_custom_gcode=True)
            return {**edited, 'selected_preset': {'type': type, 'name': name, 'filament_slot': filament_slot,
                        'flow_mode': flow_mode, 'source': preset.get('source'), **trace},
                    'ignored_config_keys': [key for key in ignored], 'normalized_vector_keys': [key for key in expanded], 'native_defaults_applied': defaults,
                    'schema': preset.get('schema'),
                    'configuration_scope': 'Resolved JSON inheritance; native defaults and final region settings require slicing'}

    def _extract_material(self, current: JsonObject, config: JsonObject,
                          slot: int) -> tuple[JsonObject, FilamentLayout, str]:
        layout = _filament_layout(current)
        self._slot(layout, slot)
        _validate_flow_vectors(current, self._settings, layout)
        mode = layout['volume_types'][slot]
        index = _flow_index(layout, slot, mode)
        output: JsonObject = {'filament_extruder_variant': [layout['variants'][index]]}
        for key, value in config.items():
            if key in _PROJECT_FILAMENT_KEYS or key == 'filament_extruder_variant' or not isinstance(value, list):
                continue
            variant = self._settings[key].get('variant_domain') == 'filament'
            source = _project_vector(current, key, layout['total'] if variant else layout['count'])
            output[key] = [source[index if variant else slot]]
        return output, layout, mode

    def _preserve_parent_variants(self, config: JsonObject, inherited: JsonObject,
                                  layout: FilamentLayout, slot: int, mode: str) -> None:
        normalized = normalize_filament_profile(inherited, self.schema)
        variants = strings(normalized.get('filament_extruder_variant', ['Direct Drive Standard']))
        target = _profile_index(normalized, layout['extruder_types'][slot], mode)
        for key, supplied in list(config.items()):
            if key == 'filament_extruder_variant' or not isinstance(supplied, list):
                continue
            parent = normalized.get(key)
            if self._settings[key].get('variant_domain') == 'filament':
                if not isinstance(parent, list) or not parent:
                    if len(variants) > 1:
                        raise ValueError(f'Parent filament vector {key} has no values for its other variants')
                    continue
                replacement = parent.copy()
                replacement[target] = supplied[0]
                config[key] = replacement
            elif isinstance(parent, list) and parent:
                config[key] = [supplied[0], *parent[1:]]
        config['filament_extruder_variant'] = list(variants)

    def save_preset(self, project_id: str, type: str, name: str, filament_slot: int = 0,
                    detach: bool = False, allow_custom_gcode: bool = False) -> JsonObject:
        kind = _type(type)
        with self.repository.transaction():
            state = self._state(project_id)
            current = self.repository.open(state).config
            allowed = self.catalog.preset_keys(type)
            config = {key: copy.deepcopy(value) for key, value in current.items()
                      if key in allowed and key != 'post_process' and key not in _PRESET_COMPATIBILITY}
            layout: FilamentLayout | None = None
            mode = ''
            parent = current.get(_IDS[kind], '')
            if kind == 'filament':
                config, layout, mode = self._extract_material(current, config, filament_slot)
                parent = parent[filament_slot] if isinstance(parent, list) else ''
            available = array(self.catalog.list_presets(type, include_system=True)['presets'])
            known_parent = any(record(item)['name'] == parent for item in available)
            if known_parent:
                if parent != name:
                    config['inherits'] = parent
                if layout is not None:
                    inherited = record(self.catalog.get_preset_config(type, text(parent))['config'])
                    self._preserve_parent_variants(config, inherited, layout, filament_slot, mode)
            else:
                for protected in ('printer_model', 'printer_technology'):
                    config.pop(protected, None)
            return self.catalog.save_preset(type, name, config, detach, allow_custom_gcode)

    def slice_project(self, project_id: str, plate: int | None = None, force: bool = False,
                      allow_newer_file: bool = True) -> JsonObject:
        with self.repository.transaction():
            state = self._state(project_id)
            path = text(state['path'])
            selected = integer(state['selected_plate']) if plate is None else plate
        return self.slicer.slice_3mf(path, selected, force=force, allow_newer_file=allow_newer_file)

    def apply_and_slice(self, project_id: str, changes: JsonObject, plate: int | None = None,
                        expected_sha256: str | None = None) -> JsonObject:
        with self.repository.transaction():
            revision = self.set_config(project_id, changes, expected_sha256)
            selected = integer(revision['selected_plate']) if plate is None else plate
            path = text(revision['path'])
        result = self.slicer.slice_3mf(path, selected, force=True)
        return {'project': revision, 'slice': result}
