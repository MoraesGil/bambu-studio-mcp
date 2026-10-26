"""Bambu component traversal and isolated native-operation input creation."""
from __future__ import annotations

import copy
import math
import posixpath
from collections import deque
from collections.abc import Sequence
from pathlib import Path
from typing import TypeAlias
from xml.etree import ElementTree as ET

from .affine import Affine
from .heights import HeightStore
from .package import BambuPackage, PRODUCTION, SETTINGS
from .xml import child, children, metadata, read_xml, set_metadata, xml_bytes

Point: TypeAlias = tuple[float, ...]
Triangle: TypeAlias = tuple[Point, ...]
InstanceKey: TypeAlias = tuple[int, int]


def matrix(value: str | None) -> list[float]:
    return list(Affine.parse(value).values)


def point(point: Sequence[float], matrix: Sequence[float]) -> Point:
    return Affine.of(matrix).apply(point)


def compose(first: Sequence[float], second: Sequence[float]) -> list[float]:
    return list(Affine.of(first).then(Affine.of(second)).values)


def read_metadata(node: ET.Element) -> dict[str, str]:
    return metadata(node)


class Geometry:
    """Read-only geometry view with Bambu's breadth-first volume ordering."""

    def __init__(self, project: Path) -> None:
        self._package = BambuPackage(Path(project))
        self.data = self._package.members.copy()
        self.models: dict[str, ET.Element] = {}
        self._bindings: dict[str, list[tuple[str, str]]] = {}
        for name, payload in self.data.items():
            if name.startswith('3D/') and name.endswith('.model'):
                self.models[name], self._bindings[name] = read_xml(payload)
        self.root_name = self._package.root_name
        if self.root_name not in self.models:
            raise ValueError('The source project has no root 3MF model')
        if any(model.get('unit', 'millimeter') != 'millimeter' for model in self.models.values()):
            raise ValueError('Native transform recovery currently supports millimeter 3MF units')
        self.model = self.models[self.root_name]
        self.objects = {name: {int(node.attrib['id']): node for node in children(child(model, 'resources'), 'object')}
                        for name, model in self.models.items()}
        build = child(self.model, 'build')
        if build is None or len(build) == 0:
            raise ValueError('The source project has no build instances')
        self.build = build
        self.items: list[tuple[InstanceKey, ET.Element]] = []
        self.instances: dict[int, list[list[float]]] = {}
        for item in children(build, 'item'):
            if item.get(f'{{{PRODUCTION}}}path'):
                raise ValueError('External build-item roots are unsupported for transform recovery')
            oid = int(item.attrib['objectid'])
            if oid not in self.objects[self.root_name]:
                raise ValueError('The source build references a missing root object')
            poses = self.instances.setdefault(oid, [])
            self.items.append(((oid, len(poses)), item))
            poses.append(matrix(item.get('transform')))
        self.settings, bindings = read_xml(self.data.get(SETTINGS, b'<config/>'))
        self._bindings[SETTINGS] = bindings
        self.object_settings = {int(node.attrib['id']): node for node in children(self.settings, 'object')}

    def _component_model(self, name: str, component: ET.Element) -> str:
        path = component.get(f'{{{PRODUCTION}}}path')
        if path is None:
            return name
        target = posixpath.normpath(path.lstrip('/'))
        if target not in self.models and not path.startswith('/'):
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), path))
        if target.startswith('../') or target not in self.models:
            raise ValueError('Missing or invalid 3MF component model path')
        return target

    def mesh(self, oid: int) -> list[Triangle]:
        config = self.object_settings.get(oid)
        parts = {int(part.attrib['id']): part for part in children(config, 'part')}
        pending = deque([(self.root_name, oid, Affine(), frozenset[tuple[str, int]]())])
        output: list[Triangle] = []
        while pending:
            name, identifier, pose, ancestors = pending.popleft()
            identity = name, identifier
            if identity in ancestors or len(ancestors) >= 10:
                raise ValueError('Cyclic or excessively nested 3MF component graph')
            obj = self.objects.get(name, {}).get(identifier)
            if obj is None:
                raise ValueError('Missing 3MF component object')
            components = children(child(obj, 'components'), 'component')
            if components:
                for component in components:
                    target = self._component_model(name, component)
                    local_pose = Affine.parse(component.get('transform')).then(pose)
                    pending.append((target, int(component.attrib['objectid']), local_pose, ancestors | {identity}))
                continue
            part = parts.get(identifier)
            fields = metadata(part)
            kind = fields.get('volume_type', fields.get('part_type', part.get('subtype', 'normal_part') if part is not None else 'normal_part'))
            if kind != 'normal_part':
                continue
            mesh = child(obj, 'mesh')
            vertices = [tuple(float(vertex.attrib[axis]) for axis in ('x', 'y', 'z'))
                        for vertex in children(child(mesh, 'vertices'), 'vertex')]
            faces = [tuple(int(face.attrib[key]) for key in ('v1', 'v2', 'v3'))
                     for face in children(child(mesh, 'triangles'), 'triangle')]
            if not vertices or not faces:
                raise ValueError('Root object has an empty normal-part mesh')
            if any(not math.isfinite(coordinate) for vertex in vertices for coordinate in vertex):
                raise ValueError('Non-finite source mesh coordinate')
            if any(index < 0 or index >= len(vertices) for face in faces for index in face):
                raise ValueError('Invalid source mesh vertex index')
            transformed = [pose.apply(vertex) for vertex in vertices]
            output.extend(tuple(transformed[index] for index in face) for face in faces)
        if not output:
            raise ValueError('Root object has no normal-part geometry')
        return output

    def _plate_members(self, node: ET.Element) -> set[InstanceKey]:
        keys = []
        for member in children(node, 'model_instance'):
            fields = metadata(member)
            keys.append((int(fields['object_id']), int(fields['instance_id'])))
        valid = {key for key, _ in self.items}
        if len(set(keys)) != len(keys) or not set(keys).issubset(valid):
            raise ValueError('Plate has invalid or ambiguous instance references')
        return set(keys)

    def locked_instances(self) -> dict[InstanceKey, int]:
        locked: dict[InstanceKey, int] = {}
        for ordinal, plate in enumerate(children(self.settings, 'plate'), 1):
            fields = metadata(plate)
            if fields.get('locked', 'false').lower() not in ('true', '1'):
                continue
            for key in self._plate_members(plate):
                if key in locked:
                    raise ValueError('Locked plate has invalid or ambiguous instance references')
                locked[key] = int(fields.get('plater_id', ordinal))
        return locked

    def selected(self, plate: int, omit_locked: bool = False) -> set[InstanceKey]:
        if plate == 0:
            result = {key for key, _ in self.items}
            if omit_locked:
                result.difference_update(self.locked_instances())
                if not result:
                    raise ValueError('All source plate instances are locked')
            return result
        candidates = [node for ordinal, node in enumerate(children(self.settings, 'plate'), 1)
                      if int(metadata(node).get('plater_id', ordinal)) == plate]
        if len(candidates) != 1:
            raise ValueError('The requested plate is missing or ambiguous')
        target = candidates[0]
        if metadata(target).get('locked', 'false').lower() in ('true', '1'):
            raise ValueError('The requested plate is locked; unlock it explicitly before editing')
        result = self._plate_members(target)
        if not result:
            raise ValueError('The requested plate is empty or references invalid instances')
        return result

    def native_input(self, target: Path, selected: set[InstanceKey]) -> dict[int, list[InstanceKey]]:
        """Create independent scratch trees; never consume the geometry view."""
        known = {key for key, _ in self.items}
        if not selected or not selected.issubset(known):
            raise ValueError('Native selection must identify existing build instances')
        model, settings = copy.deepcopy((self.model, self.settings))
        build = child(model, 'build')
        assert build is not None
        groups: dict[int, list[InstanceKey]] = {}
        ordinals: dict[InstanceKey, int] = {}
        for (key, _), item in zip(self.items, children(build, 'item')):
            if key not in selected:
                build.remove(item)
            else:
                group = groups.setdefault(key[0], [])
                ordinals[key] = len(group)
                group.append(key)
        for obj in children(child(model, 'resources'), 'object'):
            obj.set('name', 'mcp_object_' + obj.attrib['id'])
        for obj in children(settings, 'object'):
            _name = 'mcp_object_' + obj.attrib['id']
            set_metadata(obj, 'name', _name)
        for plate in children(settings, 'plate'):
            for member in children(plate, 'model_instance'):
                fields = metadata(member)
                key = int(fields['object_id']), int(fields['instance_id'])
                if key in selected:
                    set_metadata(member, 'instance_id', ordinals[key])
                else:
                    plate.remove(member)
            if selected != known and not children(plate, 'model_instance'):
                set_metadata(plate, 'locked', 'true')
        for assembly in children(settings, 'assemble'):
            for item in list(assembly):
                key = int(item.attrib['object_id']), int(item.attrib['instance_id'])
                if key not in selected:
                    assembly.remove(item)
                else:
                    item.set('instance_id', str(ordinals[key]))
        output = self.data.copy()
        output[self.root_name] = xml_bytes(model, self._bindings[self.root_name])
        output[SETTINGS] = xml_bytes(settings, self._bindings[SETTINGS])
        previous_roots = list(dict.fromkeys(key[0] for key, _ in self.items))
        selected_roots = list(groups)
        profiles = HeightStore(self.data, previous_roots).profiles()
        height_metadata = HeightStore(output, selected_roots)
        height_metadata.remap(previous_roots)
        height_metadata.write_profiles({oid: values for oid, values in profiles.items() if oid in groups})
        self._package.write(target, output)
        return groups
