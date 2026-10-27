"""Native Bambu consumption of generated variable heights, ranges and reset."""
import hashlib
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

from bambu_studio_mcp.editor import Editor
from bambu_studio_mcp.service import Slicer
from .conftest import record
from .harness import make_project, official_config, observe


def _wedge(path, config):
    source = make_project(path, config)
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    model = ET.fromstring(contents['3D/3dmodel.model'])
    for vertex in model.findall('.//{*}vertex'):
        if float(vertex.attrib['z']) > 0:
            vertex.set('z', str(2 + float(vertex.attrib['x']) / 3))
    ET.register_namespace('', 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02')
    contents['3D/3dmodel.model'] = ET.tostring(model)
    with zipfile.ZipFile(source, 'w') as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)
    return source


def _levels(editor, project_id):
    result = editor.slice_project(project_id, plate=1)
    path = Path(result['plates'][0]['gcode_path'])
    observation = observe(path.read_text())
    levels = sorted({move['z'] for move in observation['moves'] if move['feature'] == 'Outer wall'})
    assert len(levels) > 5
    return levels


def test_real_bambu_adaptive_profile_ranges_and_reset(native_engine, tmp_path):
    config = official_config(native_engine, tmp_path)
    source = _wedge(tmp_path / 'adaptive-wedge.3mf', config)
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    editor = Editor(Slicer(native_engine, tmp_path / 'slicer', timeout=120))
    try:
        state = editor.load_model(str(source))
        pid = state['project_id']
        baseline = _levels(editor, pid)
        state = editor.edit_project(pid, [{'operation': 'set_layer_height', 'object_id': 1,
                                          'mode': 'adaptive', 'quality': 0}], state['sha256'])
        fine = _levels(editor, pid)
        state = editor.edit_project(pid, [{'operation': 'set_layer_height', 'object_id': 1,
                                          'mode': 'adaptive', 'quality': 1}], state['sha256'])
        coarse = _levels(editor, pid)
        # A successful slice alone is insufficient: actual extrusion Z spacing
        # must change, proving Bambu did not ignore the saved profile file.
        assert len(fine) > len(coarse) * 1.3
        assert fine != baseline and coarse != baseline
        state = editor.edit_project(pid, [
            {'operation': 'set_height_range', 'object_id': 1, 'min_z': 1., 'max_z': 3., 'layer_height': .12},
            {'operation': 'set_layer_height', 'object_id': 1, 'mode': 'adaptive', 'quality': 1}], state['sha256'])
        ranged = _levels(editor, pid)
        middle = [z for z in ranged if 1.3 < z < 2.7]
        assert len(middle) >= 5
        assert all(abs(high - low - .12) < .003 for low, high in zip(middle, middle[1:]))
        state = editor.edit_project(pid, [{'operation': 'set_layer_height', 'object_id': 1, 'mode': 'reset'}], state['sha256'])
        reset = _levels(editor, pid)
        assert reset == baseline
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
        record(native_engine, 'adaptive', 'pass', {
            'scope': 'Bambu source-derived adaptive generation; native extrusion Z spacing, explicit range, reset and immutable source',
            'source_sha256': original_hash, 'baseline_layers': len(baseline), 'fine_layers': len(fine),
            'coarse_layers': len(coarse), 'range_layers': len(ranged), 'reset_layers': len(reset),
            'generator_source_commit': '926a7192574bcb9b3a732e1ec59a46d79cb45466'})
    except Exception as error:
        record(native_engine, 'adaptive', 'fail', {'error_type': type(error).__name__})
        raise
