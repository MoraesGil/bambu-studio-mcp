"""Hand-checked Bambu 02.08.02.61 configuration contracts."""
import copy
import hashlib
import subprocess

import pytest

from bambu_studio_mcp.presets import validate_changes, load_schema
from bambu_studio_mcp.schema_extract import parse_print_config
from bambu_studio_mcp.domain.settings.normalization import normalize_filament_profile
from bambu_studio_mcp.domain.settings.filament import _filament_layout, _flow_index


def test_named_nullable_override_arrays_copy_native_type_and_limits():
    settings, _ = parse_print_config('''
    const std::vector<std::string> filament_extruder_override_keys = {"filament_retraction_length"};
    def = this->add("retraction_length", coFloats);
    def->label = "Retraction"; def->min = 0; def->max = 20;
    def->set_default_value(new ConfigOptionFloats{0.8});
    for (auto& opt_key : filament_extruder_override_keys) {
        auto it_opt = options.find(extruder_raw_key);
        def = this->add_nullable(opt_key, it_opt->second.type);
    }
    ''')
    assert settings['filament_retraction_length']['type'] == 'coFloats'
    assert settings['filament_retraction_length']['nullable'] is True
    assert settings['filament_retraction_length']['max'] == 20


def test_explicit_nullable_and_enum_map_are_extracted():
    settings, _ = parse_print_config('''
    static const t_config_enum_values s_keys_map_NozzleVolumeType = {
        {"Standard", nvtStandard}, {"High Flow", nvtHighFlow}
    };
    def = this->add("nozzle_volume_type", coEnums);
    def->enum_keys_map = &ConfigOptionEnum<NozzleVolumeType>::get_enum_values();
    def = this->add("filament_retract_length_nc", coFloats);
    def->nullable = true;
    def->set_default_value(new ConfigOptionFloatsNullable{10.});
    ''')
    assert settings['nozzle_volume_type']['enum_values'] == ['Standard', 'High Flow']
    assert settings['filament_retract_length_nc']['nullable'] is True


def test_point_groups_keep_one_native_polygon_string_per_extruder():
    assert validate_changes({'extruder_printable_area': [
        [[0, 0], [256, 0], [256, 256]], '0x0,128x0,128x128']}, {}) == {
            'extruder_printable_area': ['0x0,256x0,256x256', '0x0,128x0,128x128']}
    with pytest.raises(ValueError, match='cardinality'):
        validate_changes({'extruder_printable_area': ['0x0,1x0,1x1']},
                         {'extruder_printable_area': ['0x0', '0x0']})
    for invalid in (['0x0,bad'], [[[0, float('nan')]]], ['0x0#1x1']):
        with pytest.raises(ValueError):
            validate_changes({'extruder_printable_area': invalid}, {})


def test_profile_variants_pad_only_declared_bambu_parameters():
    schema = {'_meta': {'variant_keys': {'filament': ['nozzle_temperature']}}}
    profile = {'filament_extruder_variant': ['Direct Drive Standard', 'Direct Drive High Flow'],
               'nozzle_temperature': ['240'], 'additional_cooling_fan_speed': ['0']}
    original = copy.deepcopy(profile)
    normalized = normalize_filament_profile(profile, schema)
    assert normalized['nozzle_temperature'] == ['240', '240']
    assert normalized['additional_cooling_fan_speed'] == ['0']
    assert profile == original
    with pytest.raises(ValueError, match='variant'):
        normalize_filament_profile({**profile, 'nozzle_temperature': ['200', '210', '220']}, schema)


def test_filament_variant_lookup_uses_self_indices_and_extruder_mapping():
    layout = _filament_layout({
        'filament_settings_id': ['one', 'two'], 'filament_map': ['2', '1'],
        'filament_volume_map': ['1', '0'], 'extruder_type': ['Direct Drive', 'Bowden'],
        'filament_self_index': ['2', '1', '1'],
        'filament_extruder_variant': ['Direct Drive Standard', 'Bowden High Flow', 'Bowden Standard'],
    })
    assert _flow_index(layout, 0, 'high_flow') == 1
    assert _flow_index(layout, 0, 'standard') == 2
    assert _flow_index(layout, 1, 'standard') == 0
    with pytest.raises(ValueError, match='variant'):
        _flow_index(layout, 1, 'high_flow')


def test_catalog_rejects_snapmaker_only_fields_and_accepts_bambu_volume_enum():
    schema = load_schema()
    for key in ('filament_flow_support', 'filament_flow_step_size', 'filament_volume_type',
                'project_schema_version', 'process_flow_support'):
        assert key not in schema
        with pytest.raises(ValueError, match='Unknown'):
            validate_changes({key: ['standard']}, {})
    assert validate_changes({'nozzle_volume_type': ['Standard', 'High Flow', 'E3D High Flow']}, {}) == {
        'nozzle_volume_type': ['Standard', 'High Flow', 'E3D High Flow']}
    with pytest.raises(ValueError):
        validate_changes({'nozzle_volume_type': ['high_flow']}, {})


def test_generator_binds_bambu_inputs_without_a_project_schema_registry(tmp_path):
    from bambu_studio_mcp.schema_extract import generate_schema
    native = tmp_path / 'native'
    lib = native / 'src/libslic3r'
    lib.mkdir(parents=True)
    source = lib / 'PrintConfig.cpp'
    definitions = '\n'.join(f'def = this->add("setting_{i}", coFloat); def->min = 0;' for i in range(600))
    lists = '\n'.join(f'std::set<std::string> {name} = {{"setting_{i}"}};' for i, name in enumerate((
        'filament_options_with_variant', 'print_options_with_variant', 'printer_options_with_variant_1',
        'printer_options_with_variant_2', 'printer_extruder_options')))
    source.write_text(definitions + '\n' + lists + '\nCLIActionsConfigDef::CLIActionsConfigDef() {\n'
                      'def = this->add("slice", coInt); }')
    for name in ('PrintConfig.hpp', 'Config.hpp', 'PresetBundle.cpp'):
        (lib / name).write_text('// synthetic native dependency\n')
    (native / 'version.inc').write_text('set(SLIC3R_VERSION "02.08.02.61")\n')
    subprocess.run(['git', 'init', '-q', str(native)], check=True)
    subprocess.run(['git', '-C', str(native), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(native), '-c', 'user.name=Fixture', '-c',
                    'user.email=fixture@example.invalid', 'commit', '-qm', 'fixture'], check=True)
    commit = subprocess.check_output(['git', '-C', str(native), 'rev-parse', 'HEAD'], text=True).strip()
    doc = generate_schema(source, commit)
    assert doc['_meta']['setting_count'] == 600
    assert doc['_meta']['native_version'] == '02.08.02.61'
    assert doc['_meta']['source_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert doc['_meta']['variant_keys'] == {'filament': ['setting_0'], 'process': ['setting_1'],
                                          'printer': ['setting_2'], 'printer_silent': ['setting_3']}
    assert doc['setting_3']['variant_domain'] == 'printer'
    assert doc['setting_3']['variant_stride'] == 2
    assert 'slice' not in doc
    source.write_text(source.read_text() + '\n// changed after binding\n')
    with pytest.raises(ValueError, match='does not match'):
        generate_schema(source, commit)
