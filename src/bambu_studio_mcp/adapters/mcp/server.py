"""Bind the published tool declarations to Bambu application capabilities."""
from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Sequence
from functools import wraps
from pathlib import Path
from typing import Protocol, cast

from fastmcp import FastMCP

from ...bootstrap import build_editor
from ...domain.types.json import JsonObject, JsonValue, json_object
from ...editor import Editor
from ...service import Slicer
from .contracts import LegacyErrors, ToolValue, register_resource, register_tool
from .interface import BambuTools

_OPERATIONS = frozenset({
    'set_object_config', 'add_modifier', 'duplicate_object', 'delete_object',
    'transform_object', 'arrange_plate', 'auto_orient', 'add_plate', 'delete_plate',
    'move_object_to_plate', 'set_layer_height', 'set_height_range',
})
_CATALOG = frozenset({'list_presets', 'get_preset_config', 'edit_preset',
                       'rename_preset', 'delete_preset', 'describe_setting', 'search_settings'})
_SLICING = frozenset({'slice_3mf', 'slice_project', 'apply_and_slice'})


class _Capability(Protocol):
    def __call__(self, *values: JsonValue, **named: JsonValue) -> ToolValue: ...


class _Declaration(Protocol):
    __name__: str
    def __call__(self, *values: JsonValue, **named: JsonValue) -> ToolValue | Awaitable[ToolValue]: ...


class _Invocation(Protocol):
    async def __call__(self, *positional: JsonValue, **named: JsonValue) -> ToolValue: ...


class _Dispatcher:
    def __init__(self, slicer: Slicer, editor: Editor) -> None:
        self.slicer, self.editor = slicer, editor

    async def invoke(self, name: str, arguments: JsonObject) -> ToolValue:
        values = dict(arguments)
        if name in _OPERATIONS:
            project_id = values.pop('project_id')
            revision = values.pop('expected_sha256')
            if not isinstance(project_id, str) or not (revision is None or isinstance(revision, str)):
                raise ValueError('Invalid project identity')
            if name in {'arrange_plate', 'auto_orient'} and values.get('plate') is None:
                values.pop('plate', None)
            operation = {'operation': name, **values}
            return await asyncio.to_thread(self.editor.edit_project, project_id, [operation], revision)
        if name == 'engine_status':
            values['check_remote'] = values.pop('check_updates')
            target: object = self.slicer.engine
            method = 'status'
        else:
            target = (self.editor.catalog if name in _CATALOG else self.slicer
                      if name in {'slice_3mf', 'recover_3mf'} else self.editor)
            method = name
        capability = cast(_Capability, getattr(target, method))
        if name in _SLICING:
            return await self.slicer.run_slice(capability, **values)
        return await asyncio.to_thread(capability, **values)


def _bind(declaration: _Declaration, dispatch: _Dispatcher) -> _Invocation:
    signature = inspect.signature(declaration)

    @wraps(declaration)
    async def invoke(*positional: JsonValue, **named: JsonValue) -> ToolValue:
        bound = signature.bind(*positional, **named)
        bound.apply_defaults()
        return await dispatch.invoke(declaration.__name__, json_object(dict(bound.arguments)))

    return invoke


def build_server(slicer: Slicer, preset_dirs: Sequence[Path] = ()) -> FastMCP[None]:
    protocol = FastMCP[None]('Bambu Studio MCP', instructions=(
        'Create, edit and slice private Bambu Studio 3MF revisions with the explicitly bound native engine. '
        'Preserve source files, painting, geometry, IDs and installed presets. '
        'After every edit, inspect the revision and re-slice. Preset changes are private artifact drafts. '
        'No GUI, printer control, automatic engine updates or global preset mutation.'
    ))
    protocol.add_middleware(LegacyErrors())
    dispatch = _Dispatcher(slicer, build_editor(slicer, preset_dirs))
    for name, member in vars(BambuTools).items():
        if isinstance(member, staticmethod):
            declaration = cast(_Declaration, getattr(BambuTools, name))
            register_tool(protocol)(_bind(declaration, dispatch))

    @register_resource(protocol, 'slicer://jobs/{job_id}/manifest')
    def manifest(job_id: str) -> str:
        record = slicer.job(job_id)
        return json.dumps(record, ensure_ascii=False)

    @register_resource(protocol, 'slicer://jobs/{job_id}/props')
    def props(job_id: str) -> str:
        record = slicer.job(job_id)
        if record.get('job_id') != job_id:
            raise ValueError('Invalid generated job ID')
        return (slicer.root / 'runs' / job_id / 'props.json').read_text(encoding='utf-8')

    @register_resource(protocol, 'slicer://jobs/{job_id}/plates/{plate}/lines/{start}/{count}')
    def gcode_lines(job_id: str, plate: int, start: int, count: int) -> str:
        record = slicer.read_lines(job_id, int(plate), int(start), int(count))
        return json.dumps(record, ensure_ascii=False)

    return protocol
