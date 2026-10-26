# Property and function regressions

Use an isolated worktree for each independent change and the agreed integration
branch for combined verification. There is no permanently required historical
`props_test_branch`. Follow the [contribution workflow](../contribution-workflow.md).

## Evidence layers

| Layer | What it establishes | Limit |
| --- | --- | --- |
| Source catalog | Extracted names, types, bounds, nullable entries and provenance from a selected source commit | Not complete native runtime or per-region semantics |
| Contract/persistence | Accepted/rejected values, saved 3MF JSON/XML, source preservation and atomic failures | A stored override can still be ignored in a native scope |
| Native property case | Relevant observed commands/extrusions for finite values and synthetic geometry | Does not cover every combination, printer or physical material |
| Tool/function contract | Discovery, parameter validation, revisions and source/output association | Process doubles do not validate Bambu algorithms |

The Bambu baseline is tag `v02.08.02.61`, source
`926a7192574bcb9b3a732e1ec59a46d79cb45466`: 679 catalog settings, including
122 nullable entries. `unparsed_keys` lists entries without extracted human
label/tooltip text; it does not add settings or prove those settings unsupported.
Keep counts and schema fingerprints tied to the generated metadata.

## Add a named case

Use `tests/property_cases/<native_key>.json` with literal independent answers:

```json
{
  "key": "wall_loops",
  "batch": 1,
  "valid": [{"value": 4, "normalized": "4", "xml": "4"}],
  "invalid": [-1, 4.5, 1001]
}
```

Native vectors need explicit arrays. Do not derive expected serialization from
the implementation under test. Check both global and supported object/part
persistence where applicable, with unchanged source bytes and unrelated payloads.
Intentional policy rejections remain accounted for in the catalog matrix.

The native harness targets `outer_wall_speed`, `inner_wall_speed`, `layer_height`,
`wall_loops`, `sparse_infill_density`, `enable_support`, `support_type`,
`nozzle_temperature`, `cool_plate_temp`, `additional_cooling_fan_speed` and a
regional modifier. Having a case file or test function does not mean it ran.
Do not count a skip, a configuration comment or a changed hash as semantic proof.

## Reproduce current native evidence

```sh
uv sync --frozen --extra test
mkdir -p .temp
uv run --frozen --extra test pytest tests -q --junitxml=.temp/contracts.xml

env -u PYTEST_ADDOPTS uv run --frozen --extra test python \
  scripts/run_native_regressions.py --all-tests \
  --app /Applications/BambuStudio.app \
  --source-commit 926a7192574bcb9b3a732e1ec59a46d79cb45466 \
  --report .temp/native-regressions.json

uv run --frozen python scripts/property_coverage.py \
  --junit .temp/native-regressions.xml \
  --native-report .temp/native-regressions.json > .temp/property-coverage.json
```

Regular tests skip real engine cases unless explicitly enabled. The required
runner enables native fixtures and rejects a missing engine, stale identity/run
IDs, incomplete cases or skips. `--all-tests` includes the full suite and refuses
inherited `PYTEST_ADDOPTS` selection. Without it, the runner targets only
`tests/native`.

Keep the paired JSON/JUnit reports locally. Record wrapper SHA/dirty state,
engine version/source declaration, executable/resource hashes, fixture scope,
commands, results and gaps. `property_coverage.py` attributes properties only
from an accepted explicit native report for the matching schema source; test
names alone do not establish semantic coverage. A later code/engine/fixture
change invalidates the relevant evidence.

The observer accepts absolute XYZ linear extrusion and supported modal/command
semantics. Relative XYZ and extruding arcs fail explicitly. Fresh native path
ordering can vary, so compare meaningful features/commands. Auxiliary fan
coverage is OFF-only and requires explicit zero-valued `M106 P2` commands.
No printer operation is included.

## Modifier coordinates and scope

`add_modifier` creates an axis-aligned box using object-local
`center=[x,y,z]` and positive `dimensions=[width,depth,height]` in millimeters.
Coordinates precede instance rotation/scale/placement. Every instance of that
root shares the native `modifier_part` volume.

Initial allowlisted speed keys are `outer_wall_speed`, `inner_wall_speed`,
`sparse_infill_speed`, `top_surface_speed` and `internal_solid_infill_speed`.
Preserve native array shape; one value is still an array:

```json
{
  "name": "add_modifier",
  "arguments": {
    "project_id": "<project-id>",
    "object_id": 1,
    "center": [6, 12, 2.5],
    "dimensions": [12, 30, 10],
    "changes": {"outer_wall_speed": [20]},
    "expected_sha256": "<latest-revision-sha256>"
  }
}
```

Use the same fields inside `edit_project` with `"operation":"add_modifier"`.
Invalid geometry, unsupported keys/units or stale revisions must leave state
unchanged. Native acceptance must observe the intended regional speed inside
the box and baseline behavior outside it, then verify original-source hashes.
Variant-array length is not the number of physical filaments.

## Engine upgrades

1. Select a new official package explicitly, preserving the previous identity.
   Do not re-label another binary with an old source commit.
2. Regenerate from that exact source and review changed keys/types/defaults,
   variant metadata and missing documentation. Update fingerprints deliberately.
3. Run complete engine-independent checks and add focused cases for changes.
4. Run current native integration and investigate semantic failures. Stored
   settings do not override an unsuccessful G-code observation.
5. Record tested identity/scope and gaps before integration. GUI equivalence,
   creation/discovery acceptance and physical printing require separate evidence.

The inherited adaptive mathematical tests are not Bambu-native evidence. The
public adaptive generator stays unavailable until separately validated against
the selected Bambu source. See [TEST.md](../TEST.md).
