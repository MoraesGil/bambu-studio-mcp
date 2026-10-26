"""Creation exercises a real subprocess boundary with synthetic public inputs."""
import json
import sys
from pathlib import Path

import pytest


def creator(tmp_path):
    from test_service import boundary
    from test_editor import project
    from bambu_studio_mcp.engine import Engine
    from bambu_studio_mcp.service import Slicer
    from bambu_studio_mcp.editor import Editor
    slicer, binary = boundary(tmp_path)
    project(binary.with_suffix('.fixture'))
    binary.write_text(f'''#!{sys.executable}
import argparse,json,pathlib,shutil,sys
p=argparse.ArgumentParser()
for flag in ('load-settings','load-filaments','datadir','outputdir','export-3mf','arrange'):
 p.add_argument('--'+flag,required=True)
p.add_argument('meshes',nargs='+')
a=p.parse_args()
out=pathlib.Path(a.outputdir)
assert not pathlib.Path(a.export_3mf).is_absolute()
assert all(pathlib.Path(s).is_file() and pathlib.Path(s).parent==out for s in a.meshes)
configs=[json.loads(pathlib.Path(s).read_text()) for s in a.load_settings.split(';')]
assert all(not d.get('inherits') for d in configs)
assert [d.get('type') for d in configs]==['machine','process']
assert all(d.get('from')=='system' and d.get('name') and d.get('version') for d in configs)
assert json.loads(pathlib.Path(a.load_filaments).read_text()).get('filament_id')=='GFLSYNTHETIC'
(out/'probe.json').write_text(json.dumps(vars(a)))
shutil.copyfile(pathlib.Path(__file__).with_suffix('.fixture'),out/a.export_3mf)
''')
    slicer = Slicer(Engine(Engine.bind(binary, '1'*40, slicer.engine.resources)), tmp_path/'artifacts')
    profiles = slicer.engine.resources/'profiles/BBL'
    for kind,name,cfg in [('machine','Printer',{'printer_model':'Synthetic Bambu','nozzle_diameter':['0.4']}),
                          ('process','Process',{'layer_height':'0.2'}),
                          ('filament','PLA',{'inherits':'PLA metadata base','filament_type':['PLA'],'filament_settings_id':['PLA'],
                                             'nozzle_temperature':['210'],'additional_cooling_fan_speed':['0']})]:
        path = profiles/kind/(name+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({'name':name,'type':kind,**cfg}))
    base = profiles/'filament/metadata-base.json'
    base.write_text(json.dumps({'name':'PLA metadata base','type':'filament','filament_id':'GFLSYNTHETIC'}))
    # Profiles were added after binding; rebind explicitly before use.
    slicer.engine = Engine(Engine.bind(binary, '1'*40, slicer.engine.resources))
    return Editor(slicer), slicer


def test_create_project_imports_meshes_with_explicit_presets_and_preserves_inputs(tmp_path):
    editor, slicer = creator(tmp_path)
    mesh = tmp_path/'original mesh.stl'
    mesh.write_bytes(b'public synthetic mesh boundary')
    result = editor.create_project([str(mesh)], 'Printer', 'Process', ['PLA'])
    assert result['revision'] == 0
    assert mesh.read_bytes() == b'public synthetic mesh boundary'
    assert result['source_meshes'][0]['path'] == str(mesh.resolve())
    assert editor.list_objects(result['project_id'])['objects']
    probe = json.loads(Path(result['creation_diagnostics']).joinpath('probe.json').read_text())
    assert probe['export_3mf'] == 'created.3mf'
    assert probe['arrange'] == '1'
    assert probe['meshes'] != [str(mesh)]


@pytest.mark.parametrize('paths,filaments', [([],['PLA']), (['input.3mf'],['PLA']), (['input.stl'],[])])
def test_creation_rejects_missing_meshes_wrong_formats_and_empty_materials(tmp_path,paths,filaments):
    editor, _ = creator(tmp_path)
    with pytest.raises((ValueError,FileNotFoundError)):
        editor.create_project(paths,'Printer','Process',filaments)
    assert list(editor.root.glob('*/state.json')) == []


def test_cli_creates_native_project_and_returns_editable_revision(tmp_path, capsys):
    from bambu_studio_mcp.cli import main
    _, slicer = creator(tmp_path)
    engine_file = tmp_path/'engine.json'
    engine_file.write_text(json.dumps(slicer.engine.manifest))
    mesh = tmp_path/'mesh.stl'
    mesh.write_bytes(b'public synthetic mesh boundary')
    main(['--engine',str(engine_file),'--artifacts',str(slicer.root),'create',str(mesh),
          '--printer','Printer','--process','Process','--filament','PLA'])
    result = json.loads(capsys.readouterr().out)
    assert result['revision'] == 0
    assert Path(result['path']).is_file()
