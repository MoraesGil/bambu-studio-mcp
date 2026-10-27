"""Regressions for Bambu-specific process isolation and the direct CLI wrapper."""
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import zipfile

import pytest

from bambu_studio_mcp.adapters.bambu.engine import file_hash


def test_slice_snapshot_is_checked_before_native_launch(tmp_path, monkeypatch):
    from test_service import boundary, make_project
    from bambu_studio_mcp.application.contracts.slicing import SliceRequest
    from bambu_studio_mcp.adapters.bambu.runner import BambuProcess
    slicer, executable = boundary(tmp_path)
    workspace = slicer.artifacts.create(slicer.artifacts.prepare_source(str(make_project(tmp_path / 'source.3mf'))))
    with Path(workspace.source.path).open('ab') as stream:
        stream.write(b'changed snapshot')
    launched = []
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: launched.append(True))
    with pytest.raises(ValueError, match='snapshot changed'):
        BambuProcess(executable).run(workspace, SliceRequest(), slicer.engine.identity(), 3)
    assert not launched


def test_slice_timeout_stops_descendants_and_removes_platform_temporaries(tmp_path):
    from test_service import boundary, make_project
    from bambu_studio_mcp.engine import Engine
    from bambu_studio_mcp.service import Slicer
    original, binary = boundary(tmp_path)
    binary.write_text(f'''#!{sys.executable}
import os,pathlib,subprocess,sys,time
job=pathlib.Path(os.environ['TMPDIR']).parent
assert os.environ['TMP']==os.environ['TEMP']==str(job/'native-tmp')
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
(job/'child.pid').write_text(str(child.pid))
time.sleep(30)
''')
    slicer = Slicer(Engine(Engine.bind(binary, '1' * 40, original.engine.resources)), tmp_path / 'jobs', timeout=3)
    with pytest.raises(TimeoutError):
        slicer.slice_3mf(str(make_project(tmp_path / 'source.3mf')))
    job, = (slicer.root / 'runs').iterdir()
    assert not (job / 'native-tmp').exists()
    pid = int((job / 'child.pid').read_text())
    assert_process_stopped(pid)


def assert_process_stopped(pid):
    for _ in range(100):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        proc = Path(f'/proc/{pid}/stat')
        if proc.exists() and proc.read_text().split(')')[-1].split()[0] == 'Z':
            return
        time.sleep(.02)
    os.kill(pid, signal.SIGKILL)
    pytest.fail('Native descendant survived process-group cleanup')


def shell_boundary(tmp_path):
    app = tmp_path / 'BambuStudio.app'
    executable = app / 'Contents/MacOS/BambuStudio'
    executable.parent.mkdir(parents=True)
    executable.write_text(f'''#!{sys.executable}
import argparse,os,pathlib,subprocess,sys,time
p=argparse.ArgumentParser()
p.add_argument('--allow-newer-file',action='store_true')
for flag in ('slice','outputdir','datadir'): p.add_argument('--'+flag,required=True)
p.add_argument('project')
a=p.parse_args()
assert not any(k in os.environ for k in ('DISPLAY','WAYLAND_DISPLAY','BAMBU_ORCA_ALLOW_NEWER_FILE'))
pathlib.Path(os.environ['SHELL_PROBE']).write_text(str(a.allow_newer_file))
if os.environ.get('SHELL_SLEEP'):
 child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
 pathlib.Path(os.environ['SHELL_CHILD']).write_text(str(child.pid))
 time.sleep(30)
(pathlib.Path(a.outputdir)/('plate_'+a.slice+'.gcode')).write_text('G1 X1 E1\\n')
''')
    executable.chmod(0o755)
    return dict(os.environ, BAMBU_APP=str(app), SHELL_PROBE=str(tmp_path / 'probe'),
                SHELL_CHILD=str(tmp_path / 'child.pid'), BAMBU_TIMEOUT_SECONDS='5')


@pytest.mark.skipif(shutil.which('xmllint') is None or os.name != 'posix', reason='direct macOS CLI wrapper needs xmllint and POSIX')
@pytest.mark.parametrize('allowed', [False, True])
def test_direct_wrapper_preserves_source_and_passes_boolean_flag(tmp_path, allowed):
    from test_service import make_project
    source = make_project(tmp_path / 'input.3mf')
    digest = file_hash(source)
    output = tmp_path / 'output.gcode'
    wrapper = Path(__file__).parents[1] / 'bin/bambu-slice.sh'
    result = subprocess.run(['bash', str(wrapper), str(source), '1', str(output),
                             *(['--allow-newer-file'] if allowed else [])], env=shell_boundary(tmp_path),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert output.read_text() == 'G1 X1 E1\n'
    assert (tmp_path / 'probe').read_text() == str(allowed)
    assert file_hash(source) == digest
    assert not list(tmp_path.glob('.bambu-slice.*'))
    repeated = subprocess.run(['bash', str(wrapper), str(source), '1', str(output)], env=dict(os.environ),
                              capture_output=True, text=True)
    assert repeated.returncode == 64
    assert output.read_text() == 'G1 X1 E1\n'


@pytest.mark.skipif(shutil.which('xmllint') is None or os.name != 'posix', reason='direct macOS CLI wrapper needs xmllint and POSIX')
def test_direct_wrapper_rejects_foreign_marker_before_import(tmp_path):
    source = tmp_path / 'foreign.3mf'
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('3D/3dmodel.model', '<model><metadata name="Application">SnapmakerOrca-2</metadata></model>')
    wrapper = Path(__file__).parents[1] / 'bin/bambu-slice.sh'
    result = subprocess.run(['bash', str(wrapper), str(source), '1', str(tmp_path / 'output.gcode')],
                            env=shell_boundary(tmp_path), capture_output=True, text=True)
    assert result.returncode == 64
    assert 'Bambu Studio project' in result.stderr
    assert not (tmp_path / 'probe').exists()
    assert not list(tmp_path.glob('.bambu-slice.*'))


@pytest.mark.skipif(shutil.which('xmllint') is None or os.name != 'posix', reason='direct macOS CLI wrapper needs xmllint and POSIX')
def test_direct_wrapper_timeout_kills_group_and_never_publishes(tmp_path):
    from test_service import make_project
    source = make_project(tmp_path / 'input.3mf')
    environment = shell_boundary(tmp_path)
    environment.update(BAMBU_TIMEOUT_SECONDS='3', SHELL_SLEEP='1')
    wrapper = Path(__file__).parents[1] / 'bin/bambu-slice.sh'
    output = tmp_path / 'output.gcode'
    result = subprocess.run(['bash', str(wrapper), str(source), '1', str(output)], env=environment,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 124, result.stderr
    assert not output.exists()
    assert not list(tmp_path.glob('.bambu-slice.*'))
    assert_process_stopped(int((tmp_path / 'child.pid').read_text()))


def test_missing_binding_assets_have_the_public_validation_error(tmp_path):
    from bambu_studio_mcp.engine import Engine
    with pytest.raises(ValueError, match='executable is unavailable'):
        Engine.bind(tmp_path / 'missing', '1' * 40, tmp_path / 'resources')
