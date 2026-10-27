"""One access policy protects the Bambu MCP and REST transports."""
from __future__ import annotations

import hmac
from collections.abc import Sequence
from pathlib import Path

from fastmcp.server.http import StarletteWithLifespan
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ...service import Slicer
from ..mcp.server import build_server
from .rest import rest_routes


def _token_from_file(path: str | Path | None) -> bytes:
    if path is None:
        raise ValueError('Streamable HTTP requires --auth-token-file')
    try:
        secret = Path(path).read_bytes().strip()
    except (OSError, TypeError, ValueError):
        raise ValueError('Cannot read the HTTP authentication token file') from None
    if secret and all(33 <= character <= 126 for character in secret):
        return secret
    raise ValueError('HTTP token file must contain one nonempty ASCII bearer token')


def _allowlist(values: Sequence[str], kind: str, required: bool = False) -> tuple[str, ...]:
    entries = tuple(values)
    if not entries and required:
        raise ValueError(f'{kind} allowlist must not be empty')
    for entry in entries:
        if not isinstance(entry, str) or not entry or '*' in entry or any(map(str.isspace, entry)):
            raise ValueError(f'{kind} allowlist requires exact values without wildcards or whitespace')
    return entries


class _AccessPolicy:
    def __init__(self, app: ASGIApp, token: bytes, allowed_hosts: tuple[str, ...],
                 allowed_origins: tuple[str, ...]) -> None:
        self.app, self.token = app, token
        self.hosts = frozenset(allowed_hosts)
        self.origins = frozenset(allowed_origins)

    def _rejection(self, scope: Scope) -> tuple[int, str] | None:
        headers = Headers(scope=scope)
        host = headers.getlist('host')
        if len(host) != 1 or host[0] not in self.hosts:
            return 421, 'Invalid Host'
        origin = headers.getlist('origin')
        if len(origin) > 1 or any(item not in self.origins for item in origin):
            return 403, 'Invalid Origin'
        if (scope['method'], scope['path']) == ('GET', '/health'):
            return None
        credentials = headers.getlist('authorization')
        if len(credentials) != 1:
            return 401, 'Unauthorized'
        parts = credentials[0].split(' ', 1)
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return 401, 'Unauthorized'
        if hmac.compare_digest(parts[1].encode('latin-1'), self.token):
            return None
        return 401, 'Unauthorized'

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        denial = self._rejection(scope) if scope['type'] == 'http' else None
        if denial is None:
            await self.app(scope, receive, send)
            return
        code, message = denial
        response = JSONResponse({'error': message}, status_code=code,
                                headers={'WWW-Authenticate': 'Bearer'} if code == 401 else None)
        await response(scope, receive, send)


def build_http_app(slicer: Slicer, preset_dirs: Sequence[Path] = (), *,
                   auth_token_file: str | Path | None = None, allowed_hosts: Sequence[str] = (),
                   allowed_origins: Sequence[str] = ()) -> StarletteWithLifespan:
    secret = _token_from_file(auth_token_file)
    hosts = _allowlist(allowed_hosts, 'Host', required=True)
    origins = _allowlist(allowed_origins, 'Origin')
    protocol = build_server(slicer, preset_dirs)

    @protocol.custom_route('/health', methods=['GET'])
    async def health(request: Request) -> JSONResponse:
        return JSONResponse(dict(status='ok'))

    application = protocol.http_app(path='/mcp', stateless_http=True, json_response=True,
                                    allowed_hosts=list(hosts), allowed_origins=list(origins))
    application.router.routes.extend(rest_routes(slicer))
    application.add_middleware(_AccessPolicy, token=secret, allowed_hosts=hosts,
                               allowed_origins=origins)
    return application
