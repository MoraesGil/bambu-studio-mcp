# Bambu Studio MCP contract

The wrapper uses a separately installed official Bambu Studio native engine.
The baseline is tag `v02.08.02.61`, source commit
`926a7192574bcb9b3a732e1ec59a46d79cb45466`. Native update checks follow upstream
`bambulab/BambuStudio` `master`. They produce notices, not installations.

## Inputs and results

- Slice a complete saved 3MF. Plate 0 means all applicable plates; positive
  numbers select a plate. The standalone Bash route selects one positive plate.
- Create a saved project from explicit STL/OBJ mesh paths and named printer, process and
  filament presets. Copy inputs into private workspaces, export resolved preset
  drafts there, and invoke native `--load-settings`, `--load-filaments`,
  `--arrange 1` and `--export-3mf`. Native project creation is separate from
  surgical edits to existing 3MFs.
- Keep generated G-code, a captured source 3MF, native exported settings,
  per-plate configuration blocks, diagnostics and hash-recorded result manifests.
  A slice result identifies the engine, plate selection and cache state.
- Do not connect to printers, execute external post-process scripts, start GUI
  automation or install a native engine.

Native libraries/resources must remain in their expected package layout.
Binding records an operator-declared source commit and measured executable and
resource hashes. Asset changes invalidate reuse and require explicit rebinding.
A commit declaration alone does not prove how a binary was built.

## Preserved sources and editing revisions

`load_model` captures a saved 3MF in a private workspace. `create_project` returns
a newly generated private project. Each successful edit advances an immutable
revision with a SHA-256. An expected SHA rejects stale edits; a failed batch
must not advance project state.

Preserve unrelated ZIP payloads, original meshes and painting, root/part IDs,
component references and instance transforms. New geometry and duplicates get
unambiguous identities. Edits invalidate embedded G-code, previews and stale
slice metadata. Installed/canonical presets and original inputs remain read-only.

Supported operations include global/object/part settings, box modifiers,
object transforms and duplication/deletion, plate operations, native arrangement
and orientation, and explicit layer-height ranges. Plate deletion parks objects
off-bed while preserving geometry; the final plate cannot be deleted.

Object IDs are stable root IDs. Instance/material slot indexes are 0-based.
Plates are 1-based; `extruder=0` inherits and `1..N` selects a logical material
slot. A logical slot does not identify a physical toolhead or AMS location.
Translations are XYZ deltas in millimeters, rotations are degree deltas, and
scales are per-axis factors. Rotation/scaling preserve the instance pivot.

Native arrangement/orientation use STL pose probes. Validate every exported
vertex before applying world-transform deltas to the preserved 3MF graph.
All-plate arrangement must also persist plate count/membership. Locked instances
remain protected. A selected-plate overflow must fail atomically.

Adaptive generation has not been validated for the Bambu baseline. The inherited
mathematical port is not accepted as Bambu support; the public adaptive request
fails explicitly. Reset clears an existing profile and Z bands. Clearing only
height ranges preserves an existing profile.

## Settings and presets

The catalog is generated from the exact Bambu source, with verified input blobs
and source metadata. The baseline has 679 settings, including 122 nullable entries.
C++ default expressions are metadata, not executed code or measured runtime
values. CLI flags and G-code placeholders are outside this catalog.

Reject unsupported keys/types, invalid values, ambiguous array cardinalities,
credential changes and executable post-processing. Custom G-code edits require
explicit opt-in. Preserve existing native array shapes. Do not apply Snapmaker
flow-variant segmentation to a Bambu configuration.

Preset lookup resolves declared inheritance, records hashes and rejects missing,
cyclic or ambiguous parents. Only artifact-local drafts can be edited, renamed
or deleted. Project selection is explicit and must preserve unrelated materials.
A missing value can use a source-derived literal only where the adapter can
validate its type/cardinality and matching schema/engine identity. Unsupported
expressions or layouts fail clearly.

## Slicing, cache and artifacts

`slice_3mf` slices a saved source; `slice_project` slices a captured working
revision. `apply_and_slice` commits settings before slicing, so a native failure
can leave the successful edit available for inspection. Regenerate G-code after
any relevant change.

Cache keys include exact source bytes, engine identity and plate/compatibility
options. Reuse verifies the stored manifest, source, settings and all G-code
hashes. `cached=true` means retained output, not a newly executed slice. Force
bypasses reuse. Corrupt, partial or failed results cannot become valid cache hits.

Native execution is serialized per service instance. A disconnected client
cannot release admission while the native worker remains active. Native errors
and timeouts retain diagnostics and cannot produce a success manifest.

The service records global native settings and per-plate CONFIG_BLOCK values.
Those exports do not exhaustively describe final per-region resolution. Native
defaults and compatibility substitutions can still occur; no fallback profile
is silently invented by the wrapper.

`allow_newer_file=true` passes the official version-gate override and records it
in request identity. It neither modifies the source nor guarantees future-file
support. The service defaults to allowing it; `--strict-file-version` disables
it. The standalone Bash launcher requires an explicit optional flag.

MCP exposes bounded G-code lines and retained artifact resources. Authenticated
REST exposes eight slicing/artifact OpenAPI paths on the same listener as MCP;
it does not add mesh upload, project-creation or printer endpoints.

## Recovery and validation limits

`recover_3mf` identifies a retained source through the supplied G-code hash and
verifies the association. Missing, changed or ambiguous associations fail.
Standalone G-code cannot reconstruct original meshes, painting or hierarchy.

Fresh native runs may differ in metadata or toolpath ordering. Equal settings
and engine identities do not establish byte identity, GUI equivalence or a
successful physical print. Validate sources, saved revisions, native commands,
observed toolpaths and physical behavior as separate evidence layers. See
[TEST.md](TEST.md) and [property regressions](docs/property-regressions.md).
