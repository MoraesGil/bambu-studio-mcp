"""Build editable Bambu projects using explicit CLI profiles and mesh snapshots."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from ...domain.types.json import JsonObject, JsonValue, json_object
from ...presets import Catalog
from ..storage.artifacts import validate_project, write_json
from .editing import NativeHost
from .engine import file_hash
from .project_format import ensure_bambu_project
from .runner import run_command


class BambuCreation:
    def __init__(self, host: NativeHost, catalog: Catalog, root: Path):
        self.host = host
        self.catalog = catalog
        self.root = root

    def _profile(self, kind: str, name: str) -> JsonObject:
        selected = self.catalog.get_preset_config(kind, name)
        flattened = dict(json_object(selected['config']))
        if flattened.get('post_process') not in (None, '', []):
            raise ValueError('External post-processing scripts are prohibited')
        flattened.pop('inherits', None)
        # load_config_file consumes these envelope fields outside PrintConfig.
        # filament_id must survive flattening even though it is schema metadata,
        # since CLI load-filaments carries it into project filament_ids.
        if kind == 'filament':
            effective = json_object(selected.get('effective_metadata', {}))
            if 'filament_id' in effective:
                flattened['filament_id'] = effective['filament_id']
        flattened.update(name=name, type={'printer': 'machine', 'print': 'process', 'filament': 'filament'}[kind])
        flattened['from'] = 'system' if selected['source'] == 'system' else 'User'
        flattened['version'] = json_object(self.catalog.schema['_meta'])['native_version']
        return flattened

    def create(self, mesh_paths: list[str], printer_preset: str, process_preset: str,
               filament_presets: list[str]) -> JsonObject:
        if not mesh_paths or not filament_presets:
            raise ValueError('Specify at least one STL/OBJ mesh and one filament preset')
        sources = [Path(item).expanduser().resolve(strict=True) for item in mesh_paths]
        if any(not path.is_file() or path.suffix.lower() not in {'.stl', '.obj'} for path in sources):
            raise ValueError('Creation accepts existing STL/OBJ meshes; load_model opens saved 3MF projects')
        profiles = [self._profile('printer', printer_preset), self._profile('print', process_preset)]
        profiles.extend(self._profile('filament', name) for name in filament_presets)
        directory = self.root / 'creations' / uuid.uuid4().hex
        directory.mkdir(parents=True, mode=0o700)
        datadir = directory / 'native-data'
        datadir.mkdir(mode=0o700)
        provenance: list[JsonValue] = []
        copies: list[str] = []
        for index, source in enumerate(sources):
            digest = file_hash(source)
            copied = directory / f'mesh_{index}{source.suffix.lower()}'
            shutil.copyfile(source, copied)
            copied.chmod(0o600)
            if digest != file_hash(copied) or digest != file_hash(source):
                raise ValueError('Mesh changed while copying; retry with a stable saved file')
            provenance.append({'path': str(source), 'sha256': digest})
            copies.append(str(copied))
        paths = []
        for index, config in enumerate(profiles):
            target = directory / f'preset_{index}.json'
            write_json(target, config)
            target.chmod(0o600)
            paths.append(str(target))
        output = directory / 'created.3mf'
        # Bambu CLI appends outputdir to export-3mf; an absolute export name
        # becomes a duplicated path. Profile lists use semicolons, in order.
        argv = [str(self.host.engine.executable), '--load-settings', ';'.join(paths[:2]),
                '--load-filaments', ';'.join(paths[2:]), '--arrange', '1',
                '--datadir', str(datadir), '--outputdir', str(directory), '--export-3mf', output.name, *copies]
        request: JsonObject = {'argv': list(argv), 'source_meshes': provenance, 'engine': self.host.engine.identity()}
        write_json(directory / 'request.json', request)
        with self.host.native_session():
            self.host.engine.verify()
            try:
                code, _ = run_command(argv, directory, self.host.timeout)
            finally:
                self.host.engine.verify()
        if code != 0:
            write_json(directory / 'failure.json', {**request, 'exit_code': code})
            raise RuntimeError(f'Native Bambu project creation failed ({code}); diagnostics: {directory}')
        if output.is_symlink():
            raise ValueError('Native creation output must be a regular 3MF file')
        validate_project(output)
        ensure_bambu_project(output)
        for source, entry in zip(sources, provenance):
            if file_hash(source) != json_object(entry)['sha256']:
                raise ValueError('Input mesh changed during native creation; no project was loaded')
        return dict(path=str(output), source_meshes=provenance, creation_diagnostics=str(directory))
