"""Bambu-specific discovery and native invocation contracts."""
from pathlib import Path

import pytest

from bambu_studio_mcp.engine import Engine


def test_binding_targets_official_bambu_master(tmp_path):
    from test_service import boundary
    slicer, _ = boundary(tmp_path)
    assert slicer.engine.identity()['repository'] == 'bambulab/BambuStudio'
    assert slicer.engine.identity()['source_ref'] == 'master'


def test_slice_rejects_foreign_project_before_native_defaults_can_replace_saved_settings(tmp_path):
    from test_service import boundary, make_project
    slicer, _ = boundary(tmp_path)
    source = make_project(tmp_path/'foreign.3mf')
    import zipfile
    with zipfile.ZipFile(source) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    contents['3D/3dmodel.model'] = b'<model><resources/><build/></model>'
    with zipfile.ZipFile(source, 'w') as archive:
        for name, payload in contents.items():
            archive.writestr(name, payload)
    before = source.read_bytes()
    with pytest.raises(ValueError, match='Bambu Studio project'):
        slicer.slice_3mf(str(source))
    assert source.read_bytes() == before


def test_discovery_reports_installed_bundle_without_binding_or_writing(tmp_path, monkeypatch):
    from bambu_studio_mcp.adapters.bambu import discovery
    monkeypatch.setattr(discovery.sys, "platform", "darwin")
    discover_installations = discovery.discover_installations
    app = tmp_path / 'BambuStudio.app'
    binary = app / 'Contents/MacOS/BambuStudio'
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    (app / 'Contents/Resources').mkdir()
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*'))
    result = discover_installations([app])
    assert result == [{'executable': str(binary.resolve()),
                       'resources': str((app / 'Contents/Resources').resolve()),
                       'repository': Engine.repository, 'bound': False}]
    assert sorted(p.relative_to(tmp_path) for p in tmp_path.rglob('*')) == before


def test_discovery_ignores_missing_resources_and_unrelated_executables(tmp_path, monkeypatch):
    from bambu_studio_mcp.adapters.bambu import discovery
    monkeypatch.setattr(discovery.sys, "platform", "darwin")
    discover_installations = discovery.discover_installations
    binary = tmp_path / 'BambuStudio.app/Contents/MacOS/BambuStudio'
    binary.parent.mkdir(parents=True)
    binary.write_text('dummy')
    binary.chmod(0o755)
    assert discover_installations([binary.parent.parent.parent, tmp_path / 'missing']) == []


def test_cli_discovery_needs_no_engine_manifest(tmp_path, capsys):
    import json
    from bambu_studio_mcp.cli import main
    main(['--engine', str(tmp_path / 'absent.json'), 'discover'])
    result = json.loads(capsys.readouterr().out)
    assert isinstance(result['installations'], list)
    assert not (tmp_path / 'absent.json').exists()


@pytest.mark.parametrize('platform,name', [('linux', 'BambuStudio'), ('win32', 'bambu-studio.exe')])
def test_discovery_supports_explicit_binary_paths(tmp_path, platform, name, monkeypatch):
    import bambu_studio_mcp.adapters.bambu.discovery as discovery
    monkeypatch.setattr(discovery.sys, 'platform', platform)
    binary = tmp_path / ('bin' if platform == 'linux' else '') / name
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text('dummy')
    binary.chmod(0o755)
    resources = tmp_path / 'resources'
    resources.mkdir()
    assert discovery.discover_installations([binary])[0]['resources'] == str(resources.resolve())
