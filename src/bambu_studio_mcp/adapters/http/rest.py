"""REST projection of Bambu jobs with verified, request-owned downloads."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import tempfile
import zipfile
from collections.abc import Awaitable, Callable, Iterator, Mapping
from functools import wraps
from importlib.metadata import version
from importlib.resources import files
from typing import BinaryIO, Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from ...application.services.lines import bounded_lines
from ...domain.types.json import JsonObject, JsonValue, decode_object, json_object
from ...service import Slicer

PREFIX = '/api/v1'
PRIVATE = {'Cache-Control': 'private, no-store'}
Endpoint = Callable[[Request], Awaitable[Response]]
ArtifactKind = Literal['gcode', 'source', 'props']


class SliceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    input_3mf: str = Field(min_length=1)
    plate: int = Field(default=0, ge=0)
    force: bool = False
    allow_newer_file: bool = True


class _RequestFailure(Exception):
    def __init__(self, status: int, detail: JsonObject) -> None:
        super().__init__(detail)
        self.status_code, self.detail = status, detail


def _fail(status: int, code: str, message: str) -> NoReturn:
    raise _RequestFailure(status, dict(code=code, message=message))


def _endpoint(function: Endpoint) -> Endpoint:
    @wraps(function)
    async def guarded(request: Request) -> Response:
        try:
            return await function(request)
        except _RequestFailure as failure:
            return JSONResponse({'error': failure.detail}, failure.status_code, headers=PRIVATE)
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile) as failure:
            if isinstance(failure, FileNotFoundError):
                status, code = 404, 'not_found'
            elif isinstance(failure, (ValueError, zipfile.BadZipFile, IsADirectoryError)):
                status, code = 400, 'invalid_project'
            elif isinstance(failure, TimeoutError):
                status, code = 504, 'native_timeout'
            elif isinstance(failure, RuntimeError):
                status, code = 502, 'native_failed'
            else:
                status, code = 503, 'service_io'
            return JSONResponse({'error': {'code': code, 'message': str(failure)}}, status, headers=PRIVATE)
    return guarded


def _text(value: JsonValue) -> str:
    if isinstance(value, str):
        return value
    _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')


def _plates(job: JsonObject) -> list[JsonObject]:
    entries = job.get('plates')
    if not isinstance(entries, list):
        _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')
    return [json_object(entry) for entry in entries]


def _job(slicer: Slicer, job_id: str) -> JsonObject:
    if re.fullmatch('[0-9a-f]{32}', job_id) is None:
        _fail(400, 'invalid_job', 'Job ID must contain 32 lowercase hexadecimal characters')
    manifest = slicer.root / 'runs' / job_id / 'result.json'
    if manifest != manifest.resolve():
        _fail(409, 'artifact_conflict', 'Job paths must not contain symlinks')
    if not manifest.is_file():
        _fail(404, 'not_found', 'Completed job is unavailable')
    try:
        job = json_object(slicer.job(job_id))
    except (OSError, ValueError, TypeError, KeyError):
        _fail(409, 'artifact_conflict', 'Stored job manifest is invalid')
    return job


def _integer(value: str, name: str) -> int:
    if re.fullmatch('[1-9][0-9]*', value) is None:
        _fail(400, 'invalid_range', f'{name} must be a positive integer')
    try:
        number = int(value)
    except ValueError:
        _fail(400, 'invalid_range', f'{name} is too large')
    return number


def _artifact(slicer: Slicer, job_id: str, kind: ArtifactKind,
              plate: int | None = None) -> tuple[BinaryIO, str, str, int]:
    record = _job(slicer, job_id)
    if kind == 'source':
        filename, digest = 'source.3mf', _text(json_object(record['source'])['sha256'])
    elif kind == 'props':
        filename, digest = 'props.json', _text(record['props_sha256'])
    else:
        matches = [item for item in _plates(record) if item['plate'] == plate]
        if not matches:
            _fail(404, 'not_found', 'Plate is not part of this job')
        filename, digest = f'plate_{plate}.gcode', _text(matches[0]['sha256'])
    source = slicer.root / 'runs' / job_id / filename
    if source != source.resolve() or not source.is_file():
        _fail(409, 'artifact_conflict', 'Retained artifact is missing or a symlink')
    snapshot = tempfile.TemporaryFile(dir=source.parent)
    try:
        try:
            stream = source.open('rb')
        except OSError:
            _fail(409, 'artifact_conflict', 'Retained artifact is unavailable')
        with stream:
            while True:
                try:
                    block = stream.read(1024 * 1024)
                except OSError:
                    _fail(409, 'artifact_conflict', 'Retained artifact is unreadable')
                if not block:
                    break
                snapshot.write(block)
        length = snapshot.tell()
        snapshot.seek(0)
        if hashlib.file_digest(snapshot, 'sha256').hexdigest() != digest:
            _fail(409, 'artifact_conflict', 'Retained artifact changed')
        snapshot.seek(0)
    except BaseException:
        snapshot.close()
        raise
    return snapshot, digest, filename, length


class _SnapshotResponse(StreamingResponse):
    def __init__(self, snapshot: BinaryIO, *, media_type: str, headers: Mapping[str, str]) -> None:
        self.snapshot = snapshot
        super().__init__(self._chunks(), media_type=media_type, headers=headers)

    def _chunks(self) -> Iterator[bytes]:
        yield from iter(lambda: self.snapshot.read(65536), b'')

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.snapshot.close()


def _linked(job: JsonObject) -> JsonObject:
    location = f'{PREFIX}/jobs/{job["job_id"]}'
    plate_links: JsonObject = {}
    for plate in _plates(job):
        path = f'{location}/plates/{plate["plate"]}'
        plate_links[str(plate['plate'])] = dict(gcode=path + '/gcode', lines=path + '/lines')
    links: JsonObject = dict(self=location, props=location + '/props', source_3mf=location + '/source',
                            plates=plate_links)
    return {**job, 'links': links}


def _openapi() -> JsonObject:
    declaration = files('bambu_studio_mcp').joinpath('data/rest_openapi.json').read_bytes()
    schema = decode_object(declaration, 'REST contract')
    schema['info'] = {**json_object(schema['info']), 'version': version('bambu-studio-mcp')}
    return schema


class _BambuAPI:
    def __init__(self, slicer: Slicer) -> None:
        self.slicer = slicer

    async def _verify(self) -> None:
        try:
            await asyncio.to_thread(self.slicer.engine.verify)
        except ValueError as failure:
            _fail(409, 'engine_conflict', str(failure))

    async def slice(self, request: Request) -> Response:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            _fail(400, 'invalid_json', 'Request body must contain valid JSON')
        try:
            selected = SliceRequest.model_validate(body)
        except ValidationError as failure:
            detail = failure.errors(include_input=False, include_url=False)
            return JSONResponse({'error': {'code': 'invalid_request', 'details': detail}}, 422, headers=PRIVATE)
        await self._verify()
        try:
            value = await self.slicer.run_slice(self.slicer.slice_3mf, selected.input_3mf,
                                                selected.plate, selected.force, selected.allow_newer_file)
        except ValueError:
            await self._verify()
            raise
        return JSONResponse(_linked(value), headers=PRIVATE)

    async def manifest(self, request: Request) -> Response:
        value = await asyncio.to_thread(_job, self.slicer, _text(request.path_params['job_id']))
        return JSONResponse(_linked(value), headers=PRIVATE)

    def download(self, kind: ArtifactKind) -> Endpoint:
        async def respond(request: Request) -> Response:
            number = _integer(_text(request.path_params['plate']), 'plate') if kind == 'gcode' else None
            snapshot, digest, filename, length = await asyncio.to_thread(
                _artifact, self.slicer, _text(request.path_params['job_id']), kind, number)
            headers = {**PRIVATE, 'ETag': f'"{digest}"'}
            if kind == 'props':
                with snapshot:
                    contents = snapshot.read()
                try:
                    json.loads(contents)
                except (ValueError, UnicodeDecodeError):
                    _fail(409, 'artifact_conflict', 'Stored properties are invalid')
                return Response(contents, media_type='application/json', headers=headers)
            headers['Content-Length'] = str(length)
            headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            return _SnapshotResponse(snapshot, headers=headers,
                                     media_type='text/x.gcode' if kind == 'gcode' else 'model/3mf')
        return respond

    async def lines(self, request: Request) -> Response:
        job_id = _text(request.path_params['job_id'])
        number = _integer(_text(request.path_params['plate']), 'plate')
        start = _integer(request.query_params.get('start', '1'), 'start')
        count = _integer(request.query_params.get('count', '200'), 'count')
        if count > 1000:
            _fail(400, 'invalid_range', 'count must be between 1 and 1000')
        snapshot, digest, _, _ = await asyncio.to_thread(_artifact, self.slicer, job_id, 'gcode', number)
        def read() -> JsonObject:
            with io.TextIOWrapper(snapshot, encoding='utf-8', errors='replace') as stream:
                result = bounded_lines(stream, job_id, number, start, count)
                return json_object(result)
        result = await asyncio.to_thread(read)
        return JSONResponse(result, headers={**PRIVATE, 'ETag': f'"{digest}"'})

    async def engine(self, request: Request) -> Response:
        await self._verify()
        try:
            result = await asyncio.to_thread(self.slicer.engine.status)
        except ValueError:
            await self._verify()
            raise
        return JSONResponse(result, headers=PRIVATE)

    async def openapi(self, request: Request) -> Response:
        return JSONResponse(_openapi(), headers=PRIVATE)


def rest_routes(slicer: Slicer) -> list[Route]:
    api = _BambuAPI(slicer)
    declarations: list[tuple[str, Endpoint, list[str]]] = [
        ('/slices', api.slice, ['POST']), ('/engine', api.engine, ['GET']),
        ('/openapi.json', api.openapi, ['GET']), ('/jobs/{job_id}', api.manifest, ['GET']),
        ('/jobs/{job_id}/props', api.download('props'), ['GET']),
        ('/jobs/{job_id}/source', api.download('source'), ['GET']),
        ('/jobs/{job_id}/plates/{plate}/gcode', api.download('gcode'), ['GET']),
        ('/jobs/{job_id}/plates/{plate}/lines', api.lines, ['GET']),
    ]
    return [Route(PREFIX + path, _endpoint(handler), methods=methods)
            for path, handler, methods in declarations]
