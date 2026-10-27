"""Run BambuStudio CLI actions with private data and temporary directories.

Each invocation uses a POSIX process group, so a timeout also stops children.
Action-specific request and output diagnostics survive every exit.
"""
from __future__ import annotations

import math
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from ...application.contracts.slicing import SliceRequest, SliceWorkspace
from ...domain.types.json import JsonObject
from ..storage.artifacts import write_json
from .engine import file_hash
from .project_format import ensure_bambu_project


def _stop_process(process: subprocess.Popen[str], operating_system: str) -> None:
    """Reap a timed-out CLI, including children it may have started."""
    if operating_system == 'nt':
        try:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        except OSError:
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


def run_command(argv: list[str], directory: Path, timeout: float, *,
                operating_system: str = os.name) -> tuple[int, float]:
    """Execute one CLI action; callers certify its particular output format.

    CLI::setup reads --datadir; platform temp APIs use TMPDIR/TMP/TEMP. Neither
    may escape the job. Compatibility is the CLI boolean flag, never an old
    fork-specific environment variable.
    """
    if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Native timeout must be a finite positive number')
    temporary = directory / 'native-tmp'
    temporary.mkdir(mode=0o700)
    environment = dict(os.environ)
    for key in ('DISPLAY', 'WAYLAND_DISPLAY', 'BAMBU_ORCA_ALLOW_NEWER_FILE'):
        environment.pop(key, None)
    environment.update(LC_ALL='C', TMPDIR=str(temporary) + os.sep,
                       TMP=str(temporary), TEMP=str(temporary))
    started = time.perf_counter()
    try:
        with (directory / 'engine.log').open('w', encoding='utf-8') as stream:
            child = subprocess.Popen(argv, cwd=directory, env=environment,
                                     stdout=stream, stderr=subprocess.STDOUT, text=True,
                                     start_new_session=operating_system != 'nt')
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                _stop_process(child, operating_system)
                raise TimeoutError(f'Native command timed out; diagnostics: {directory}') from error
            except BaseException:
                _stop_process(child, operating_system)
                raise
        return code, time.perf_counter() - started
    finally:
        shutil.rmtree(temporary)


class BambuProcess:
    def __init__(self, executable: Path, operating_system: str = os.name) -> None:
        self.executable = executable
        self.operating_system = operating_system

    def run(self, workspace: SliceWorkspace, request: SliceRequest,
            engine: JsonObject, timeout: float) -> float:
        source, output = Path(workspace.source.path), Path(workspace.directory)
        ensure_bambu_project(source)
        if file_hash(source) != workspace.source.sha256:
            raise ValueError('Source snapshot changed before native slicing')
        data = output / 'native-data'
        data.mkdir(mode=0o700)
        # export-settings writes effective global configuration. slice=0 means
        # all plates; Bambu writes plate_N.gcode underneath outputdir.
        argv = [str(self.executable)]
        if request.allow_newer_file:
            argv.append('--allow-newer-file')
        argv.extend(['--slice', str(request.plate), '--outputdir', str(output),
                     '--datadir', str(data), '--export-settings', str(output / 'native-settings.json'), str(source)])
        record: JsonObject = {
            'job_id': workspace.job_id, 'engine': engine, 'argv': list(argv),
            'source_sha256': workspace.source.sha256, 'requested_plate': request.plate,
            'compatibility_flags': {'allow_newer_file': request.allow_newer_file},
        }
        write_json(output / 'request.json', record)
        try:
            code, elapsed = run_command(argv, output, timeout, operating_system=self.operating_system)
            if code:
                write_json(output / 'failure.json', {**record, 'exit_code': code,
                                                    'log_path': str(output / 'engine.log')})
                raise RuntimeError(f'Native Bambu slice failed ({code}); diagnostics: {output}')
            if file_hash(source) != workspace.source.sha256:
                raise ValueError('Source snapshot changed during native slicing')
            return elapsed
        except TimeoutError:
            write_json(output / 'failure.json', {**record, 'reason': 'timeout',
                                                'log_path': str(output / 'engine.log')})
            raise
