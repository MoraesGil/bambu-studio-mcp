# Native macOS setup

Use the official Bambu Studio app bundle with its matching resources and
libraries. The documented baseline is `02.08.02.61`, source tag
`v02.08.02.61` at `926a7192574bcb9b3a732e1ec59a46d79cb45466`.
The default installed path is `/Applications/BambuStudio.app`.

Basic synthetic slice and native project-creation feasibility have been observed
on macOS. Full acceptance is a separate [validation workflow](TEST.md). The CLI
uses the native executable inside the app; the GUI can remain closed. No Linux
VM, Docker, Xvfb or ML runtime is required for this route.

## Bash: one saved project, one plate

```sh
bash bin/bambu-slice.sh /absolute/project.3mf 1 /absolute/plate1.gcode
```

The destination directory must exist, the filename must be new and its volume
must support hard links. The launcher copies the saved source into a private
run directory and passes through native diagnostics/failure status. Temporary
files are removed on exit. It keeps no artifact database or result cache.

Set `BAMBU_APP="/absolute/BambuStudio.app"` to choose another installed bundle.
The optional fourth argument `--allow-newer-file` bypasses the version gate after
an explicit compatibility decision. The launcher is strict without that flag.

## Python: discover, bind and create

Install Python 3.11+ and uv, then use a checkout of this repository:

```sh
uv sync --frozen
uv run bambu-studio-mcp discover

uv run bambu-studio-mcp --engine /absolute/runtime/engine-macos.json bind-engine \
  --binary /Applications/BambuStudio.app/Contents/MacOS/BambuStudio \
  --resources /Applications/BambuStudio.app/Contents/Resources \
  --commit 926a7192574bcb9b3a732e1ec59a46d79cb45466
```

Discovery is read-only and does not bind or install anything. Verify the source
identity of the selected build before supplying `--commit`. The binder measures
binary/resource hashes and rejects mismatched package layouts. Preserve the
complete app bundle and create a new binding after engine changes.

Create a project with explicit presets:

```sh
uv run bambu-studio-mcp --engine /absolute/runtime/engine-macos.json \
  --artifacts /absolute/artifacts create /absolute/part.stl \
  --printer "Bambu Lab X1 Carbon 0.4 nozzle" \
  --process "0.20mm Standard @BBL X1C" \
  --filament "Generic PLA"
```

The native CLI creates a private 3MF. Use its returned path for subsequent
inspection and slicing. Source meshes and installed presets remain unchanged.
To slice an existing saved project:

```sh
uv run bambu-studio-mcp --engine /absolute/runtime/engine-macos.json \
  --artifacts /absolute/artifacts slice /absolute/project.3mf --plate 1 --force
```

`--force` bypasses cache. Retained jobs include source snapshots, G-code,
settings, hashes and diagnostics. The Python slice route permits the native
version override by default; use `--strict-file-version` for strict checking.
Do not use a container mount alias as a native host path.

## MCP over stdio or HTTP

Stdio is the default:

```sh
.venv/bin/bambu-studio-mcp --engine /absolute/runtime/engine-macos.json \
  --artifacts /absolute/artifacts serve --no-update-check
```

Use [client examples](docs/usage.md) to invoke `create_project`, `load_model`,
editing and slicing tools. For several clients or an application using REST,
start one authenticated listener. Create its private token file first:

```sh
mkdir -p /absolute/secrets
umask 077
openssl rand -hex 32 > /absolute/secrets/bambu-mcp-token

.venv/bin/bambu-studio-mcp --engine /absolute/runtime/engine-macos.json \
  --artifacts /absolute/artifacts serve --transport streamable-http \
  --host 127.0.0.1 --port 8080 --no-update-check \
  --auth-token-file /absolute/secrets/bambu-mcp-token \
  --allowed-host 127.0.0.1:8080 \
  --allowed-origin http://127.0.0.1:8080
```

MCP uses `http://127.0.0.1:8080/mcp`; REST uses `/api/v1` on the same listener.
Supply the token through the client's private secret storage. Host headers and
Origins must match the explicit allowlists. A loopback listener is local to the
Mac; remote/private transport configuration is a separate operation.

Each slice creates a native worker. Shared admission serializes native work
while project-state reads can continue. `engine_status` reports the bound
identity; upstream checks only produce notices.

A persistent service can use an operator-managed LaunchAgent with absolute
paths to the venv executable, engine manifest, artifact store and token. Keep
these local installation files outside Git. This guide does not install a
service, migrate another backend or certify a deployed environment.

## Interpretation limits

Sources and installed presets remain read-only. Private revisions are the
editable output. Inspect exact saved revisions and re-slice after changes.
The adaptive generator is not validated for Bambu and fails explicitly.
Equal settings or hashes do not prove GUI/CLI toolpath equivalence or physical
printing. See [TEST.md](TEST.md).
