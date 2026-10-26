"""Actual Bambu arrange/orient preserves source geometry and saved revisions."""
from pathlib import Path

from bambu_studio_mcp.editor import Editor
from bambu_studio_mcp.service import Slicer
from .conftest import record
from .harness import make_project, official_config


def test_real_native_arrange_and_orient(native_engine, tmp_path):
    editor = Editor(Slicer(native_engine, tmp_path/'slicer', timeout=120))
    source = make_project(tmp_path/'layout.3mf', official_config(native_engine, tmp_path))
    original = source.read_bytes()
    try:
        state = editor.load_model(str(source))
        pid = state['project_id']
        for operation in ('arrange_plate', 'auto_orient'):
            state = editor.edit_project(pid, [{'operation': operation, 'plate': 1}], state['sha256'])
            assert Path(state['path']).is_file()
            assert source.read_bytes() == original
        result = editor.slice_project(pid, plate=1)
        assert Path(result['plates'][0]['gcode_path']).stat().st_size > 0
        record(native_engine, 'layout', 'pass', {'scope': 'native arrange/orient STL measurements with vertex correspondence, preserved source, saved revision sliced',
            'source_3mf': state['path']})
    except Exception as error:
        record(native_engine, 'layout', 'fail', {'error_type': type(error).__name__})
        raise
