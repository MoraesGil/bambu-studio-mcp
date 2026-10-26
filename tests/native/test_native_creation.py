"""Observed STL/OBJ -> native 3MF -> immutable edit -> G-code workflow."""
from pathlib import Path
import json
import zipfile

import pytest

from bambu_studio_mcp.editor import Editor
from bambu_studio_mcp.service import Slicer
from .conftest import record
from .harness import box_mesh, observe


def test_real_native_creation_edit_and_slice(native_engine, tmp_path):
    vertices, faces = box_mesh((0, 0, 0), (12, 12, 3))
    mesh = tmp_path / 'synthetic.obj'
    mesh.write_text('\n'.join([*(f'v {x} {y} {z}' for x, y, z in vertices),
                                *(f'f {a+1} {b+1} {c+1}' for a, b, c in faces)]) + '\n')
    original = mesh.read_bytes()
    editor = Editor(Slicer(native_engine, tmp_path/'slicer', timeout=120))
    try:
        loaded = editor.create_project([str(mesh)], 'Bambu Lab X1 Carbon 0.4 nozzle',
                                       '0.20mm Standard @BBL X1C', ['Generic PLA'])
        created = Path(loaded['path'])
        source_3mf = created.read_bytes()
        with zipfile.ZipFile(created) as archive:
            assert json.loads(archive.read('Metadata/project_settings.config'))['filament_ids'] == ['GFL99']
        pid = loaded['project_id']
        edited = editor.set_config(pid, {'layer_height': 0.28, 'curr_bed_type': 'Cool Plate',
            'cool_plate_temp': [50], 'cool_plate_temp_initial_layer': [50],
            'additional_cooling_fan_speed': [0]}, loaded['sha256'])
        assert edited['path'] != loaded['path']
        with pytest.raises(ValueError, match='Stale'):
            editor.set_config(pid, {'layer_height': 0.12}, loaded['sha256'])
        sliced = editor.slice_project(pid, plate=1)
        gcode = Path(sliced['plates'][0]['gcode_path'])
        observation = observe(gcode.read_text())
        assert observation['moves']
        levels = sorted({move['z'] for move in observation['moves'] if move['feature']=='Outer wall'})
        assert any(abs(b-a-0.28)<0.005 for a, b in zip(levels, levels[1:]))
        assert mesh.read_bytes() == original
        assert created.read_bytes() == source_3mf
        record(native_engine, 'creation', 'pass', {'scope': 'native OBJ import, explicit Bambu presets, saved immutable edit, stale rejection, observed layer spacing',
            'source_3mf': edited['path'], 'gcode': str(gcode)})
    except Exception as error:
        record(native_engine, 'creation', 'fail', {'error_type': type(error).__name__})
        raise
