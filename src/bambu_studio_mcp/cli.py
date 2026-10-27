"""Bambu Studio command entry point and explicit service lifecycle."""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .bootstrap import build_editor, build_slicer
from .domain.types.json import JsonObject, decode_object
from .engine import Engine
from .service import Slicer, write_json


def _parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog='bambu-studio-mcp')
    command.add_argument('--engine', type=Path, default=Path('engine.json'))
    command.add_argument('--artifacts', type=Path)
    command.add_argument('--timeout', type=float, default=1800)
    command.add_argument('--presets', type=Path, action='append', default=[],
                         help='Additional read-only preset root (repeatable); edits are artifact drafts only')
    subcommands = command.add_subparsers(dest='command', required=True)
    subcommands.add_parser('discover', help='Locate installed Bambu Studio engines without binding or downloading')
    binding = subcommands.add_parser('bind-engine', help='Record an explicitly selected official Bambu Studio build')
    for option, value_type in (('binary', Path), ('resources', Path), ('commit', str)):
        binding.add_argument('--' + option, type=value_type, required=True)
    subcommands.add_parser('status').add_argument('--check-updates', action='store_true')
    creation = subcommands.add_parser('create', help='Create an editable native 3MF from STL/OBJ meshes')
    creation.add_argument('mesh_paths', nargs='+')
    for option in ('printer', 'process'):
        creation.add_argument('--' + option, required=True)
    creation.add_argument('--filament', action='append', required=True)
    slicing = subcommands.add_parser('slice')
    slicing.add_argument('input_3mf')
    slicing.add_argument('--plate', type=int, default=0)
    for flag in ('force', 'strict-file-version'):
        slicing.add_argument('--' + flag, action='store_true')
    subcommands.add_parser('recover').add_argument('gcode_file')
    serving = subcommands.add_parser('serve')
    serving.add_argument('--transport', choices=['stdio', 'streamable-http'], default='stdio')
    serving.add_argument('--no-update-check', action='store_true')
    serving.add_argument('--host', default='127.0.0.1')
    serving.add_argument('--port', type=int, default=8080)
    serving.add_argument('--auth-token-file', type=Path)
    serving.add_argument('--allowed-host', action='append', default=[],
                         help='Exact HTTP Host header, including port (repeatable); defaults to loopbacks')
    serving.add_argument('--allowed-origin', action='append', default=[],
                         help='Exact allowed HTTP Origin (repeatable); absent Origin is accepted')
    return command


def _serve(options: argparse.Namespace, slicer: Slicer) -> None:
    if options.transport == 'stdio':
        from .server import build_server
        if not options.no_update_check:
            slicer.engine.status(check_remote=True)
        build_server(slicer, options.presets).run(transport='stdio', show_banner=False)
        return
    port: int = options.port
    if not 0 < port < 65536:
        raise ValueError('HTTP port must be between 1 and 65535')
    if options.auth_token_file is None:
        raise ValueError('Streamable HTTP requires --auth-token-file')
    import uvicorn
    from .http_server import build_http_app
    loopbacks = ('127.0.0.1', 'localhost', '[::1]')
    hosts = options.allowed_host or [f'{name}:{port}' for name in loopbacks]
    if port == 80 and not options.allowed_host:
        hosts.extend(loopbacks)
    app = build_http_app(slicer, options.presets, auth_token_file=options.auth_token_file,
                         allowed_hosts=hosts, allowed_origins=options.allowed_origin)
    if not options.no_update_check:
        slicer.engine.status(check_remote=True)
    uvicorn.run(app, host=options.host, port=port, workers=1, access_log=False)


def _dispatch(options: argparse.Namespace) -> JsonObject | None:
    name: str = options.command
    if name == 'discover':
        from .adapters.bambu.discovery import discover_installations
        return {'installations': list(discover_installations())}
    if name == 'bind-engine':
        manifest = Engine.bind(options.binary, options.commit, options.resources)
        target: Path = options.engine
        target.parent.mkdir(parents=True, exist_ok=True)
        write_json(target, manifest)
        return manifest
    manifest_path: Path = options.engine
    native = Engine(decode_object(manifest_path.read_bytes(), 'Engine manifest'))
    if name == 'status':
        return native.status(check_remote=options.check_updates)
    workspace = options.artifacts or manifest_path.resolve().parent / 'artifacts'
    slicer = build_slicer(native, workspace, options.timeout)
    match name:
        case 'slice':
            return slicer.slice_3mf(options.input_3mf, options.plate, options.force,
                                    not options.strict_file_version)
        case 'recover':
            return slicer.recover_3mf(options.gcode_file)
        case 'create':
            editor = build_editor(slicer, options.presets)
            return editor.create_project(options.mesh_paths, options.printer,
                                         options.process, options.filament)
        case 'serve':
            _serve(options, slicer)
            return None
        case _:
            raise ValueError(f'Unknown command: {name}')


def main(argv: Sequence[str] | None = None) -> None:
    options = _parser().parse_args(argv)
    try:
        value = _dispatch(options)
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as failure:
        destination = sys.stderr if options.command == 'serve' else sys.stdout
        print(json.dumps({'error': str(failure)}), file=destination)
        raise SystemExit(1) from failure
    if value is not None:
        print(json.dumps(value, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
