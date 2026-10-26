"""Create a native 3MF from copied meshes and explicitly selected presets."""
from __future__ import annotations

import os
import signal
import shutil
import subprocess
import uuid
from pathlib import Path

from ...domain.types.json import JsonObject, JsonValue, json_object
from ...presets import Catalog
from ..storage.artifacts import validate_project, write_json
from .editing import NativeHost
from .engine import file_hash


class BambuCreation:
    def __init__(self, host: NativeHost, catalog: Catalog, root: Path):
        self.host, self.catalog, self.root = host, catalog, root

    def create(self, mesh_paths: list[str], printer_preset: str, process_preset: str,
               filament_presets: list[str]) -> JsonObject:
        if not mesh_paths or not filament_presets:
            raise ValueError('Specify at least one STL/OBJ mesh and one filament preset')
        sources = [Path(path).expanduser().resolve(strict=True) for path in mesh_paths]
        if any(path.suffix.lower() not in ('.stl', '.obj') or not path.is_file() for path in sources):
            raise ValueError('Creation accepts existing STL/OBJ meshes; load_model opens saved 3MF projects')
        profiles = [('printer', printer_preset), ('print', process_preset),
                    *(('filament', name) for name in filament_presets)]
        configs: list[JsonObject] = []
        for kind, name in profiles:
            selected = self.catalog.get_preset_config(kind, name)
            config = json_object(selected['config'])
            config.pop('inherits', None)
            if config.get('post_process') not in (None, '', []):
                raise ValueError('External post-processing scripts are prohibited')
            metadata = json_object(self.catalog.schema['_meta'])
            effective = json_object(selected.get('effective_metadata', {}))
            if kind == 'filament' and 'filament_id' in effective:
                config['filament_id'] = effective['filament_id']
            config.update(name=name, type={'printer': 'machine', 'print': 'process', 'filament': 'filament'}[kind],
                          **{'from': 'system' if selected['source'] == 'system' else 'User',
                             'version': metadata['native_version']})
            configs.append(config)
        work = self.root / 'creations' / uuid.uuid4().hex
        work.mkdir(parents=True, mode=0o700)
        datadir, temporary = work / 'native-data', work / 'native-tmp'
        datadir.mkdir(mode=0o700)
        temporary.mkdir(mode=0o700)
        snapshots: list[Path] = []
        provenance: list[JsonValue] = []
        for index, source in enumerate(sources):
            expected = file_hash(source)
            target = work / f'mesh_{index}{source.suffix.lower()}'
            shutil.copyfile(source, target)
            target.chmod(0o600)
            if file_hash(target) != expected:
                raise ValueError('Mesh changed while copying; retry with a stable saved file')
            snapshots.append(target)
            provenance.append({'path': str(source), 'sha256': expected})
        settings: list[Path] = []
        for index, config in enumerate(configs):
            path = work / f'preset_{index}.json'
            write_json(path, config)
            path.chmod(0o600)
            settings.append(path)
        output = work / 'created.3mf'
        argv = [str(self.host.engine.executable), '--load-settings', ';'.join(map(str, settings[:2])),
                '--load-filaments', ';'.join(map(str, settings[2:])), '--arrange', '1',
                '--datadir', str(datadir), '--outputdir', str(work), '--export-3mf', output.name,
                *map(str, snapshots)]
        env = {key: value for key, value in os.environ.items() if key not in ('DISPLAY', 'WAYLAND_DISPLAY')}
        env.update(LC_ALL='C', TMPDIR=str(temporary) + os.sep)
        write_json(work / 'request.json', {'argv': [value for value in argv], 'source_meshes': provenance,
                                          'engine': self.host.engine.identity()})
        try:
            with self.host.native_session():
                self.host.engine.verify()
                with (work / 'engine.log').open('w') as log:
                    process = subprocess.Popen(argv, cwd=work, env=env, stdout=log, stderr=subprocess.STDOUT,
                                               start_new_session=(os.name != 'nt'))
                    try:
                        code = process.wait(timeout=self.host.timeout)
                    except subprocess.TimeoutExpired as error:
                        if os.name == 'nt':
                            process.kill()
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                        raise TimeoutError(f'Native creation timed out; diagnostics: {work}') from error
                self.host.engine.verify()
            if code:
                raise RuntimeError(f'Native Bambu project creation failed ({code}); diagnostics: {work}')
            if output.is_symlink():
                raise ValueError('Native creation output must be a regular 3MF file')
            validate_project(output)
            for source, entry in zip(sources, provenance):
                if file_hash(source) != json_object(entry)['sha256']:
                    raise ValueError('Input mesh changed during native creation; no project was loaded')
            return {'path': str(output), 'source_meshes': provenance, 'creation_diagnostics': str(work)}
        finally:
            shutil.rmtree(temporary)
