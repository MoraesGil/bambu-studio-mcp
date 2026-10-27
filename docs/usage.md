# Client and tool usage

The optional service offers 35 MCP tools, three resource templates and eight
REST OpenAPI paths. MCP and REST share one engine, native admission gate, cache
and artifact store. All clients share backend files/state; this is not a
multitenant upload service. Use separate loaded projects for independent work.

## Connect a client

Stdio is the default. Clients that accept `mcpServers` configuration can use:

```json
{
  "mcpServers": {
    "bambu-studio": {
      "command": "/absolute/bambu-studio-mcp/.venv/bin/bambu-studio-mcp",
      "args": [
        "--engine", "/absolute/runtime/engine.json",
        "--artifacts", "/absolute/artifacts",
        "serve", "--no-update-check"
      ]
    }
  }
}
```

For shared clients, start an authenticated Streamable HTTP listener as described
in [macOS setup](../MACOS.md). A client with URL/header configuration can use:

```json
{
  "mcpServers": {
    "bambu-studio": {
      "url": "http://127.0.0.1:8080/mcp",
      "headers": {"Authorization": "Bearer <token-from-private-client-storage>"}
    }
  }
}
```

Use the client's secret storage for the bearer value. Host allowlists match
exact headers, including ports. Origin allowlists match exact origins; absent
Origin is accepted. Token rotation requires service restart. `/health` is
minimal public liveness under the Host/Origin policy. REST is documented
[separately](rest.md); stdio exposes no HTTP routes.

The JSON examples below are MCP `tools/call` parameters, not raw HTTP requests.
Paths are server-side paths. Use `/input/...` only when that mount exists.

## Create or load a project

STL/OBJ mesh creation requires explicit printer, process and filament presets:

```json
{
  "name": "create_project",
  "arguments": {
    "mesh_paths": ["/absolute/part.stl"],
    "printer_preset": "Bambu Lab X1 Carbon 0.4 nozzle",
    "process_preset": "0.20mm Standard @BBL X1C",
    "filament_presets": ["Generic PLA"]
  }
}
```

The native CLI receives private mesh copies and resolved temporary preset
exports. It arranges the input and exports a new saved 3MF. Source meshes and
installed presets remain unchanged. Creation does not infer a printer or
material and does not itself produce G-code.

For an existing complete saved 3MF:

```json
{"name":"load_model","arguments":{"path":"/absolute/project.3mf"}}
```

Retain the returned `project_id`, `sha256` and private revision `path`. Use
`get_status`, `get_config`, `list_objects` and `list_plates` to inspect actual
object/part IDs, material indexes and plate membership. Request selected keys
to avoid reading an entire configuration unnecessarily:

```json
{"name":"get_config","arguments":{"project_id":"<project-id>","keys":["layer_height","curr_bed_type","filament_settings_id"]}}
```

There is no separate MCP export tool: each committed revision is already a
saved 3MF at the returned `path`.

## Edit a private revision

```json
{
  "name": "edit_project",
  "arguments": {
    "project_id": "<project-id>",
    "expected_sha256": "<latest-revision-sha256>",
    "operations": [
      {"operation": "set_config", "changes": {"layer_height": 0.16}},
      {"operation": "arrange_plate", "plate": 0}
    ]
  }
}
```

A successful batch creates one new revision and invalidates embedded old
G-code/previews. A failed batch does not advance state. A stale SHA rejects an
edit instead of replacing newer work. Custom G-code changes require explicit
opt-in; external post-processing scripts are unsupported.

`set_object_config` applies to an object or explicit `part_id`. `extruder=0`
inherits; `1..N` selects a logical material slot. `transform_object` accepts
translation deltas in millimeters, degree rotation deltas and scale factors,
preserving the instance pivot.

`add_modifier` creates a box in object-local XYZ coordinates with positive
millimeter dimensions and allowlisted regional speed overrides. Every instance
of that root shares the volume. Speeds are arrays, even for a single native
variant; preserve their native shape. See the
[modifier example](property-regressions.md#modifier-coordinates-and-scope).

Plate numbers are 1-based. `plate=0` applies to all applicable/unlocked plates.
All-plate arrangement persists native count/membership/transforms; a selected
plate that would overflow fails atomically. Plate deletion parks its objects
off-bed; `move_object_to_plate` can restore them. The final plate cannot be removed.

`set_height_range` uses object-relative Z bands. Clearing those bands preserves
an existing continuous profile. `set_layer_height(mode="reset")` clears both.
`set_layer_height(mode="adaptive")` generates a profile using the pinned Bambu
source algorithm. The wrapper applies explicit ranges as a separate overlay.
Profiles use Bambu-native ordinal records; old wrapper root-ID records remain
readable. Native acceptance covers fine/coarse/reset spacing on synthetic
geometry, not every nozzle/material combination or GUI equivalence.

## Presets and native material variants

`list_presets`, `get_preset_config`, `describe_setting` and `search_settings`
inspect read-only files and source metadata. Preset inheritance includes hashes
and rejects missing/cyclic/ambiguous parents. Include system presets explicitly
when listing them. Installed Bambu presets live under `resources/profiles/BBL`.
Additional `--presets` roots remain read-only.

Select an existing preset for a known project material:

```json
{
  "name": "select_preset",
  "arguments": {
    "project_id": "<project-id>",
    "type": "filament",
    "name": "Generic PLA",
    "filament_slot": 0,
    "flow_mode": "Standard",
    "expected_sha256": "<latest-revision-sha256>"
  }
}
```

`filament_slot` is 0-based. Supported native mode names are `Standard`,
`High Flow`, `TPU High Flow` and `E3D High Flow`; compatibility aliases
`standard` and `high_flow` remain accepted. These are Bambu nozzle-volume modes,
not additional physical bobbins. The project must have the matching material /
extruder variant. Missing or ambiguous mappings fail instead of editing another
material through the native global-index-zero fallback.

Native mapping uses `filament_extruder_variant`, `filament_self_index`,
`filament_map`, `filament_volume_map` and `extruder_type`. Preserve these arrays
and the source-derived variant-key cardinalities. Snapmaker flow-step fields
and segment counts do not describe this format. A single-material source preset
can use its safe native first-variant fallback; that does not authorize a
fallback into another project material.

Missing project values can be backfilled only from validated primitive literal
defaults with matching schema/engine identity and unambiguous cardinality.
Nullable defaults, unsupported expressions and malformed existing layouts fail.
Selection reports the values/default provenance it applied.

`save_preset` writes a new artifact-local draft from the selected material,
preserving the parent's other variant values. `edit_preset`, `rename_preset`
and `delete_preset` operate only on drafts. They cannot overwrite installed or
canonical profiles. Choosing a different material remains an explicit project
edit; comparing presets does not select one.

## Slice and inspect output

```json
{"name":"slice_project","arguments":{"project_id":"<project-id>","plate":1,"force":true}}
```

`slice_3mf` accepts a saved source path directly. `slice_project` captures the
current private revision. `apply_and_slice` commits settings before slicing;
if slicing fails, the successful edit remains available for inspection.
Re-slice after each relevant geometry, painting, support or setting change.

Jobs retain the exact source, plain `plate_N.gcode`, native settings, properties,
request/result manifests and native diagnostics. Global/per-plate property
exports do not exhaustively describe final per-region values. Native execution
is serialized, while state reads can continue. Later edits cannot replace an
in-flight captured source.

Three resource templates expose bounded reads:

```text
slicer://jobs/{job_id}/manifest
slicer://jobs/{job_id}/props
slicer://jobs/{job_id}/plates/{plate}/lines/{start}/{count}
```

Lines are 1-based and `count` is limited to 1..1000. Full files stay at the
returned artifact paths. `recover_3mf` verifies a retained source association
through a supplied G-code hash; it cannot reconstruct geometry from arbitrary
G-code.

`force=true` bypasses verified cache. A cache hit verifies source, settings,
manifest and output hashes plus engine/options identity. Native timing on a
cache hit belongs to the earlier slice; request timing describes the current call.

`allow_newer_file=true` passes the official compatibility option and records it
in request/cache identity. It does not make unsupported future features valid.
Use `false`, or CLI `--strict-file-version`, for strict checking.

`engine_status(check_updates=false)` reads local identity. Startup normally
checks upstream once; `serve --no-update-check` disables that query. An update
notice follows Bambu upstream `master` and never downloads or replaces a build.
The default native timeout is 1800 seconds; global `--timeout` changes it.

## Direct CLI

Global options precede the subcommand:

```sh
uv run bambu-studio-mcp discover

uv run bambu-studio-mcp --engine /absolute/runtime/engine.json \
  --artifacts /absolute/artifacts create /absolute/part.stl \
  --printer "Bambu Lab X1 Carbon 0.4 nozzle" \
  --process "0.20mm Standard @BBL X1C" --filament "Generic PLA"

uv run bambu-studio-mcp --engine /absolute/runtime/engine.json \
  --artifacts /absolute/artifacts slice /absolute/project.3mf --plate 1 --force

uv run bambu-studio-mcp --engine /absolute/runtime/engine.json status
```

Successful CLI output is JSON. Native diagnostics do not become MCP stdout.
See [TEST.md](../TEST.md) for native/platform and physical-print limits.
