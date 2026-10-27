"""Immutable slice snapshots, manifest validation and verified artifact reuse."""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import TypeGuard
from xml.etree import ElementTree

from ...application.contracts.artifacts import GcodeLines
from ...application.contracts.slicing import SliceRequest, SliceWorkspace, SourceSnapshot
from ...application.services.lines import bounded_lines
from ...domain.entities.slicing import JobManifest, PlateRecord
from ...domain.types.json import JsonObject, JsonValue, decode_json, decode_object, json_object
from ..bambu.engine import file_hash


def write_json(path: Path, value: JsonObject) -> None:
    encoded = (json.dumps(json_object(value), ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _reject_post_process(value: JsonValue) -> None:
    pending = [value]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            if node.get('post_process') not in (None, '', [], ['']):
                raise ValueError('External post-processing is unsupported by the native slice contract')
            pending.extend(node.values())


def validate_project(path: Path) -> None:
    if path.suffix.casefold() != '.3mf':
        raise ValueError('Input must be a 3MF project')
    with zipfile.ZipFile(path, 'r') as package:
        names = package.namelist()
        if not any(name.startswith('3D/') and name.endswith('.model') for name in names):
            raise ValueError('The archive has no 3MF model; G-code alone is not a source project')
        for member in package.infolist():
            if not member.filename.endswith(('.json', '.config')):
                continue
            contents = package.read(member)
            try:
                decoded = decode_json(contents)
            except (json.JSONDecodeError, UnicodeDecodeError):
                try:
                    xml = ElementTree.fromstring(contents)
                except ElementTree.ParseError:
                    continue
                for element in xml.iter():
                    if element.attrib.get('key') == 'post_process' and element.attrib.get('value', '').strip():
                        raise ValueError('External post-processing is unsupported by the native slice contract')
            else:
                _reject_post_process(decoded)


def gcode_properties(path: Path) -> JsonObject:
    exported: JsonObject = {}
    in_config = False
    with path.open(errors='replace') as source:
        for raw in source:
            if 'CONFIG_BLOCK_START' in raw:
                in_config = True
                continue
            if 'CONFIG_BLOCK_END' in raw:
                in_config = False
                continue
            if in_config and raw.startswith(';'):
                key, separator, value = raw[1:].partition(' = ')
                if separator:
                    exported[key.strip()] = value.strip()
    return exported


def _manifest(value: JsonObject) -> TypeGuard[JobManifest]:
    required_strings = {'status', 'job_id', 'cache_key', 'props_path', 'props_sha256', 'manifest_uri', 'props_uri', 'log_path'}
    for key in required_strings:
        if not isinstance(value.get(key), str):
            return False
    if value.get('status') != 'ready' or type(value.get('cached')) is not bool or type(value.get('requested_plate')) is not int:
        return False
    for key in ('native_elapsed_seconds', 'request_elapsed_seconds'):
        duration = value.get(key)
        if type(duration) not in (int, float) or not isinstance(duration, (int, float)) or not math.isfinite(duration):
            return False
    source, flags, plates = value.get('source'), value.get('compatibility_flags'), value.get('plates')
    if not isinstance(source, dict) or any(not isinstance(source.get(key), str) for key in ('path', 'sha256')):
        return False
    if not isinstance(flags, dict) or type(flags.get('allow_newer_file')) is not bool:
        return False
    if not isinstance(value.get('engine'), dict) or not isinstance(value.get('engine_status'), dict):
        return False
    if not isinstance(plates, list) or not plates:
        return False
    seen: set[int] = set()
    for plate in plates:
        if not isinstance(plate, dict):
            return False
        number, size = plate.get('plate'), plate.get('size_bytes')
        if type(number) is not int or number <= 0 or number in seen or type(size) is not int or size <= 0:
            return False
        seen.add(number)
        if any(not isinstance(plate.get(key), str) for key in ('gcode_path', 'sha256', 'gcode_lines_uri')):
            return False
    return True


class FilesystemArtifacts:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        for name in ('runs', 'cache'):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def _directory(self, job_id: str) -> Path:
        if not isinstance(job_id, str) or re.fullmatch('[0-9a-f]{32}', job_id) is None:
            raise ValueError('Invalid generated job ID')
        directory = self.root / 'runs' / job_id
        if directory.resolve() != directory:
            raise ValueError('Generated job directory cannot be a symlink')
        return directory

    def prepare_source(self, input_3mf: str) -> SourceSnapshot:
        path = Path(input_3mf).expanduser().resolve(strict=True)
        validate_project(path)
        digest = file_hash(path)
        return SourceSnapshot(path=str(path), sha256=digest)

    def job(self, job_id: str) -> JobManifest:
        directory = self._directory(job_id)
        manifest = directory / 'result.json'
        if not manifest.is_file() or manifest.is_symlink():
            raise ValueError('Generated job is unavailable')
        parsed = decode_object(manifest.read_bytes(), 'Generated job record')
        if parsed.get('job_id') != job_id or not _manifest(parsed):
            raise ValueError('Generated job artifact records are invalid')
        parsed['source']['path'] = str(directory / 'source.3mf')
        for plate in parsed['plates']:
            plate['gcode_path'] = str(directory / f"plate_{plate['plate']}.gcode")
        parsed['props_path'] = str(directory / 'props.json')
        parsed['log_path'] = str(directory / 'engine.log')
        return parsed

    def intact(self, result: JobManifest) -> bool:
        try:
            directory = self._directory(result['job_id'])
            expected = {'source.3mf': result['source']['sha256'], 'props.json': result['props_sha256']}
            for plate in result['plates']:
                expected[f"plate_{plate['plate']}.gcode"] = plate['sha256']
            if not result['plates']:
                return False
            for filename, digest in expected.items():
                path = directory / filename
                if path.is_symlink() or not path.is_file() or file_hash(path) != digest:
                    return False
            return True
        except (OSError, ValueError):
            return False

    def cached(self, key: str, request: SliceRequest, source: SourceSnapshot, engine: JsonObject) -> JsonObject | None:
        pointer = self.root / 'cache' / f'{key}.json'
        try:
            entry = decode_object(pointer.read_bytes())
            job_id = entry.get('job_id')
            if not isinstance(job_id, str):
                return None
            manifest = self._directory(job_id) / 'result.json'
            if entry.get('result_sha256') != file_hash(manifest):
                return None
            result = self.job(job_id)
            if (result['cache_key'], result['requested_plate'], result['source']['sha256'], result['engine'], result['compatibility_flags']) != (
                    key, request.plate, source.sha256, engine, {'allow_newer_file': request.allow_newer_file}):
                return None
            return json_object(result) if self.intact(result) else None
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def create(self, source: SourceSnapshot) -> SliceWorkspace:
        identity = uuid.uuid4().hex
        directory = self._directory(identity)
        directory.mkdir()
        target = directory / 'source.3mf'
        try:
            shutil.copyfile(source.path, target)
            if source.sha256 != file_hash(target):
                raise ValueError('Source changed during snapshot; retry with a stable saved file')
            validate_project(target)
        except BaseException:
            shutil.rmtree(directory)
            raise
        return SliceWorkspace(identity, str(directory), SourceSnapshot(str(target), source.sha256))

    def publish(self, workspace: SliceWorkspace, key: str, request: SliceRequest,
                engine: JsonObject, engine_status: JsonObject, native_seconds: float,
                request_seconds: float) -> JsonObject:
        directory = self._directory(workspace.job_id)
        if directory != Path(workspace.directory) or Path(workspace.source.path) != directory / 'source.3mf':
            raise ValueError('Slice workspace belongs to another repository')
        if file_hash(directory / 'source.3mf') != workspace.source.sha256:
            raise ValueError('Slice source snapshot changed during native execution')
        outputs: dict[int, Path] = {}
        for candidate in directory.glob('plate_*.gcode'):
            match = re.fullmatch(r'plate_([1-9][0-9]*)\.gcode', candidate.name)
            if match and candidate.is_file() and not candidate.is_symlink():
                outputs[int(match[1])] = candidate
        if not outputs or any(path.stat().st_size <= 0 for path in outputs.values()):
            raise RuntimeError(f'Native slice returned no valid G-code; diagnostics: {directory}')
        if request.plate and set(outputs) != {request.plate}:
            raise RuntimeError(f'Native output plate mismatch; diagnostics: {directory}')
        properties: JsonObject = {
            'scope': 'native_global_export_and_per_plate_config_block; final per-region values are not exhaustively exported',
            'native_global': decode_json((directory / 'native-settings.json').read_bytes()),
            'plate_config': {str(number): gcode_properties(outputs[number]) for number in sorted(outputs)},
            'fallback_profile': None,
            'substitution_coverage': 'Native defaults and compatibility substitutions may apply; consult settings and engine.log'}
        props = directory / 'props.json'
        write_json(props, properties)
        plates: list[PlateRecord] = []
        for number, path in sorted(outputs.items()):
            plates.append(PlateRecord(plate=number, gcode_path=str(path), sha256=file_hash(path), size_bytes=path.stat().st_size,
                gcode_lines_uri=f'slicer://jobs/{workspace.job_id}/plates/{number}/lines/1/200'))
        result: JobManifest = {
            'status': 'ready', 'job_id': workspace.job_id, 'cached': False, 'cache_key': key,
            'requested_plate': request.plate, 'compatibility_flags': {'allow_newer_file': request.allow_newer_file},
            'source': {'path': workspace.source.path, 'sha256': workspace.source.sha256},
            'engine': engine, 'engine_status': engine_status, 'plates': plates,
            'props_path': str(props), 'props_sha256': file_hash(props),
            'manifest_uri': f'slicer://jobs/{workspace.job_id}/manifest', 'props_uri': f'slicer://jobs/{workspace.job_id}/props',
            'log_path': str(directory / 'engine.log'), 'native_elapsed_seconds': native_seconds,
            'request_elapsed_seconds': request_seconds}
        manifest = directory / 'result.json'
        output = json_object(result)
        write_json(manifest, output)
        write_json(self.root / 'cache' / f'{key}.json', {'job_id': workspace.job_id, 'result_sha256': file_hash(manifest)})
        return output

    def recover_3mf(self, gcode_file: str) -> JsonObject:
        wanted = file_hash(Path(gcode_file).expanduser().resolve(strict=True))
        sources: dict[str, JsonObject] = {}
        for record_path in (self.root / 'runs').glob('*/result.json'):
            try:
                manifest = self.job(record_path.parent.name)
                matching = any(plate['sha256'] == wanted for plate in manifest['plates'])
                if matching and self.intact(manifest):
                    sources[manifest['source']['sha256']] = json_object(manifest['source'])
            except (OSError, ValueError, TypeError, KeyError):
                continue
        if len(sources) != 1:
            raise ValueError('Original 3MF is unavailable or ambiguous; G-code cannot reconstruct its source mesh')
        source = next(iter(sources.values()))
        return dict(source, kind='source_recovery', reconstructed=False, gcode_sha256=wanted)

    def read_lines(self, job_id: str, plate: int, start: int = 1, count: int = 200) -> GcodeLines:
        bounded_lines((), job_id, plate, start, count)
        manifest = self.job(job_id)
        if type(plate) is not int or not any(item['plate'] == plate for item in manifest['plates']):
            raise ValueError('Plate is not part of this generated job')
        if not self.intact(manifest):
            raise ValueError('Generated job artifacts changed outside the service')
        target = self._directory(job_id) / f'plate_{plate}.gcode'
        with target.open(errors='replace') as source:
            return bounded_lines(source, job_id, plate, start, count)
