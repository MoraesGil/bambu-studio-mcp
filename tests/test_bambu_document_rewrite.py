"""Regressions discovered while replacing the Bambu package implementation."""
import zipfile
from xml.etree import ElementTree as ET

import pytest

from bambu_studio_mcp.adapters.three_mf.document import Project, PROFILE, CUT, _read_xml, _xml_bytes, _tree_state
from bambu_studio_mcp.adapters.three_mf.geometry import Geometry


def test_serializing_document_restores_process_namespace_registry():
    root, namespaces = _read_xml(b'<model xmlns="urn:bambu-regression" xmlns:q="urn:unused" requiredextensions="q"/>')
    previous = dict(ET._namespace_map)
    serialized = _xml_bytes(root, namespaces)
    assert dict(ET._namespace_map) == previous
    assert b'xmlns:q="urn:unused"' in serialized


def test_native_input_can_select_again_without_mutating_source_view(tmp_path):
    core = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
    source = tmp_path / 'source.3mf'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('3D/3dmodel.model', f'''<model xmlns="{core}"><resources>
          <object id="1" name="original"><mesh><vertices>
            <vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>
          </vertices><triangles><triangle v1="0" v2="1" v3="2"/></triangles></mesh></object>
          </resources><build><item objectid="1"/><item objectid="1"/></build></model>''')
        archive.writestr('Auxiliaries/opaque.bin', b'\x00\xffopaque')
    geometry = Geometry(source)
    original_model = ET.tostring(geometry.model)
    assert geometry.native_input(tmp_path / 'first.3mf', {(1, 0)}) == {1: [(1, 0)]}
    assert ET.tostring(geometry.model) == original_model
    assert geometry.native_input(tmp_path / 'second.3mf', {(1, 1)}) == {1: [(1, 1)]}
    with zipfile.ZipFile(tmp_path / 'second.3mf') as archive:
        model = ET.fromstring(archive.read('3D/3dmodel.model'))
        assert len(model.find('{*}build')) == 1
        assert archive.read('Auxiliaries/opaque.bin') == b'\x00\xffopaque'


def profile_project(path, profile):
    core = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('3D/3dmodel.model', f'''<model xmlns="{core}"><resources>
          <object id="20"/><object id="40"/>
          </resources><build><item objectid="40"/><item objectid="20"/></build></model>''')
        archive.writestr(PROFILE, profile)
        archive.writestr('Auxiliaries/opaque.bin', b'\x00\xffopaque')
    return path


def test_old_wrapper_v2_profiles_migrate_to_native_build_ordinals(tmp_path):
    source = profile_project(tmp_path / 'old.3mf',
        'layer_heights_profile_format_version=2\nobject_id=20|0;0.1;1;0.1;2;0.2\n'
        'object_id=40|0;0.2;1;0.2;3;0.3\n')
    original = source.read_bytes()
    project = Project(source)
    before = project.get_layer_profile(20)
    target = tmp_path / 'native.3mf'
    project.save(target)
    with zipfile.ZipFile(target) as archive:
        profile = archive.read(PROFILE).decode()
        assert 'format_version' not in profile
        assert [line.partition('|')[0] for line in profile.splitlines()] == ['object_id=1', 'object_id=2']
        assert archive.read('Auxiliaries/opaque.bin') == b'\x00\xffopaque'
    assert source.read_bytes() == original
    native = Project(target)
    assert native.get_layer_profile(20) == before
    assert native.get_layer_profile(40) == [0, .2, 1, .2, 3, .3]
    native.delete_object(40)
    assert native.get_layer_profile(20) == before
    assert native.data[PROFILE].startswith(b'object_id=1|')


def test_duplicate_with_invalid_auxiliary_ordinal_is_atomic(tmp_path):
    source = profile_project(tmp_path / 'invalid.3mf', 'object_id=1|0;.2;1;.2;2;.2\n')
    project = Project(source)
    project.data[CUT] = b'<objects><object id="99"/></objects>'
    before = _tree_state(project.model), _tree_state(project.settings), project.data.copy()
    with pytest.raises(ValueError, match='ordinal'):
        project.duplicate_object(40)
    assert (_tree_state(project.model), _tree_state(project.settings), project.data) == before


def test_native_selection_reindexes_ordinal_height_metadata(tmp_path):
    source = profile_project(tmp_path / 'two.3mf',
        'object_id=1|0;.2;1;.2;3;.3\nobject_id=2|0;.1;1;.1;2;.2\n')
    target = tmp_path / 'one.3mf'
    geometry = Geometry(source)
    geometry.native_input(target, {(20, 0)})
    selected = Project(target)
    assert selected.get_layer_profile(20) == [0, .1, 1, .1, 2, .2]
    assert selected.data[PROFILE].startswith(b'object_id=1|')
