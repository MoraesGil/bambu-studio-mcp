"""Observable storage and admission failures covered during service replacement."""
import asyncio
import copy
import threading
from pathlib import Path

import pytest


def test_json_boundary_rejects_nonfinite_numbers_and_cycles():
    from bambu_studio_mcp.domain.types.json import decode_json, is_json_value, json_object
    for text in ('NaN', 'Infinity', '-Infinity', '1e999'):
        with pytest.raises(ValueError):
            decode_json(text)
    for number in (float('nan'), float('inf')):
        assert not is_json_value({'value': number})
        with pytest.raises(ValueError):
            json_object({'value': number})
    cycle = []
    cycle.append(cycle)
    assert not is_json_value(cycle)


@pytest.mark.asyncio
async def test_async_admission_shares_the_synchronous_gate():
    from bambu_studio_mcp.application.services.admission import NativeAdmission
    admission = NativeAdmission()
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()
    def first():
        with admission.session():
            entered.set()
            assert release.wait(3)
    first_thread = threading.Thread(target=first)
    first_thread.start()
    assert await asyncio.to_thread(entered.wait, 3)
    second = asyncio.create_task(admission.run(second_entered.set))
    try:
        assert not await asyncio.to_thread(second_entered.wait, 0.1)
    finally:
        release.set()
        await second
        first_thread.join(3)
    assert second_entered.is_set()


def test_draft_repository_refuses_direct_removal_of_an_external_record(tmp_path):
    from bambu_studio_mcp.adapters.storage.presets import FilesystemPresetRepository
    from bambu_studio_mcp.engine import file_hash
    repository = FilesystemPresetRepository(tmp_path / 'resources', [], tmp_path / 'drafts')
    installed = tmp_path / 'installed.json'
    installed.write_text('{"name":"Installed"}')
    record = {'name': 'Installed', 'type': 'print', 'source': 'system',
              'path': str(installed), 'sha256': file_hash(installed), 'raw': {}, 'read_only': True}
    with pytest.raises(ValueError, match='draft|read.only'):
        repository.remove(record)
    assert installed.exists()


def test_stale_repository_selection_cannot_restore_an_old_revision(tmp_path):
    from test_editor import project
    from test_service import boundary
    from bambu_studio_mcp.editor import Editor
    slicer, _ = boundary(tmp_path)
    editor = Editor(slicer)
    loaded = editor.load_model(str(project(tmp_path / 'source.3mf')))
    repository = editor.repository
    stale = copy.deepcopy(repository.state(loaded['project_id']))
    changed = editor.set_config(loaded['project_id'], {'layer_height': 0.16})
    with pytest.raises(ValueError, match='[Ss]tale'):
        repository.select_plate(stale, 1)
    assert repository.state(loaded['project_id'])['sha256'] == changed['sha256']


def test_atomic_json_write_rejects_nonfinite_without_publishing(tmp_path):
    from bambu_studio_mcp.adapters.storage.artifacts import write_json
    target = tmp_path / 'record.json'
    target.write_text('{"old":true}\n')
    with pytest.raises(ValueError):
        write_json(target, {'bad': float('nan')})
    assert target.read_text() == '{"old":true}\n'
    assert [path.name for path in tmp_path.iterdir()] == ['record.json']


def test_rename_rolls_back_new_draft_when_old_path_becomes_a_symlink(tmp_path):
    from bambu_studio_mcp.adapters.storage.presets import FilesystemPresetRepository
    from bambu_studio_mcp.engine import file_hash
    repository = FilesystemPresetRepository(tmp_path / 'resources', [], tmp_path / 'drafts')
    previous = repository.draft_path('print', 'Old')
    replacement = repository.draft_path('print', 'New')
    repository.write(previous, {'name': 'Old', 'type': 'process'})
    record = repository.records()[0]
    repository.write(replacement, {'name': 'New', 'type': 'process'})
    old_path = Path(previous)
    installed = tmp_path / 'installed.json'
    installed.write_text('{"name":"Installed"}')
    original_hash = file_hash(installed)
    old_path.unlink()
    old_path.symlink_to(installed)
    with pytest.raises(ValueError):
        repository.remove(record, rollback_path=replacement, action='rename')
    assert not Path(replacement).exists()
    assert file_hash(installed) == original_hash
