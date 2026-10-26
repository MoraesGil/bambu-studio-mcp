import copy
import hashlib
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from test_service import boundary

NATIVE_SOURCE = '926a7192574bcb9b3a732e1ec59a46d79cb45466'


def project(path):
    model = '''<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" unit="millimeter"><metadata name="Application">BambuStudio-02.08.02.61</metadata><resources><object id="2" type="model"><mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="10" y="0" z="0"/><vertex x="0" y="10" z="0"/><vertex x="0" y="0" z="10"/></vertices><triangles><triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/><triangle v1="0" v2="3" v3="2"/><triangle v1="1" v2="2" v3="3"/></triangles></mesh></object></resources><build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 30 40 0"/></build></model>'''
    settings = '''<config><object id="2"><metadata key="name" value="tetra"/></object><plate><metadata key="plater_id" value="1"/><model_instance><metadata key="object_id" value="2"/><metadata key="instance_id" value="0"/><metadata key="identify_id" value="74"/></model_instance></plate></config>'''
    config = {'layer_height': '0.2', 'printer_model': 'Bambu Lab P1S', 'printer_technology': 'FFF',
              'printer_settings_id': 'Current P1S', 'print_settings_id': 'Current process', 'curr_bed_type': 'Cool Plate',
              'bridge_speed': '50', 'small_perimeter_speed': '150%', 'host_type': 'octoprint',
              'filament_settings_id': ['one', 'two'], 'filament_type': ['PLA', 'PETG'],
              'nozzle_temperature': ['220', '240'], 'nozzle_temperature_initial_layer': ['220', '245'],
              'cool_plate_temp': ['50', '65'], 'cool_plate_temp_initial_layer': ['50', '65'],
              'additional_cooling_fan_speed': ['0', '0'], 'printable_area': ['0x0', '270x0', '270x270', '0x270'],
              'printable_height': '270', 'post_process': []}
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('3D/3dmodel.model', model)
        z.writestr('Metadata/model_settings.config', settings)
        z.writestr('Metadata/project_settings.config', json.dumps(config))
        z.writestr('Metadata/plate_1.gcode', 'G1 X999 E999 ; stale')
        z.writestr('Auxiliaries/keep.bin', b'preserve this payload')
    return path


def test_edit_versions_preserve_origin_and_reject_a_stale_revision(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    source = project(tmp_path / 'input.3mf')
    original = source.read_bytes()
    loaded = editor.load_model(str(source))
    edited = editor.set_config(loaded['project_id'], {'layer_height': 0.12}, expected_sha256=loaded['sha256'])
    assert source.read_bytes() == original
    assert edited['sha256'] != loaded['sha256']
    with zipfile.ZipFile(edited['path']) as z:
        assert json.loads(z.read('Metadata/project_settings.config'))['layer_height'] == '0.12'
        assert z.read('Auxiliaries/keep.bin') == b'preserve this payload'
        assert 'Metadata/plate_1.gcode' not in z.namelist()
    with pytest.raises(ValueError, match='revision|changed|stale'):
        editor.set_config(loaded['project_id'], {'layer_height': 0.16}, expected_sha256=loaded['sha256'])
    assert editor.get_status(loaded['project_id'])['sha256'] == edited['sha256']
    result = editor.slice_project(loaded['project_id'], plate=1)
    assert Path(slicer.recover_3mf(result['plates'][0]['gcode_path'])['path']).read_bytes() == Path(edited['path']).read_bytes()


@pytest.mark.parametrize('apply_changes', [False, True])
def test_slow_slice_releases_editor_lock_and_keeps_its_captured_revision(tmp_path, monkeypatch, apply_changes):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    source = project(tmp_path / 'input.3mf')
    original = source.read_bytes()
    loaded = editor.load_model(str(source))
    loaded = editor.edit_project(loaded['project_id'], [{'operation': 'add_plate'}])
    if not apply_changes:
        editor.set_config(loaded['project_id'], {'layer_height': 0.12}, loaded['sha256'])
    started, release = threading.Event(), threading.Event()
    native_slice = slicer.slice_3mf

    def slow_slice(*args, **kwargs):
        started.set()
        if not release.wait(5):
            raise TimeoutError('Test did not release the slice boundary')
        return native_slice(*args, **kwargs)

    monkeypatch.setattr(slicer, 'slice_3mf', slow_slice)
    with ThreadPoolExecutor(max_workers=3) as pool:
        if apply_changes:
            pending = pool.submit(editor.apply_and_slice, loaded['project_id'], {'layer_height': 0.12},
                                  expected_sha256=loaded['sha256'])
        else:
            pending = pool.submit(editor.slice_project, loaded['project_id'], force=True)
        assert started.wait(3)
        try:
            captured = pool.submit(editor.get_config, loaded['project_id']).result(timeout=1)
            assert captured['config']['layer_height'] == '0.12'
            later = pool.submit(editor.set_config, loaded['project_id'], {'layer_height': 0.16},
                                captured['sha256']).result(timeout=1)
            pool.submit(editor.select_plate, loaded['project_id'], 2).result(timeout=1)
            with pytest.raises(ValueError, match='Stale'):
                pool.submit(editor.set_config, loaded['project_id'], {'layer_height': 0.2},
                            captured['sha256']).result(timeout=1)
        finally:
            release.set()
        result = pending.result(timeout=5)
    sliced = result['slice'] if apply_changes else result
    assert sliced['requested_plate'] == 1
    assert sliced['source']['sha256'] == captured['sha256'] != later['sha256']
    with zipfile.ZipFile(sliced['source']['path']) as archive:
        assert json.loads(archive.read('Metadata/project_settings.config'))['layer_height'] == '0.12'
    if apply_changes:
        assert result['project']['revision'] == captured['revision']
        assert result['project']['sha256'] == captured['sha256']
    assert editor.get_status(loaded['project_id'])['selected_plate'] == 2
    assert source.read_bytes() == original


def test_select_filament_changes_one_slot_and_preserves_preset_source(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    root = tmp_path / 'catalog'
    (root / 'filament').mkdir(parents=True)
    preset = root / 'filament/_GENERIC_PETG.json'
    preset.write_text(json.dumps({'type': 'filament', 'name': '_GENERIC_PETG', 'inherits': '', 'from': 'User',
                                 'nozzle_temperature': ['230'], 'nozzle_temperature_initial_layer': ['230'],
                                 'cool_plate_temp': ['65'], 'cool_plate_temp_initial_layer': ['65'],
                                 'filament_type': ['PETG'], 'additional_cooling_fan_speed': ['0']}))
    original_hash = hashlib.sha256(preset.read_bytes()).hexdigest()
    editor = Editor(slicer, preset_dirs=[root])
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    selected = editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', filament_slot=1)
    config = editor.get_config(loaded['project_id'])['config']
    assert config['nozzle_temperature'] == ['220', '230']
    assert config['cool_plate_temp'] == ['50', '65']
    assert config['filament_settings_id'] == ['one', '_GENERIC_PETG']
    assert hashlib.sha256(preset.read_bytes()).hexdigest() == original_hash
    assert Path(selected['path']).is_file()


@pytest.mark.parametrize('native_id', ['GFL99', None])
def test_select_filament_replaces_target_native_id_without_changing_other_slots(tmp_path, native_id):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    root = tmp_path / 'catalog/filament'
    root.mkdir(parents=True)
    parent = {'type': 'filament', 'name': 'Identity parent', 'nozzle_temperature': ['230']}
    if native_id is not None:
        parent['filament_id'] = native_id
    (root / 'parent.json').write_text(json.dumps(parent))
    (root / 'child.json').write_text(json.dumps({'type': 'filament', 'name': 'Selected material',
                                                'inherits': 'Identity parent'}))
    source = project(tmp_path / 'identity.3mf')
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    config = json.loads(entries['Metadata/project_settings.config'])
    config['filament_ids'] = ['GFL00', 'STALE']
    entries['Metadata/project_settings.config'] = json.dumps(config).encode()
    with zipfile.ZipFile(source, 'w') as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    original = source.read_bytes()
    preset_bytes = {p.name: p.read_bytes() for p in root.glob('*.json')}
    editor = Editor(slicer, preset_dirs=[root.parent])
    loaded = editor.load_model(str(source))
    result = editor.select_preset(loaded['project_id'], 'filament', 'Selected material', filament_slot=1)
    updated = editor.get_config(loaded['project_id'])['config']
    assert updated['filament_settings_id'] == ['one', 'Selected material']
    assert updated['filament_ids'] == ['GFL00', native_id or '']
    with zipfile.ZipFile(result['path']) as archive:
        assert json.loads(archive.read('Metadata/project_settings.config'))['filament_ids'] == ['GFL00', native_id or '']
    assert source.read_bytes() == original
    assert {p.name: p.read_bytes() for p in root.glob('*.json')} == preset_bytes


def test_failed_batch_does_not_commit_partial_edits(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    with pytest.raises(ValueError):
        editor.edit_project(loaded['project_id'], [{'operation': 'set_config', 'changes': {'layer_height': 0.12}},
                                                   {'operation': 'delete_object', 'object_id': 999}])
    assert editor.get_status(loaded['project_id'])['sha256'] == loaded['sha256']
    assert editor.get_config(loaded['project_id'])['config']['layer_height'] == '0.2'


def test_select_and_save_machine_preset_preserves_target_and_read_only_parent(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    root = tmp_path / 'catalog'
    (root / 'machine').mkdir(parents=True)
    preset = root / 'machine/u1.json'
    preset.write_text(json.dumps({'type': 'machine', 'name': 'Tuned P1S', 'inherits': '', 'from': 'User',
                                  'printer_model': 'Bambu Lab P1S', 'printer_technology': 'FFF', 'host_type': 'octoprint',
                                  'nozzle_diameter': ['0.4', '0.4']}))
    original = preset.read_bytes()
    editor = Editor(slicer, preset_dirs=[root])
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    editor.select_preset(loaded['project_id'], 'machine', 'Tuned P1S')
    saved = editor.save_preset(loaded['project_id'], 'machine', 'Draft P1S')
    assert saved['inherits'] == 'Tuned P1S'
    assert saved['config']['printer_model'] == 'Bambu Lab P1S'
    assert saved['config']['printer_technology'] == 'FFF'
    assert preset.read_bytes() == original


def test_save_process_draft_retains_parent_without_copying_post_process(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    root = tmp_path / 'catalog'
    (root / 'process').mkdir(parents=True)
    source = root / 'process/current.json'
    source.write_text(json.dumps({'type': 'process', 'name': 'Current process', 'inherits': '',
                                 'layer_height': '0.2', 'from': 'User'}))
    original = source.read_bytes()
    editor = Editor(slicer, preset_dirs=[root])
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    saved = editor.save_preset(loaded['project_id'], 'process', 'Draft process')
    assert saved['inherits'] == 'Current process'
    assert 'post_process' not in saved['raw_config']
    assert source.read_bytes() == original


def test_adaptive_generation_rejects_a_different_bound_source_version(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    with pytest.raises(ValueError, match='[Aa]daptive.*(?:version|source|commit)'):
        editor.edit_project(loaded['project_id'], [{'operation': 'set_layer_height', 'object_id': 2,
                                                   'mode': 'adaptive', 'quality': 0.5}])
    assert editor.get_status(loaded['project_id'])['sha256'] == loaded['sha256']


def test_select_native_process_normalizes_legacy_vectors_and_preserves_transport(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    root = tmp_path / 'catalog'
    (root / 'process').mkdir(parents=True)
    source = root / 'process/current.json'
    source.write_text(json.dumps({'type': 'process', 'name': 'Native process', 'inherits': '',
                                 'bridge_speed': '45', 'small_perimeter_speed': '140%', 'post_process': [],
                                 'filename_format': 'ignored_{input_filename_base}.gcode'}))
    editor = Editor(slicer, preset_dirs=[root])
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    result = editor.select_preset(loaded['project_id'], 'process', 'Native process')
    config = editor.get_config(loaded['project_id'])['config']
    assert config['bridge_speed'] == ['45']
    assert config['small_perimeter_speed'] == ['140%']
    assert config['host_type'] == 'octoprint'
    assert 'filename_format' in result['ignored_config_keys']


def test_read_project_config_redacts_url_credentials_in_scalars_and_arrays(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    source = project(tmp_path / 'input.3mf')
    with zipfile.ZipFile(source) as z:
        payloads = {n: z.read(n) for n in z.namelist()}
    config = json.loads(payloads['Metadata/project_settings.config'])
    url = 'https://example-user:TEST-PASSWORD@localhost/'
    config.update(print_host=url, print_host_webui=[url], printhost_apikey='TEST-API-KEY')
    payloads['Metadata/project_settings.config'] = json.dumps(config).encode()
    with zipfile.ZipFile(source, 'w') as z:
        for name, value in payloads.items():
            z.writestr(name, value)
    original = source.read_bytes()
    editor = Editor(slicer)
    loaded = editor.load_model(str(source))
    output = json.dumps(editor.get_config(loaded['project_id']))
    assert 'TEST-PASSWORD' not in output and 'TEST-API-KEY' not in output and 'example-user' not in output
    assert 'localhost' in output and source.read_bytes() == original


def test_native_global_arrange_commits_new_plates_and_memberships(tmp_path, monkeypatch):
    from bambu_studio_mcp.editor import Editor
    from bambu_studio_mcp.project import Project
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    source = project(tmp_path / 'input.3mf')
    original = source.read_bytes()
    loaded = editor.load_model(str(source))

    def native(engine, staged, work, **kwargs):
        assert kwargs['arrange'] and kwargs['plate'] == 0
        (work / 'native_ops.json').write_text(json.dumps({'status': 'verified', 'layout': {
            'plate_count': 2, 'instances': [{'object_id': 2, 'instance_id': 0, 'plate': 2}]}}))
        return {(2, 0): [1, 0, 0, 0, 1, 0, 0, 0, 1, 324, 0, 0]}

    monkeypatch.setattr('bambu_studio_mcp.native_ops.native_deltas', native)
    edited = editor.edit_project(loaded['project_id'], [{'operation': 'arrange_plate', 'plate': 0}])
    result = Project(Path(edited['path']))
    plates = result.list_plates()
    assert len(plates) == 2 and plates[0]['objects'] == []
    assert plates[1]['objects'][0]['object_id'] == 2
    assert result.model.find('.//{*}build/{*}item').get('transform').split()[-3:] == ['354', '40', '0']
    assert edited['results'][0]['result']['plate_count'] == 2
    assert source.read_bytes() == original


def test_select_process_then_filament_preserves_project_compatibility_metadata(tmp_path):
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    root = tmp_path / 'catalog'
    (root / 'process').mkdir(parents=True)
    (root / 'filament').mkdir()
    process = root / 'process/native.json'
    process.write_text(json.dumps({'type': 'process', 'name': 'Native process', 'inherits': '',
                                   'layer_height': '0.2', 'compatible_printers': ['Bambu Lab P1S'],
                                   'compatible_printers_condition': 'printer_model=="Bambu Lab P1S"'}))
    filament = root / 'filament/generic.json'
    filament.write_text(json.dumps({'type': 'filament', 'name': '_GENERIC_PETG', 'inherits': '',
                                    'nozzle_temperature': ['240'], 'compatible_printers': ['Bambu Lab P1S'],
                                    'compatible_prints': ['Native process'], 'compatible_prints_condition': ''}))
    before = process.read_bytes(), filament.read_bytes()
    editor = Editor(slicer, preset_dirs=[root])
    loaded = editor.load_model(str(project(tmp_path / 'input.3mf')))
    editor.set_config(loaded['project_id'], {'compatible_printers': ['saved machine'],
                                          'compatible_printers_condition': 'saved expression'})
    selected_process = editor.select_preset(loaded['project_id'], 'process', 'Native process')
    selected_filament = editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', filament_slot=1)
    config = editor.get_config(loaded['project_id'])['config']
    assert config['nozzle_temperature'] == ['220', '240']
    assert config['compatible_printers'] == ['saved machine']
    assert config['compatible_printers_condition'] == 'saved expression'
    assert 'compatible_prints' not in config
    assert 'compatible_printers' in selected_process['ignored_config_keys']
    assert 'compatible_prints' in selected_filament['ignored_config_keys']
    assert (process.read_bytes(), filament.read_bytes()) == before


def flow_editor(tmp_path, overrides=None, parent_values=None, engine_commit='1' * 40):
    """Native Bambu variants: material one has two entries; material two has one."""
    from bambu_studio_mcp.editor import Editor
    from bambu_studio_mcp.engine import Engine
    slicer, _ = boundary(tmp_path)
    system = slicer.engine.resources / 'profiles/BBL/filament'
    system.mkdir(parents=True)
    parent_config = {
        'type': 'filament', 'name': 'Dual variant PETG', 'inherits': '',
        'filament_extruder_variant': ['Direct Drive Standard', 'Direct Drive High Flow'],
        'filament_settings_id': ['Dual variant PETG'], 'filament_type': ['PETG'],
        'nozzle_temperature': ['240', '260'], 'nozzle_temperature_initial_layer': ['245', '265'],
        'additional_cooling_fan_speed': ['0'], 'cool_plate_temp': ['65'],
    }
    parent_config.update(parent_values or {})
    parent = system / 'parent.json'
    parent.write_text(json.dumps(parent_config))
    slicer.engine = Engine(Engine.bind(slicer.engine.executable, engine_commit, slicer.engine.resources))
    canonical = tmp_path / 'catalog/filament/generic.json'
    canonical.parent.mkdir(parents=True)
    canonical.write_text(json.dumps({'type': 'filament', 'name': '_GENERIC_PETG', 'inherits': 'Dual variant PETG'}))
    source = project(tmp_path / 'input.3mf')
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    config = json.loads(entries['Metadata/project_settings.config'])
    config.update({
        'filament_settings_id': ['_GENERIC_PETG', 'other'], 'filament_self_index': ['1', '1', '2'],
        'filament_extruder_variant': ['Direct Drive Standard', 'Direct Drive High Flow', 'Direct Drive Standard'],
        'filament_map': ['1', '1'], 'filament_volume_map': ['0', '0'], 'extruder_type': ['Direct Drive'],
        'nozzle_temperature': ['221', '231', '241'],
        'nozzle_temperature_initial_layer': ['222', '232', '242'],
    })
    config.update(overrides or {})
    config = {k: v for k, v in config.items() if v is not None}
    entries['Metadata/project_settings.config'] = json.dumps(config).encode()
    with zipfile.ZipFile(source, 'w') as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    editor = Editor(slicer, preset_dirs=[canonical.parent.parent])
    return editor, editor.load_model(str(source)), source, canonical, parent


@pytest.mark.parametrize('slot,mode,index,value', [
    (0, 'standard', 0, '240'), (0, 'high_flow', 1, '260'), (1, 'standard', 2, '240'),
])
def test_bambu_select_preserves_other_materials_variants_and_source(tmp_path, slot, mode, index, value):
    editor, loaded, source, canonical, parent = flow_editor(tmp_path)
    originals = source.read_bytes(), canonical.read_bytes(), parent.read_bytes()
    result = editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', slot, mode)
    config = editor.get_config(loaded['project_id'])['config']
    expected = ['221', '231', '241']; expected[index] = value
    assert config['nozzle_temperature'] == expected
    assert config['cool_plate_temp'] == (['65', '65'] if slot == 0 else ['50', '65'])
    assert config['additional_cooling_fan_speed'] == ['0', '0']
    assert config['filament_volume_map'] == (['1', '0'] if mode == 'high_flow' else ['0', '0'])
    assert config['filament_self_index'] == ['1', '1', '2']
    assert config['filament_extruder_variant'] == ['Direct Drive Standard', 'Direct Drive High Flow', 'Direct Drive Standard']
    assert result['selected_preset']['project_flow_index'] == index
    assert (source.read_bytes(), canonical.read_bytes(), parent.read_bytes()) == originals


@pytest.mark.parametrize('mode,index,wanted', [('standard', 0, ['246', '260']), ('high_flow', 1, ['240', '246'])])
def test_save_and_resave_bambu_variants_preserve_parent_other_variant(tmp_path, mode, index, wanted):
    editor, loaded, source, canonical, parent = flow_editor(tmp_path)
    before = source.read_bytes(), canonical.read_bytes(), parent.read_bytes()
    editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0, mode)
    edited = ['221', '231', '241']; edited[index] = '246'
    editor.set_config(loaded['project_id'], {'nozzle_temperature': edited})
    saved = editor.save_preset(loaded['project_id'], 'filament', 'Mixed draft', 0)
    assert saved['inherits'] == '_GENERIC_PETG'
    assert saved['config']['nozzle_temperature'] == wanted
    assert saved['config']['filament_extruder_variant'] == ['Direct Drive Standard', 'Direct Drive High Flow']
    assert 'filament_self_index' not in saved['raw_config']
    editor.select_preset(loaded['project_id'], 'filament', 'Mixed draft', 0, mode)
    edited[index] = '247'
    editor.set_config(loaded['project_id'], {'nozzle_temperature': edited})
    resaved = editor.save_preset(loaded['project_id'], 'filament', 'Mixed draft', 0)
    assert resaved['inherits'] == '_GENERIC_PETG'
    assert resaved['config']['nozzle_temperature'] == (['247', '260'] if mode == 'standard' else ['240', '247'])
    assert (source.read_bytes(), canonical.read_bytes(), parent.read_bytes()) == before


def test_single_variant_preset_uses_native_fallback_without_changing_project_variants(tmp_path):
    editor, loaded, _, _, _ = flow_editor(tmp_path, parent_values={
        'filament_extruder_variant': ['Direct Drive Standard'], 'nozzle_temperature': ['250'],
        'nozzle_temperature_initial_layer': ['255'],
    })
    result = editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0, 'high_flow')
    assert editor.get_config(loaded['project_id'])['config']['nozzle_temperature'] == ['221', '250', '241']
    assert result['selected_preset']['preset_flow_index'] == 0
    assert result['selected_preset']['preset_flow_fallback'] is True


@pytest.mark.parametrize('bad', [
    {'filament_self_index': ['1', '1']}, {'filament_self_index': ['0', '1', '2']},
    {'filament_self_index': ['1', '1.5', '2']}, {'filament_self_index': [1, 1, 2]},
    {'filament_extruder_variant': ['Direct Drive Standard']},
    {'filament_map': ['2', '1']}, {'filament_volume_map': ['9', '0']},
    {'nozzle_temperature': ['221', '231']}, {'cool_plate_temp': ['50', '60', '65']},
])
@pytest.mark.parametrize('action', ['select', 'save'])
def test_malformed_bambu_variant_layout_fails_atomically(tmp_path, bad, action):
    editor, loaded, source, canonical, parent = flow_editor(tmp_path, bad)
    before = source.read_bytes(), canonical.read_bytes(), parent.read_bytes()
    with pytest.raises(ValueError):
        if action == 'select':
            editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0, 'high_flow')
        else:
            editor.save_preset(loaded['project_id'], 'filament', 'Invalid draft', 0)
    assert editor.get_status(loaded['project_id'])['sha256'] == loaded['sha256']
    assert not list((editor.slicer.root / 'preset-drafts').rglob('*.json'))
    assert (source.read_bytes(), canonical.read_bytes(), parent.read_bytes()) == before


def test_missing_project_variant_never_modifies_unrelated_native_fallback_slot(tmp_path):
    editor, loaded, _, _, _ = flow_editor(tmp_path)
    with pytest.raises(ValueError, match='variant'):
        editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 1, 'high_flow')
    assert editor.get_status(loaded['project_id'])['sha256'] == loaded['sha256']


def test_variant_lookup_handles_reordered_noncontiguous_material_entries(tmp_path):
    editor, loaded, _, _, _ = flow_editor(tmp_path, {
        'filament_self_index': ['1', '2', '1'],
        'filament_extruder_variant': ['Direct Drive High Flow', 'Direct Drive Standard', 'Direct Drive Standard'],
    }, {'filament_extruder_variant': ['Direct Drive High Flow', 'Direct Drive Standard'],
        'nozzle_temperature': ['260', '240'], 'nozzle_temperature_initial_layer': ['265', '245']})
    result = editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0, 'standard')
    assert editor.get_config(loaded['project_id'])['config']['nozzle_temperature'] == ['221', '231', '240']
    assert result['selected_preset']['project_flow_index'] == 2
    assert result['selected_preset']['preset_flow_index'] == 1


def test_four_materials_and_bowden_mapping_preserve_other_entries(tmp_path):
    editor, loaded, _, _, _ = flow_editor(tmp_path, {
        'filament_settings_id': ['one', 'two', 'three', 'four'],
        'filament_self_index': ['1', '1', '2', '3', '4'],
        'filament_extruder_variant': ['Direct Drive Standard', 'Direct Drive High Flow', 'Direct Drive Standard', 'Direct Drive Standard', 'Bowden High Flow'],
        'extruder_type': ['Direct Drive', 'Bowden'], 'filament_map': ['1', '1', '1', '2'],
        'filament_volume_map': ['0', '0', '0', '1'],
        'nozzle_temperature': ['221', '231', '241', '251', '261'],
        'nozzle_temperature_initial_layer': ['222', '232', '242', '252', '262'],
        'additional_cooling_fan_speed': ['0', '0', '0', '0'],
        'cool_plate_temp': ['50', '65', '55', '70'], 'filament_type': ['PLA', 'PETG', 'PLA', 'PETG'],
    }, {'filament_extruder_variant': ['Bowden High Flow', 'Direct Drive Standard'],
        'nozzle_temperature': ['260', '240']})
    editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 3, 'high_flow')
    config = editor.get_config(loaded['project_id'])['config']
    assert config['nozzle_temperature'] == ['221', '231', '241', '251', '260']
    assert config['cool_plate_temp'] == ['50', '65', '55', '65']
    assert config['filament_map'] == ['1', '1', '1', '2']


def test_unknown_embedded_parent_saves_explicit_active_bambu_variant(tmp_path):
    editor, loaded, _, _, _ = flow_editor(tmp_path, {
        'filament_settings_id': ['Unknown embedded filament', 'other'], 'filament_volume_map': ['1', '0'],
    })
    saved = editor.save_preset(loaded['project_id'], 'filament', 'Standalone high-flow', 0)
    assert saved['inherits'] == ''
    assert saved['config']['filament_extruder_variant'] == ['Direct Drive High Flow']
    assert saved['config']['nozzle_temperature'] == ['231']


@pytest.mark.parametrize('key,preset_values,mode,expected,default,cardinality', [
    ('filament_adhesiveness_category', ['300'], 'standard', ['300', '0'], '0', 2),
    ('slow_down_min_speed', ['20', '30'], 'high_flow', ['10', '30', '10'], '10', 3),
])
def test_missing_vector_uses_pinned_native_default_for_other_materials_and_modes(
        tmp_path, key, preset_values, mode, expected, default, cardinality):
    editor, loaded, source, canonical, parent = flow_editor(
        tmp_path, {key: None}, {key: preset_values}, engine_commit=NATIVE_SOURCE)
    before = source.read_bytes(), canonical.read_bytes(), parent.read_bytes()
    selected = editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0, mode)
    assert editor.get_config(loaded['project_id'])['config'][key] == expected
    assert selected['native_defaults_applied'] == [{
        'key': key, 'default': default, 'cardinality': cardinality,
        'source': {'repository': 'bambulab/BambuStudio', 'path': 'src/libslic3r/PrintConfig.cpp',
                   'source_commit': NATIVE_SOURCE},
    }]
    assert (source.read_bytes(), canonical.read_bytes(), parent.read_bytes()) == before


@pytest.mark.parametrize('key,record_changes,engine_commit,reason', [
    ('filament_adhesiveness_category', {'default': '100 + 200'}, NATIVE_SOURCE, 'literal'),
    ('filament_retraction_length', {}, NATIVE_SOURCE, 'nullable'),
    ('filament_adhesiveness_category', {}, '1' * 40, 'commit'),
    ('filament_adhesiveness_category', {'default': '-1'}, NATIVE_SOURCE, 'outside'),
    ('filament_adhesiveness_category', {'default': 'true'}, NATIVE_SOURCE, 'expected'),
    ('filament_adhesiveness_category', {'default': '[100]'}, NATIVE_SOURCE, 'primitive'),
    ('filament_adhesiveness_category', {'default': 'NaN'}, NATIVE_SOURCE, 'finite'),
    ('filament_adhesiveness_category', {'default': None}, NATIVE_SOURCE, 'literal'),
])
def test_missing_vector_rejects_unsafe_native_defaults_atomically(
        tmp_path, monkeypatch, key, record_changes, engine_commit, reason):
    from bambu_studio_mcp import presets
    document = copy.deepcopy(presets._schema_doc())
    document[key].update(record_changes)
    monkeypatch.setattr(presets, '_schema_doc', lambda: document)
    editor, loaded, source, canonical, parent = flow_editor(
        tmp_path, {key: None}, {key: ['300']}, engine_commit=engine_commit)
    before = source.read_bytes(), canonical.read_bytes(), parent.read_bytes()
    with pytest.raises(ValueError, match=reason):
        editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0)
    assert editor.get_status(loaded['project_id'])['sha256'] == loaded['sha256']
    assert (source.read_bytes(), canonical.read_bytes(), parent.read_bytes()) == before


@pytest.mark.parametrize('value', [[], ['111'], '111'])
def test_native_default_never_repairs_an_existing_malformed_vector(tmp_path, value):
    key = 'filament_adhesiveness_category'
    editor, loaded, _, _, _ = flow_editor(
        tmp_path, {key: value}, {key: ['300']}, engine_commit=NATIVE_SOURCE)
    with pytest.raises(ValueError, match='cardinality'):
        editor.select_preset(loaded['project_id'], 'filament', '_GENERIC_PETG', 0)
    assert editor.get_status(loaded['project_id'])['sha256'] == loaded['sha256']
