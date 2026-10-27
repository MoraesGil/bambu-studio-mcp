"""Real MCP stdio consumer exercises every public tool with a bound Bambu app.

This is one synthetic X1C/PLA workflow, not all values/platforms or physical proof.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .conftest import record
from .harness import box_mesh, observe


async def test_every_mcp_tool_in_a_real_bambu_workflow(native_engine, tmp_path):
    vertices, faces = box_mesh((0, 0, 0), (12, 12, 4))
    mesh = tmp_path / 'public-box.obj'
    mesh.write_text('\n'.join([*(f'v {x} {y} {z}' for x, y, z in vertices),
                                *(f'f {a+1} {b+1} {c+1}' for a, b, c in faces)]) + '\n')
    mesh_hash = hashlib.sha256(mesh.read_bytes()).hexdigest()
    binding = tmp_path / 'engine.json'
    binding.write_text(json.dumps(native_engine.manifest))
    arguments = ['-m', 'bambu_studio_mcp.cli', '--engine', str(binding), '--artifacts',
                 str(tmp_path / 'artifacts'), '--timeout', '120', 'serve', '--no-update-check']
    params = StdioServerParameters(command=sys.executable, args=arguments)
    called = set()
    source_path = None
    try:
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=180) as session:
                await session.initialize()
                declared = {tool.name for tool in (await session.list_tools()).tools}

                async def call(tool_name, **values):
                    result = await session.call_tool(tool_name, values)
                    assert not result.is_error, (tool_name, result.content)
                    called.add(tool_name)
                    return result.structured_content or json.loads(result.content[0].text)

                status = await call('engine_status', check_updates=False)
                assert status['source_commit'] == native_engine.manifest['source_commit']
                await call('search_settings', query='layer height', limit=2)
                assert (await call('describe_setting', key='layer_height'))['type'] == 'coFloat'
                presets = await call('list_presets', type='print', include_system=True)
                assert presets['presets']
                await call('get_preset_config', type='print', name='0.20mm Standard @BBL X1C')
                created = await call('create_project', mesh_paths=[str(mesh)],
                    printer_preset='Bambu Lab X1 Carbon 0.4 nozzle',
                    process_preset='0.20mm Standard @BBL X1C', filament_presets=['Generic PLA'])
                source_path = Path(created['path'])
                source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
                state = await call('load_model', path=str(source_path))
                pid = state['project_id']
                await call('get_status', project_id=pid)
                config = (await call('get_config', project_id=pid,
                                     keys=['layer_height', 'outer_wall_speed']))['config']
                objects = (await call('list_objects', project_id=pid))['objects']
                root_id = objects[0]['object_id']
                assert (await call('list_plates', project_id=pid))['plates']

                async def edit(tool_name, **values):
                    nonlocal state
                    state = await call(tool_name, project_id=pid, expected_sha256=state['sha256'], **values)
                    assert Path(state['path']).is_file()
                    return state

                await edit('set_config', changes={'enable_arc_fitting': False, 'curr_bed_type': 'Cool Plate',
                    'cool_plate_temp': [50], 'cool_plate_temp_initial_layer': [50],
                    'additional_cooling_fan_speed': [0]})
                stale = await session.call_tool('set_config', {'project_id': pid,
                    'expected_sha256': created['sha256'], 'changes': {'layer_height': 0.12}})
                assert stale.is_error
                await edit('set_object_config', object_id=root_id, changes={'wall_loops': 3})
                await edit('add_modifier', object_id=root_id, center=[0., 0., 2.],
                           dimensions=[4., 4., 4.], changes={
                               'outer_wall_speed': [20.] * len(config['outer_wall_speed'])})
                await edit('duplicate_object', object_id=root_id)
                objects = (await call('list_objects', project_id=pid))['objects']
                duplicate = next(item['object_id'] for item in objects if item['object_id'] != root_id)
                await edit('transform_object', object_id=duplicate, translate=[20., 0., 0.])
                await edit('add_plate', name='Public workflow second plate')
                selection = await call('select_plate', project_id=pid, plate=2)
                state = selection
                await edit('move_object_to_plate', object_id=duplicate, plate=2)
                await edit('move_object_to_plate', object_id=duplicate, plate=1)
                await call('select_plate', project_id=pid, plate=1)
                await edit('delete_plate', plate=2)
                await edit('delete_object', object_id=duplicate)
                await edit('edit_project', operations=[{'operation': 'transform_object',
                    'object_id': root_id, 'translate': [1., 0., 0.]}])
                await edit('set_height_range', object_id=root_id, min_z=1., max_z=2., layer_height=0.12)
                await edit('set_layer_height', object_id=root_id, mode='adaptive', quality=0.5)
                await edit('set_layer_height', object_id=root_id, mode='reset')
                await edit('arrange_plate', plate=1)
                await edit('auto_orient', plate=1)
                state = await call('select_preset', project_id=pid, type='print',
                                   name='0.20mm Standard @BBL X1C', expected_sha256=state['sha256'])
                draft = await call('save_preset', project_id=pid, type='print', name='Native workflow draft')
                assert draft['name'] == 'Native workflow draft'
                await call('edit_preset', type='print', name='Native workflow draft', changes={'layer_height': 0.16})
                await call('rename_preset', type='print', name='Native workflow draft', new_name='Native workflow renamed')
                state = await call('select_preset', project_id=pid, type='print', name='Native workflow renamed',
                                   expected_sha256=state['sha256'])
                applied = await call('apply_and_slice', project_id=pid, changes={'layer_height': 0.28,
                    'enable_arc_fitting': False}, plate=1, expected_sha256=state['sha256'])
                state = applied['project']
                sliced = await call('slice_project', project_id=pid, plate=1)
                assert sliced['source']['sha256'] == state['sha256']
                direct = await call('slice_3mf', input_3mf=state['path'], plate=1)
                gcode = Path(direct['plates'][0]['gcode_path'])
                recovered = await call('recover_3mf', gcode_file=str(gcode))
                assert Path(recovered['path']).read_bytes() == Path(state['path']).read_bytes()
                observation = observe(gcode.read_text())
                assert observation['moves']
                levels = sorted({move['z'] for move in observation['moves'] if move['feature'] == 'Outer wall'})
                assert any(abs(b - a - 0.28) < 0.005 for a, b in zip(levels, levels[1:]))
                for operation, values in observation['commands']:
                    if operation == 'M106' and values.get('P') == 2:
                        assert values.get('S', 0) == 0
                for uri in (direct['props_uri'], direct['manifest_uri'], direct['plates'][0]['gcode_lines_uri']):
                    resource = await session.read_resource(uri)
                    assert resource.contents
                await call('delete_preset', type='print', name='Native workflow renamed')
                assert called == declared, declared - called
                assert len(called) == 35
                assert hashlib.sha256(mesh.read_bytes()).hexdigest() == mesh_hash
                assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash
                record(native_engine, 'public_workflow', 'pass', {
                    'tools': sorted(called), 'scope': '35 MCP tool names through real stdio; X1C/PLA synthetic workflow; '
                    'CRUD/revisions/plates/presets/adaptive/layout/recovery/resources and final observed layer spacing; '
                    'does not establish all inputs or 679 setting semantics'})
    except Exception as error:
        record(native_engine, 'public_workflow', 'fail', {'error_type': type(error).__name__, 'completed_tools': sorted(called)})
        raise
