# Linux and Docker: unvalidated native deployment

The Python contract suite can run on Linux without a slicer. That does not
validate the native Bambu Studio CLI on Linux. No Bambu Linux or Windows engine
acceptance, runtime bundle hash, downloadable artifact or ready-made container
image is claimed here. The primary documented native route is [macOS](MACOS.md).

## Supply and verify the native runtime

An operator must obtain/build an official Bambu Studio runtime and know its
source identity. The schema baseline is tag `v02.08.02.61`, commit
`926a7192574bcb9b3a732e1ec59a46d79cb45466`. Keep executable, matching resources
and libraries together. Do not reuse a Snapmaker executable, artifact URL,
AppImage hash, library recipe or acceptance report as Bambu evidence.

The binder supports standard package resource layouts. Custom FHS builds can
require adapter work; changing a manifest path cannot make an unsupported layout
valid. Determine the actual binary/resource locations and required runtime
libraries from the supplied Bambu package.

Before treating Linux deployment as supported, independently exercise native
slice, settings export, project creation, layout/orientation and the relevant
G-code oracles without a GUI/display server. Preserve identity and diagnostics.
Do not install a display server or silently switch engines to conceal failure.

## Experimental container layer

`Dockerfile` is a wrapper-service layer over an **operator-supplied native
runtime image**. It does not fetch/build Bambu Studio or claim to provide its
native shared libraries. The base image must already contain:

- the selected complete Bambu CLI package and its required libraries/environment;
- Python 3.11+, `venv`, pip, CA certificates and `sha256sum`;
- standard package paths accepted by the binder.

Build the Python wheel locally. Replace every value below with a verified local
image, real in-image paths, declared source identity and measured binary hash:

```sh
uv sync --frozen
uv build
docker build \
  --build-arg BAMBU_RUNTIME_IMAGE=your-native-runtime-image:local \
  --build-arg ENGINE_BINARY=/actual/package/bin/bambu-studio \
  --build-arg ENGINE_RESOURCES=/actual/package/resources \
  --build-arg ENGINE_COMMIT=926a7192574bcb9b3a732e1ec59a46d79cb45466 \
  --build-arg ENGINE_EXECUTABLE_SHA256=your-measured-sha256 \
  --tag bambu-studio-mcp:local .
```

The example image name is a local input you supply, not a published image.
The executable/resource paths are illustrative; inspect the real package.
The binary digest is checked before binding. Binding hashes resources but does
not execute the engine or prove that every shared library is present. The base
image must supply any `APPDIR`/library-path setup required by its native package.

This recipe has not received native Bambu Linux acceptance. A successful Docker
build or Python healthcheck cannot establish native slicing support.

## Optional Compose configuration

`compose.yml` serves authenticated MCP and REST on a loopback-published port.
It uses a non-root UID/GID, read-only model/preset mounts and a separate writable
artifact mount. Create those directories with suitable permissions and provide a
private bearer token file outside Git.

| Variable | Operator-provided value |
| --- | --- |
| `BAMBU_MCP_IMAGE` | Your locally built wrapper/runtime image |
| `BAMBU_MCP_MODELS_DIR` | Existing read-only directory of source meshes and 3MFs |
| `BAMBU_MCP_PRESETS_DIR` | Existing optional read-only preset directory |
| `BAMBU_MCP_ARTIFACTS_DIR` | Writable persistent revisions, drafts, jobs and cache |
| `BAMBU_MCP_TOKEN_FILE` | Existing private bearer token file |

`BAMBU_MCP_UID`, `BAMBU_MCP_GID` and `BAMBU_MCP_PORT` default to `1000`, `1000`
and `8080`. Set IDs to match the artifact-directory owner. Supply the required
variables through your local deployment configuration, then inspect it before
starting the service:

```sh
docker compose config
docker compose up -d
```

Do not publish the resolved configuration if it contains private paths or other
local details. Compose does not grant remote access. Private reverse proxies or
other network routes need explicit Host/Origin configuration.

Inside the container, use `/input`, `/presets` and `/artifacts` paths. Host paths
are not interchangeable with container paths. The native manifest is stored at
`/opt/slicer-mcp/engine.json`. The minimal healthcheck verifies HTTP liveness,
not a working native process. The internal network and `--no-update-check` avoid
an upstream request during normal startup.

Retained artifacts are durable application state; worker temporary directories
are separate. Never run two services against the same editing/artifact state
without a defined ownership/migration procedure. A Linux manifest cannot be
reused for macOS binaries. See [REST](docs/rest.md), [usage](docs/usage.md) and
[validation](TEST.md) before deployment.
