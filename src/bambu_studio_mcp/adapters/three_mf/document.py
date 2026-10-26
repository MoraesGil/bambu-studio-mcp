"""Bambu-native 3MF document editing with stable public capabilities.

The package is never round-tripped through a mesh converter. Editable trees,
opaque ZIP members and native ordinal metadata have separate owners. Native
semantics are derived from BambuStudio 926a7192574bcb9b3a732e1ec59a46d79cb45466.
"""
from __future__ import annotations

import copy
import functools
import json
import math
import re
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree as ET

from .affine import Affine, IDENTITY_VALUES
from .heights import HeightStore
from .options import escape_cstyle as _escape_cstyle, option_text
from .package import (
    BambuPackage, without_slice_outputs, relationship_target,
    CORE as CORE, PRODUCTION as PRODUCTION, CONFIG as CONFIG,
    SETTINGS as SETTINGS, RANGES as RANGES, PROFILE as PROFILE,
    BRIM as BRIM, CUT as CUT, CUSTOM_GCODE as CUSTOM_GCODE, PARKED as PARKED,
)
from .xml import (
    XML_LOCK as _XML_LOCK, TreeState as TreeState,
    child as _child, children as _children, local_name as _local,
    metadata as _metadata, set_metadata as _set_metadata,
    read_xml as _read_xml, tree_state as _tree_state, xml_bytes as _xml_bytes,
)
from ...domain.types.json import JsonValue, JsonObject, decode_object
from ...domain.types.values import ObjectId, XYZ, BoxDimensions
from ...domain.entities.project import (
    PlateRecord, ObjectRecord, ProjectDescription, ModifierRecord,
    DeletePlateRecord, LayoutResult, MembershipRecord, InstanceRecord, PartRecord,
)

IDENTITY = list(IDENTITY_VALUES)
_STRUCTURAL = frozenset(('name', 'module', 'matrix', 'source_file', 'merged_group_id'))
__all__ = [
    'Project', 'CORE', 'PRODUCTION', 'CONFIG', 'SETTINGS', 'RANGES', 'PROFILE',
    'BRIM', 'CUT', 'CUSTOM_GCODE', 'PARKED', 'IDENTITY', 'TreeState',
    '_local', '_children', '_child', '_metadata', '_option_config', '_set_metadata',
    '_option_types', '_escape_cstyle', '_native_option_value', '_read_xml',
    '_tree_state', '_xml_bytes', '_transform', 'compose_transform', '_XML_LOCK',
]


def _option_config(root: ET.Element) -> dict[str, str]:
    options = _metadata(root)
    return {key: options[key] for key in options if key not in _STRUCTURAL and not key.startswith('source_')}


@functools.lru_cache(maxsize=1)
def _option_types() -> dict[str, str]:
    from ...presets import load_schema
    types = {}
    for key, definition in load_schema().items():
        kind = definition['type']
        if not isinstance(kind, str):
            raise ValueError(f'Native option type must be a string: {key}')
        types[key] = kind
    return types


def _native_option_value(key: str, value: JsonValue | tuple[JsonValue, ...]) -> str | None:
    return option_text(key, value, _option_types().get(key))


def _transform(text: str | None) -> list[float]:
    return list(Affine.parse(text).values)


def compose_transform(a: list[float], b: list[float]) -> list[float]:
    return list(Affine.of(a).then(Affine.of(b)).values)


def _xyz(value: Sequence[float] | None, default: Sequence[float]) -> list[float]:
    if value is None:
        return list(default)
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError('Transform vectors must contain three finite numbers')
    numbers = [float(entry) for entry in value]
    if not all(math.isfinite(entry) for entry in numbers):
        raise ValueError('Transform vectors must contain three finite numbers')
    return numbers


class Project:
    """An in-memory private revision over one read-only source package."""

    def __init__(self, path: Path) -> None:
        self._package = BambuPackage(Path(path))
        self.path = self._package.path
        self.data = self._package.members.copy()
        self._infos = self._package.infos
        self._comment = self._package.comment
        self._model_name = self._package.root_name
        self.model, model_bindings = _read_xml(self.data[self._model_name])
        self.settings, settings_bindings = _read_xml(self.data.get(SETTINGS, b'<config/>'))
        self._namespaces = {self._model_name: model_bindings, SETTINGS: settings_bindings}
        self._initial_xml = {self._model_name: _tree_state(self.model), SETTINGS: _tree_state(self.settings)}
        self.config = decode_object(self.data.get(CONFIG, b'{}'), 'Project settings')
        self._initial_config = copy.deepcopy(self.config)

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        before = copy.deepcopy((self.model, self.settings, self.config, self.data))
        try:
            yield
        except BaseException:
            self.model, self.settings, self.config, self.data = before
            raise

    def _resources(self) -> ET.Element:
        root = _child(self.model, 'resources')
        if root is None:
            raise ValueError('The root model has no resources')
        return root

    def _items(self, object_id: int | str | None = None) -> list[ET.Element]:
        items = _children(_child(self.model, 'build'), 'item')
        return [item for item in items if object_id is None or item.get('objectid') == str(object_id)]

    def _root_ids(self) -> list[int]:
        return list(dict.fromkeys(int(item.attrib['objectid']) for item in self._items()))

    def _objects(self) -> list[ET.Element]:
        active = set(self._root_ids())
        return [obj for obj in _children(self._resources(), 'object') if int(obj.attrib['id']) in active]

    def _object(self, object_id: int | str) -> ET.Element:
        for obj in self._objects():
            if obj.get('id') == str(object_id):
                return obj
        raise ValueError(f'Unknown root object ID: {object_id}')

    def _object_settings(self, object_id: int | str, create: bool = False) -> ET.Element | None:
        matching = [node for node in _children(self.settings, 'object') if node.get('id') == str(object_id)]
        if matching:
            return matching[0]
        if not create:
            return None
        node = ET.Element('object', {'id': str(object_id)})
        self.settings.insert(0, node)
        return node

    def _instance_index(self) -> dict[tuple[int, int], ET.Element]:
        result: dict[tuple[int, int], ET.Element] = {}
        ordinals: dict[int, int] = {}
        for item in self._items():
            oid = int(item.attrib['objectid'])
            ordinal = ordinals.get(oid, 0)
            result[oid, ordinal] = item
            ordinals[oid] = ordinal + 1
        return result

    def list_plates(self) -> list[PlateRecord]:
        records: list[PlateRecord] = []
        for number, plate in enumerate(_children(self.settings, 'plate'), 1):
            fields = _metadata(plate)
            members: list[MembershipRecord] = []
            for member in _children(plate, 'model_instance'):
                identity = _metadata(member)
                members.append({'object_id': int(identity['object_id']), 'instance_id': int(identity['instance_id'])})
            records.append({'plate': int(fields.get('plater_id', number)), 'name': fields.get('plater_name', ''),
                            'locked': fields.get('locked', 'false').lower() in ('1', 'true'), 'objects': members})
        return records

    def list_objects(self) -> list[ObjectRecord]:
        assignments = {(member['object_id'], member['instance_id']): plate['plate']
                       for plate in self.list_plates() for member in plate['objects']}
        records: list[ObjectRecord] = []
        for obj in self._objects():
            oid = int(obj.attrib['id'])
            config = self._object_settings(oid)
            properties = _metadata(config)
            instances: list[InstanceRecord] = []
            for ordinal, item in enumerate(self._items(oid)):
                instances.append({'instance_id': ordinal, 'transform': _transform(item.get('transform')),
                                  'plate': assignments.get((oid, ordinal)), 'printable': item.get('printable', '1') != '0'})
            parts: list[PartRecord] = []
            for part in _children(config, 'part'):
                parts.append({'part_id': int(part.attrib['id']), 'name': _metadata(part).get('name', ''),
                              'subtype': part.get('subtype', 'normal_part'), 'config': _option_config(part)})
            records.append({'object_id': oid, 'name': properties.get('name', obj.get('name', f'Object {oid}')),
                            'config': _option_config(config) if config is not None else {}, 'instances': instances, 'parts': parts})
        return records

    def describe(self) -> ProjectDescription:
        return {'plates': self.list_plates(), 'objects': self.list_objects()}

    def get_config(self, keys: Sequence[str] | None = None) -> JsonObject:
        chosen = self.config.keys() if keys is None else keys
        return copy.deepcopy({key: self.config[key] for key in chosen if key in self.config})

    def _slot_count(self) -> int:
        values = self.config.get('filament_colour', self.config.get('filament_settings_id', []))
        if not isinstance(values, (dict, list, str)):
            raise ValueError('Material slots must be a native array')
        return len(values)

    def set_config(self, changes: JsonObject) -> None:
        count = self._slot_count()
        for key, new in changes.items():
            if key in ('printer_model', 'printer_variant', 'printer_technology') and key in self.config and self.config[key] != new:
                raise ValueError("Changing the project's machine is a separate operation")
            if not count or not isinstance(new, list):
                continue
            required = {'flush_volumes_matrix': count ** 2, 'flush_volumes_vector': count * 2}.get(key)
            old = self.config.get(key)
            material = key.startswith('filament_') or key in ('nozzle_temperature', 'nozzle_temperature_initial_layer', 'additional_cooling_fan_speed')
            if required is None and material and isinstance(old, list) and len(old) == count:
                required = count
            if required is not None and len(new) != required:
                raise ValueError('Changing the material slot count is unsupported')
        self.config.update(copy.deepcopy(changes))

    def set_object_config(self, object_id: int | str, changes: JsonObject, part_id: int | None = None) -> None:
        self._object(object_id)
        parent = self._object_settings(object_id)
        if part_id is not None:
            parent = next((part for part in _children(parent, 'part') if part.get('id') == str(part_id)), None)
            if parent is None:
                raise ValueError(f'Unknown part ID {part_id} in object {object_id}')
        encoded: dict[str, str | None] = {}
        for key, value in changes.items():
            if key in ('matrix', 'source_file', 'merged_group_id') or key.startswith('source_'):
                raise ValueError('Source and geometry metadata are not print overrides')
            if key == 'extruder' and value is not None:
                count = self._slot_count()
                integral = type(value) is int or isinstance(value, str) and re.fullmatch(r'\d+', value) is not None
                if not integral or not 0 <= int(value) <= count:  # type: ignore[arg-type]
                    raise ValueError(f'extruder must be 0 (inherit) or an existing material slot 1..{count}')
            encoded[key] = _native_option_value(key, value)
        if parent is None:
            parent = self._object_settings(object_id, create=True)
        assert parent is not None
        for key, value in encoded.items():
            _set_metadata(parent, key, value)

    def _next_resource_id(self) -> int:
        ids = {int(node.attrib['id']) for node in self._resources() if 'id' in node.attrib}
        ids.update(int(node.attrib['id']) for node in self.settings.iter() if _local(node.tag) == 'part' and 'id' in node.attrib)
        ids.update(int(node.attrib['objectid']) for node in self.model.iter() if _local(node.tag) == 'component')
        return max(ids, default=0) + 1

    def add_modifier(self, object_id: int, center: JsonValue, dimensions: JsonValue, changes: JsonObject, name: str = 'Modifier') -> ModifierRecord:
        from ...presets import validate_changes
        ObjectId(object_id)
        obj = self._object(object_id)
        if self.model.get('unit', 'millimeter') != 'millimeter':
            raise ValueError('Modifiers require millimeter model units')
        if not isinstance(center, list) or len(center) != 3 or not all(type(v) in (int, float) for v in center):
            raise ValueError('center must contain three finite XYZ numbers')
        if not isinstance(dimensions, list) or len(dimensions) != 3 or not all(type(v) in (int, float) for v in dimensions):
            raise ValueError('dimensions must contain three finite XYZ numbers')
        position = XYZ(*(float(v) for v in center)).as_list()  # type: ignore[arg-type]
        sizes = BoxDimensions(*(float(v) for v in dimensions)).as_list()  # type: ignore[arg-type]
        if not isinstance(name, str) or not name.strip():
            raise ValueError('name must be a nonempty string')
        supported = {'outer_wall_speed', 'inner_wall_speed', 'sparse_infill_speed', 'top_surface_speed', 'internal_solid_infill_speed'}
        if not isinstance(changes, dict) or not changes or set(changes).difference(supported):
            raise ValueError('Modifier changes require supported regional speed keys')
        if any(isinstance(v, list) and len(v) == 0 for v in changes.values()):
            raise ValueError('Modifier speed arrays must not be empty')
        normalized = validate_changes(changes, self.config)
        options = {key: _native_option_value(key, value) for key, value in normalized.items()}
        mesh, components = _child(obj, 'mesh'), _child(obj, 'components')
        if (mesh is None) == (components is None):
            raise ValueError('Root must contain exactly one mesh or components collection')
        settings = self._object_settings(object_id)
        old_parts = _children(settings, 'part')
        if mesh is not None and (len(old_parts) > 1 or any(part.get('id') != str(object_id) for part in old_parts)):
            raise ValueError('Direct mesh part metadata cannot be mapped unambiguously')
        first_id = self._next_resource_id()
        modifier_id = first_id + int(mesh is not None)
        volume = self._box_volume(modifier_id, sizes)
        with self._transaction():
            settings = self._object_settings(object_id, create=True)
            assert settings is not None
            if mesh is not None:
                shared = ET.SubElement(self._resources(), f'{{{CORE}}}object', {'id': str(first_id), 'type': 'model'})
                obj.remove(mesh)
                shared.append(mesh)
                components = ET.SubElement(obj, f'{{{CORE}}}components')
                ET.SubElement(components, f'{{{CORE}}}component', {'objectid': str(first_id)})
                if old_parts:
                    old_parts[0].set('id', str(first_id))
                else:
                    ET.SubElement(settings, 'part', {'id': str(first_id), 'subtype': 'normal_part'})
            assert components is not None
            self._resources().append(volume)
            ET.SubElement(components, f'{{{CORE}}}component', {'objectid': str(modifier_id), 'transform': Affine().shift(position).serialize()})
            part = ET.SubElement(settings, 'part', {'id': str(modifier_id), 'subtype': 'modifier_part'})
            _set_metadata(part, 'name', name)
            for key, value in options.items():
                _set_metadata(part, key, value)
        return {'object_id': object_id, 'part_id': modifier_id, 'subtype': 'modifier_part'}

    @staticmethod
    def _box_volume(resource_id: int, sizes: Sequence[float]) -> ET.Element:
        from itertools import product
        obj = ET.Element(f'{{{CORE}}}object', {'id': str(resource_id), 'type': 'model'})
        mesh = ET.SubElement(obj, f'{{{CORE}}}mesh')
        vertices = ET.SubElement(mesh, f'{{{CORE}}}vertices')
        for signs in product((-1, 1), repeat=3):
            coordinates = {axis: format(signs[i] * sizes[i] * .5, '.17g') for i, axis in enumerate(('x', 'y', 'z'))}
            ET.SubElement(vertices, f'{{{CORE}}}vertex', coordinates)
        faces = ET.SubElement(mesh, f'{{{CORE}}}triangles')
        # Six outward quads, triangulated without changing shared vertices.
        for a, b, c, d in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
            for face in ((a, b, c), (a, c, d)):
                ET.SubElement(faces, f'{{{CORE}}}triangle', {f'v{i}': str(vertex) for i, vertex in enumerate(face, 1)})
        return obj

    def _plate(self, plate: int) -> ET.Element:
        if type(plate) is not int:
            raise ValueError('plate must be an existing 1-based plate ID')
        for ordinal, node in enumerate(_children(self.settings, 'plate'), 1):
            if int(_metadata(node).get('plater_id', ordinal)) == plate:
                return node
        raise ValueError(f'Unknown plate ID: {plate}')

    def _bed_dimensions(self) -> tuple[float, float]:
        area = self.config.get('printable_area')
        try:
            if not isinstance(area, list) or len(area) < 3 or any(not isinstance(v, str) for v in area):
                raise ValueError
            points = [tuple(map(float, value.split('x'))) for value in area if isinstance(value, str)]
            if any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
                raise ValueError
            widths = tuple(max(p[axis] for p in points) - min(p[axis] for p in points) for axis in (0, 1))
            if min(widths) <= 0:
                raise ValueError
            return widths[0], widths[1]
        except (TypeError, ValueError):
            raise ValueError('Plate operations require an explicit, valid printable_area') from None

    @staticmethod
    def _plate_origin(position: int, count: int, dimensions: Sequence[float]) -> list[float]:
        row, column = divmod(position, math.isqrt(count - 1) + 1)
        return [column * dimensions[0] * 1.2, -row * dimensions[1] * 1.2, 0.]

    @staticmethod
    def _unprintable_origin(count: int, dimensions: Sequence[float]) -> list[float]:
        columns = math.isqrt(count - 1) + 1
        index = count
        if count == columns ** 2:
            index, columns = count + columns - 1, columns + 1
        row, column = divmod(index, columns)
        return [column * dimensions[0] * 1.2, -row * dimensions[1] * 1.2, 0.]

    def _parked_tree(self) -> ET.Element:
        payload = self.data.get(PARKED)
        if payload is None:
            return ET.Element('parked_instances')
        tree, _bindings = _read_xml(payload)
        return tree

    def _write_parked(self, root: ET.Element) -> None:
        if len(_children(root, 'model_instance')) == 0:
            self.data.pop(PARKED, None)
        else:
            self.data[PARKED] = _xml_bytes(root)

    def _identify_seed(self, parked: ET.Element) -> int:
        values = [_metadata(member).get('identify_id', '0') for tree in (self.settings, parked)
                  for member in tree.iter() if _local(member.tag) == 'model_instance']
        return max(map(int, values), default=0)

    def _grid_updates(self, old_plates: list[ET.Element], new_plates: list[ET.Element]) -> list[tuple[ET.Element, list[float]]]:
        items = self._instance_index()
        changes = []
        dimensions: tuple[float, float] | None = None
        for new_index, plate in enumerate(new_plates):
            if plate not in old_plates or not _children(plate, 'model_instance'):
                continue
            old_index = old_plates.index(plate)
            if (old_index, math.isqrt(len(old_plates) - 1)) == (new_index, math.isqrt(len(new_plates) - 1)):
                continue
            dimensions = self._bed_dimensions() if dimensions is None else dimensions
            before = self._plate_origin(old_index, len(old_plates), dimensions)
            after = self._plate_origin(new_index, len(new_plates), dimensions)
            offset = [end - start for start, end in zip(before, after)]
            for member in _children(plate, 'model_instance'):
                fields = _metadata(member)
                item = items.get((int(fields['object_id']), int(fields['instance_id'])))
                if item is None:
                    raise ValueError('Plate metadata references an unknown instance')
                changes.append((item, list(Affine.parse(item.get('transform')).shift(offset).values)))
        return changes

    @staticmethod
    def _apply_placements(updates: Sequence[tuple[ET.Element, list[float]]]) -> None:
        for element, transform in updates:
            element.set('transform', Affine.of(transform).serialize())

    def _new_plate(self, number: int, name: str = '') -> ET.Element:
        plate = ET.Element('plate')
        source = _child(self.settings, 'plate')
        for key, value in _metadata(source).items():
            if key in ('filament_maps', 'filament_map_mode', 'bed_type', 'curr_bed_type', 'print_sequence', 'spiral_mode'):
                _set_metadata(plate, key, value)
        for key, value in {'plater_id': str(number), 'plater_name': name, 'locked': 'false'}.items():
            _set_metadata(plate, key, value)
        return plate

    def add_plate(self, name: str = '') -> int:
        if not isinstance(name, str):
            raise ValueError('Plate name must be a string')
        old = _children(self.settings, 'plate')
        if len(old) >= 36:
            raise ValueError('Bambu Studio supports at most 36 plates')
        added = self._new_plate(len(old) + 1, name)
        changes = self._grid_updates(old, [*old, added])
        self.settings.append(added)
        self._apply_placements(changes)
        return len(old) + 1

    def _custom_gcode_after_plate_delete(self, plate: int, mapping: dict[int, int]) -> bytes | None:
        if CUSTOM_GCODE not in self.data:
            return None
        root, bindings = _read_xml(self.data[CUSTOM_GCODE])
        before = _tree_state(root)
        entries = _children(root, 'plate')
        if not entries:
            return b'' if plate == 1 else None
        for node in entries:
            info = _child(node, 'plate_info')
            if info is None:
                continue
            old_id = int(info.attrib['id'])
            if old_id == plate:
                root.remove(node)
            elif old_id in mapping:
                info.set('id', str(mapping[old_id]))
        return _xml_bytes(root, bindings) if _tree_state(root) != before else None

    def _store_custom_gcode(self, data: bytes | None) -> None:
        if data is not None:
            if data:
                self.data[CUSTOM_GCODE] = data
            else:
                self.data.pop(CUSTOM_GCODE, None)

    def delete_plate(self, plate: int) -> DeletePlateRecord:
        target = self._plate(plate)
        old = _children(self.settings, 'plate')
        if len(old) == 1:
            raise ValueError('The last plate cannot be deleted')
        new = [node for node in old if node is not target]
        mapping = {int(_metadata(node).get('plater_id', old.index(node) + 1)): ordinal for ordinal, node in enumerate(new, 1)}
        changes = self._grid_updates(old, new)
        codes = self._custom_gcode_after_plate_delete(plate, mapping)
        parked = self._parked_tree()
        members = _children(target, 'model_instance')
        if members:
            dimensions = self._bed_dimensions()
            origin = self._plate_origin(old.index(target), len(old), dimensions)
            parking = self._unprintable_origin(len(new), dimensions)
            offset = [b - a for a, b in zip(origin, parking)]
            index = self._instance_index()
            for member in members:
                identity = _metadata(member)
                key = int(identity['object_id']), int(identity['instance_id'])
                if key not in index:
                    raise ValueError('Plate metadata references an unknown instance')
                item = index[key]
                changes.append((item, list(Affine.parse(item.get('transform')).shift(offset).values)))
                parked.append(copy.deepcopy(member))
        with self._transaction():
            self.settings.remove(target)
            for node in new:
                _set_metadata(node, 'plater_id', mapping[int(_metadata(node).get('plater_id', old.index(node) + 1))])
            self._apply_placements(changes)
            self._write_parked(parked)
            self._store_custom_gcode(codes)
            for tower_key in ('wipe_tower_x', 'wipe_tower_y'):
                values = self.config.get(tower_key)
                if isinstance(values, list) and old.index(target) < len(values):
                    values.pop(old.index(target))
        return {'deleted': True, 'plate': plate, 'count': len(new), 'objects_moved_off_plate': len(members)}

    @staticmethod
    def _membership_key(member: ET.Element) -> tuple[int, int]:
        fields = _metadata(member)
        return int(fields['object_id']), int(fields['instance_id'])

    def move_object_to_plate(self, object_id: int | str, plate: int, instance_id: int | None = None) -> None:
        self._object(object_id)
        destination = self._plate(plate)
        plates = _children(self.settings, 'plate')
        items = self._items(object_id)
        selection = range(len(items)) if instance_id is None else [instance_id]
        if any(type(index) is not int or not 0 <= index < len(items) for index in selection):
            raise ValueError(f'Unknown instance ID: {instance_id}')
        dimensions = self._bed_dimensions()
        target_origin = self._plate_origin(plates.index(destination), len(plates), dimensions)
        parked = self._parked_tree()
        next_identity = self._identify_seed(parked)
        moves = []
        for ordinal in selection:
            key = int(object_id), ordinal
            matches = [(source, member) for source in plates for member in _children(source, 'model_instance') if self._membership_key(member) == key]
            if len(matches) > 1:
                raise ValueError('An instance cannot belong to more than one plate')
            pose = Affine.parse(items[ordinal].get('transform'))
            source: ET.Element | None
            if matches:
                source, member = matches[0]
                old_origin = self._plate_origin(plates.index(source), len(plates), dimensions)
                pose = pose.shift([b - a for a, b in zip(old_origin, target_origin)])
            else:
                source = parked
                found = next((entry for entry in _children(parked, 'model_instance') if self._membership_key(entry) == key), None)
                if found is None:
                    source = None
                    next_identity += 1
                    member = self._new_membership(key, next_identity)
                else:
                    member = found
                translation = (target_origin[0] + dimensions[0] / 2, target_origin[1] + dimensions[1] / 2, pose.values[11])
                pose = Affine(pose.values[:9] + translation)
            moves.append((source, member, items[ordinal], pose))
        for source, member, item, pose in moves:
            if source is destination:
                continue
            if source is not None:
                source.remove(member)
            destination.append(member)
            item.set('transform', pose.serialize())
        self._write_parked(parked)

    @staticmethod
    def _new_membership(key: tuple[int, int], identify: int) -> ET.Element:
        member = ET.Element('model_instance')
        for name, value in zip(('object_id', 'instance_id', 'identify_id'), (*key, identify)):
            _set_metadata(member, name, value)
        return member

    def apply_native_layout(self, layout: JsonValue, deltas: dict[tuple[int, int], list[float]]) -> LayoutResult:
        with self._transaction():
            return self._apply_native_layout(layout, deltas)

    def _apply_native_layout(self, layout: JsonValue, deltas: dict[tuple[int, int], list[float]]) -> LayoutResult:
        if not isinstance(layout, dict):
            raise ValueError('Native layout needs plate_count 1..36, instance records and WORLD deltas')
        count, entries = layout.get('plate_count'), layout.get('instances')
        if type(count) is not int or not 1 <= count <= 36 or not isinstance(entries, list) or not isinstance(deltas, dict):
            raise ValueError('Native layout needs plate_count 1..36, instance records and WORLD deltas')
        items = self._instance_index()
        targets: dict[tuple[int, int], int] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError('Native layout instances must be objects')
            oid, ordinal, raw_plate = entry.get('object_id'), entry.get('instance_id'), entry.get('plate')
            if type(oid) is not int or type(ordinal) is not int or type(raw_plate) is not int:
                raise ValueError('Native layout instance IDs and plate must be integers')
            key = oid, ordinal
            if key not in items or key in targets or not 1 <= raw_plate <= count:
                raise ValueError('Native layout contains an unknown/duplicate instance or plate')
            targets[key] = raw_plate
        if set(targets) != set(deltas):
            raise ValueError('Native layout memberships must match the WORLD delta identities')
        poses: dict[tuple[int, int], Affine] = {}
        try:
            for key in targets:
                poses[key] = Affine.parse(items[key].get('transform')).then(Affine.of(deltas[key]))
        except (ValueError, TypeError, OverflowError):
            raise ValueError('Native WORLD deltas must contain 12 finite values') from None
        previous = _children(self.settings, 'plate')
        if [row['plate'] for row in self.list_plates()] != list(range(1, len(previous) + 1)):
            raise ValueError('Native layout requires contiguous 1-based source plate IDs')
        if any(row['locked'] for row in self.list_plates()[count:]):
            raise ValueError('Native layout cannot remove a locked source plate')
        parked = self._parked_tree()
        identify = self._identify_seed(parked)
        retained: dict[tuple[int, int], ET.Element] = {}
        for plate in [*previous, parked]:
            locked = _metadata(plate).get('locked', 'false').lower() in ('true', '1')
            for member in _children(plate, 'model_instance'):
                key = self._membership_key(member)
                if key not in targets:
                    continue
                if key in retained or locked:
                    raise ValueError('Native layout cannot move a locked or multiply assigned instance')
                retained[key] = member
                plate.remove(member)
        if any(_children(plate, 'model_instance') for plate in previous[count:]):
            raise ValueError('Native layout would remove a plate containing unselected instances')
        following = previous[:count] + [self._new_plate(i) for i in range(len(previous) + 1, count + 1)]
        reflow = self._grid_updates(previous, following)
        for number in reversed(range(count + 1, len(previous) + 1)):
            self._store_custom_gcode(self._custom_gcode_after_plate_delete(number, {i: i for i in range(1, count + 1)}))
        for plate in previous[count:]:
            self.settings.remove(plate)
        for plate in following[len(previous):]:
            self.settings.append(plate)
        self._apply_placements(reflow)
        for key, number in targets.items():
            recovered_member = retained.get(key)
            if recovered_member is None:
                identify += 1
                recovered_member = self._new_membership(key, identify)
            following[number - 1].append(recovered_member)
            items[key].set('transform', poses[key].serialize())
        self._write_parked(parked)
        if count < len(previous):
            for name in ('wipe_tower_x', 'wipe_tower_y'):
                values = self.config.get(name)
                if isinstance(values, list):
                    self.config[name] = values[:count]
        return {'plate_count': count, 'changed_instances': len(targets)}

    def transform_object(self, object_id: int | str, translate: Sequence[float] | None = None, rotate: Sequence[float] | None = None, scale: Sequence[float] | None = None, instance_id: int | None = None) -> None:
        self._object(object_id)
        offset = _xyz(translate, (0., 0., 0.))
        delta = Affine.pose_delta(_xyz(scale, (1., 1., 1.)), _xyz(rotate, (0., 0., 0.)))
        items = self._items(object_id)
        if instance_id is not None:
            if type(instance_id) is not int or not 0 <= instance_id < len(items):
                raise ValueError(f'Unknown instance ID: {instance_id}')
            items = items[instance_id:instance_id + 1]
        changes = []
        for item in items:
            before = Affine.parse(item.get('transform'))
            linear = Affine(before.values[:9] + (0., 0., 0.)).then(delta)
            after = Affine(linear.values[:9] + before.values[9:]).shift(offset)
            changes.append((item, list(after.values)))
        self._apply_placements(changes)

    def _heights(self) -> HeightStore:
        return HeightStore(self.data, self._root_ids())

    def _read_profiles(self) -> dict[int, list[float]]:
        return self._heights().profiles()

    def _write_profiles(self, profiles: dict[int, list[float]]) -> None:
        self._heights().write_profiles(profiles)

    def get_layer_profile(self, object_id: int | str) -> list[float]:
        root = self._object(object_id)
        profiles = self._heights().profiles()
        return list(profiles.get(int(root.attrib['id']), ()))

    def set_layer_profile(self, object_id: int | str, profile: list[float]) -> None:
        self._object(object_id)
        values = HeightStore.validate_profile(profile)
        profiles = self._read_profiles()
        profiles[int(object_id)] = values
        self._write_profiles(profiles)

    def _range_tree(self) -> tuple[ET.Element, list[tuple[str, str]]]:
        return self._heights().ranges()

    def _write_ranges(self, root: ET.Element, namespaces: Sequence[tuple[str, str]]) -> None:
        self._heights().write_ranges(root, namespaces)

    def set_height_range(self, object_id: int | str, min_z: float | None = None, max_z: float | None = None, layer_height: float | None = None, clear: bool = False) -> None:
        self._object(object_id)
        self._heights().set_band(int(object_id), min_z, max_z, layer_height, clear)

    def reset_layer_height(self, object_id: int | str) -> None:
        self._object(object_id)
        profiles = self._read_profiles()
        profiles.pop(int(object_id), None)
        self.set_height_range(object_id, clear=True)
        self._write_profiles(profiles)

    def _remap_ranges(self, old_ids: list[int], clone: tuple[int, int] | None = None) -> None:
        self._heights().remap(old_ids, clone)

    @staticmethod
    def _renew_uuids(element: ET.Element) -> None:
        for node in element.iter():
            for attribute in tuple(node.attrib):
                if _local(attribute).casefold() == 'uuid':
                    node.set(attribute, str(uuid.uuid4()))

    def duplicate_object(self, object_id: int | str) -> int:
        original = self._object(object_id)
        previous = self._root_ids()
        profiles = self._read_profiles()
        new_id = self._next_resource_id()
        duplicate = copy.deepcopy(original)
        duplicate.set('id', str(new_id))
        mesh = _child(duplicate, 'mesh')
        if mesh is not None:
            duplicate.remove(mesh)
            components = ET.SubElement(duplicate, f'{{{CORE}}}components')
            ET.SubElement(components, f'{{{CORE}}}component', {'objectid': str(object_id)})
        self._renew_uuids(duplicate)
        build = _child(self.model, 'build')
        assert build is not None
        source_items = self._items(object_id)
        with self._transaction():
            self._resources().append(duplicate)
            for item in source_items:
                instance = copy.deepcopy(item)
                instance.set('objectid', str(new_id))
                self._renew_uuids(instance)
                build.append(instance)
            settings = self._object_settings(object_id)
            if settings is not None:
                settings = copy.deepcopy(settings)
                settings.set('id', str(new_id))
                self.settings.append(settings)
            parked = self._parked_tree()
            identify = self._identify_seed(parked)
            for plate in [*_children(self.settings, 'plate'), parked]:
                for member in _children(plate, 'model_instance'):
                    if self._membership_key(member)[0] == int(object_id):
                        extra = copy.deepcopy(member)
                        identify += 1
                        _set_metadata(extra, 'object_id', new_id)
                        _set_metadata(extra, 'identify_id', identify)
                        plate.append(extra)
            self._write_parked(parked)
            assemble = _child(self.settings, 'assemble')
            if assemble is not None:
                for item in _children(assemble, 'assemble_item'):
                    if item.get('object_id') == str(object_id):
                        extra = copy.deepcopy(item)
                        extra.set('object_id', str(new_id))
                        assemble.append(extra)
            self._remap_ranges(previous, (int(object_id), new_id))
            if int(object_id) in profiles:
                profiles[new_id] = profiles[int(object_id)].copy()
            self._write_profiles(profiles)
        return new_id

    def delete_object(self, object_id: int | str) -> None:
        target = self._object(object_id)
        previous = self._root_ids()
        profiles = self._read_profiles()
        build = _child(self.model, 'build')
        assert build is not None
        with self._transaction():
            for item in self._items(object_id):
                build.remove(item)
            needed = False
            for root in _children(self._resources(), 'object'):
                if root is target:
                    continue
                for component in root.iter():
                    if _local(component.tag) != 'component' or component.get('objectid') != str(object_id):
                        continue
                    path = component.get(f'{{{PRODUCTION}}}path', '/' + self._model_name)
                    if relationship_target('_rels/.rels', path) == self._model_name:
                        needed = True
            if not needed:
                self._resources().remove(target)
            settings = self._object_settings(object_id)
            if settings is not None:
                self.settings.remove(settings)
            parked = self._parked_tree()
            for plate in [*_children(self.settings, 'plate'), parked]:
                for member in _children(plate, 'model_instance'):
                    if self._membership_key(member)[0] == int(object_id):
                        plate.remove(member)
            self._write_parked(parked)
            assemble = _child(self.settings, 'assemble')
            if assemble is not None:
                for item in _children(assemble, 'assemble_item'):
                    if item.get('object_id') == str(object_id):
                        assemble.remove(item)
            self._remap_ranges(previous)
            profiles.pop(int(object_id), None)
            self._write_profiles(profiles)

    def _output_tree(self, output: dict[str, bytes], name: str, root: ET.Element) -> None:
        if self._initial_xml.get(name) != _tree_state(root):
            output[name] = _xml_bytes(root, self._namespaces.get(name, ()))

    def save(self, path: Path) -> None:
        output = self.data.copy()
        if self.config != self._initial_config:
            output[CONFIG] = (json.dumps(self.config, indent=2, ensure_ascii=False) + '\n').encode()
        self._output_tree(output, self._model_name, self.model)
        # The previous wrapper's v2 profile is not a Bambu format. Migrate it
        # on output without altering the in-memory source inventory.
        if output.get(PROFILE, b'').lstrip(b'\xef\xbb\xbf').startswith(b'layer_heights_profile_format_version='):
            HeightStore(output, self._root_ids()).write_profiles(self._read_profiles())
        output, settings = without_slice_outputs(output, self.settings, set(self._infos).difference(output))
        self._output_tree(output, SETTINGS, settings)
        self._package.write(Path(path), output)
