"""Apply measured Bambu operations to private revisions of the original graph."""
from __future__ import annotations

import json
import uuid
from contextlib import AbstractContextManager
from typing import Protocol

from ...application.contracts.projects import ProjectDocument
from ...domain.settings.values import integer, record
from ...domain.types.json import JsonObject, JsonValue, json_object
from ...engine import Engine
from ..storage.revisions import FilesystemRevisionRepository, StoredProjectDocument
from .layout import BAMBU_ADAPTIVE_SOURCE


class NativeHost(Protocol):
    @property
    def engine(self) -> Engine: ...
    @property
    def timeout(self) -> float: ...
    def native_session(self) -> AbstractContextManager[None]: ...


class BambuNativeEditing:
    def __init__(self, host: NativeHost, revisions: FilesystemRevisionRepository):
        self.host, self.revisions = host, revisions

    def execute(self, document: ProjectDocument, name: str, arguments: JsonObject,
                state: JsonObject) -> JsonValue:
        if not isinstance(document, StoredProjectDocument):
            raise ValueError('Native editing requires a stored 3MF document')
        if name == 'set_layer_height':
            return self._layer_height(document, arguments, state)
        if name not in {'arrange_plate', 'auto_orient'}:
            raise ValueError(f'Unsupported native edit: {name}')
        return self._layout(document, name, arguments, state)

    def _layer_height(self, document: StoredProjectDocument, arguments: JsonObject,
                      state: JsonObject) -> JsonValue:
        oid = integer(arguments['object_id'])
        mode = arguments['mode']
        if mode in ('reset', 'default', 'none'):
            document.project.reset_layer_height(oid)
            return None
        if mode != 'adaptive':
            raise ValueError('mode must be adaptive or reset')
        if self.host.engine.identity()['source_commit'] != BAMBU_ADAPTIVE_SOURCE:
            raise ValueError('Adaptive generator source commit differs from the engine; update and validate the MCP adapter explicitly')
        quality = arguments.get('quality', .5)
        if isinstance(quality, bool) or not isinstance(quality, (float, int)):
            raise ValueError('quality must be a number')
        from ...native_ops import adaptive_layer_profile
        staged = self.revisions.workspace(state) / ('adaptive-' + uuid.uuid4().hex + '.3mf')
        document.save(staged)
        with self.host.native_session():
            self.host.engine.verify()
            profile = adaptive_layer_profile(staged, oid, document.config, quality)
            self.host.engine.verify()
        document.project.set_layer_profile(oid, profile)
        return dict(object_id=oid, quality=quality, profile=list(profile),
                    generator_source_commit=BAMBU_ADAPTIVE_SOURCE)

    def _layout(self, document: StoredProjectDocument, name: str, arguments: JsonObject,
                state: JsonObject) -> JsonObject:
        from ...native_ops import native_deltas
        from ...project import compose_transform
        directory = self.revisions.workspace(state) / ('native-' + uuid.uuid4().hex)
        directory.mkdir(mode=0o700)
        source = directory / 'input.3mf'
        document.save(source)
        plate = integer(arguments.get('plate', state['selected_plate']))
        with self.host.native_session():
            deltas = native_deltas(self.host.engine, source, directory,
                                   arrange=name == 'arrange_plate', orient=name == 'auto_orient',
                                   plate=plate, timeout=self.host.timeout)
        audit = record(json.loads((directory / 'native_ops.json').read_text()))
        if name == 'arrange_plate' and plate == 0:
            result = json_object(document.project.apply_native_layout(audit.get('layout'), deltas))
        else:
            ordinals: dict[int, int] = {}
            for item in document.project.model.findall('.//{*}build/{*}item'):
                oid = int(item.attrib['objectid'])
                instance = ordinals.get(oid, 0)
                ordinals[oid] = instance + 1
                delta = deltas.get((oid, instance))
                if delta is not None:
                    current = [float(value) for value in item.get('transform', '1 0 0 0 1 0 0 0 1 0 0 0').split()]
                    updated = compose_transform(current, delta)
                    item.set('transform', ' '.join(format(value, '.12g') for value in updated))
            result = {'changed_instances': len(deltas)}
        result['diagnostics'] = str(directory)
        return result
