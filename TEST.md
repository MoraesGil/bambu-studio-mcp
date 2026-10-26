# Validation workflow and evidence limits

This document defines repeatable checks for the Bambu adaptation. Historical
Snapmaker acceptance results are not Bambu results and are not carried forward
as passing counts, binary hashes or deployment claims.

## Baseline and current scope

| Item | Scope |
| --- | --- |
| Wrapper | Separate `bambu-studio-mcp` 0.1.0 adaptation of the public Snapmaker wrapper |
| Native target | Bambu Studio `02.08.02.61`, tag `v02.08.02.61`, source `926a7192574bcb9b3a732e1ec59a46d79cb45466` |
| Source catalog | 679 extracted settings, including 122 nullable entries; catalog/persistence tests are distinct from native semantics |
| Basic macOS feasibility | A synthetic saved 3MF produced nonempty plain `plate_1.gcode`; a native CLI mesh-to-3MF operation produced a saved project without opening a window |
| Native acceptance | Must be established for the actual final wrapper revision using current JSON/JUnit evidence; feasibility alone is insufficient |

The basic observations do not certify every MCP tool, the full native property
matrix, GUI equivalence, physical output, or a deployed service. Native Linux
and Windows execution is not validated. CI's engine-independent Linux/macOS
jobs do not change that boundary. Windows-specific process/path doubles are
contracts, not evidence of an actual Windows engine run.

## Engine-independent release checks

Use a named worktree and its own Python environment:

```sh
uv sync --frozen --extra test
uv run --frozen --extra test mypy
uv run --frozen --extra test pytest tests -q
uv run --isolated scripts/check_sdk1_interop.py \
  --server-python .venv/bin/python --scratch .temp/sdk1-interop
bash -n bin/bambu-slice.sh
uv build
uv run --frozen python scripts/check_distribution.py
git diff --check
```

These checks cover transport/authentication, public tool/resource/OpenAPI
contracts, typing boundaries, revisions and atomic failures, source/ZIP/painting
preservation, preset drafts, cache verification and native subprocess lifecycle
using controlled fixtures. They can run without Bambu Studio or a printer.
A test that uses a native-process double does not validate Bambu slicing.

The wire baseline contains 35 MCP tools, three resource templates and eight REST
OpenAPI paths. Review intentional changes to the checked-in contract fixture.
SDK-v1 interoperability runs in an isolated consumer environment; it does not
change the service's SDK dependency or establish native engine semantics.

The current required native suite contains **13 cases**: ten named setting
effects, a regional modifier, a create/edit/slice workflow and a layout workflow.
These cases do not cover all 35 tools or all 679 catalog settings. In particular,
there is no claim of complete GUI parity or full native feature acceptance.
The implementation still reuses Snapmaker wrapper code; passing contract tests
does not turn that reuse into an independently validated Bambu implementation.

## Required native macOS integration

Select the exact installed official app and verify its source identity first.
Do not edit an old manifest to conceal changed binaries or resources. Clear
inherited test selection before a complete run:

```sh
mkdir -p .temp
env -u PYTEST_ADDOPTS uv run --frozen --extra test python \
  scripts/run_native_regressions.py --all-tests \
  --app /Applications/BambuStudio.app \
  --source-commit 926a7192574bcb9b3a732e1ec59a46d79cb45466 \
  --report .temp/native-regressions.json

uv run --frozen python scripts/property_coverage.py \
  --junit .temp/native-regressions.xml \
  --native-report .temp/native-regressions.json > .temp/property-coverage.json
```

The required runner rejects absent engines, stale reports, omitted native
fixtures and skipped/incomplete required cases. Keep the paired JSON/JUnit
reports locally. Record wrapper commit and dirty state, run IDs, native
version/source declaration, measured executable/resource hashes, test counts,
per-case scope and all gaps. Reports become stale after relevant code, schema,
fixture, setting or engine changes.

The fixtures use a synthetic X1 Carbon project with explicit official presets.
Named cases inspect wall speeds, layer spacing, wall loops, infill, support,
nozzle/bed temperature and auxiliary fan commands. The modifier case must observe
regional speed inside the box and baseline speed outside it. Fan evidence is
OFF-only: explicit `M106 P2` commands must stay at zero. It does not validate a
nonzero fan setting or another printer's fan mapping.

The G-code observer is deliberately bounded: absolute XYZ linear extrusions,
modal feed and supported temperature/fan commands. Relative XYZ or extruding
arcs fail the observer instead of producing unsupported semantic claims.
Configuration comments, changed file hashes and native exit status alone are
insufficient evidence for property behavior.

## Creation, discovery and preservation acceptance

1. Discover a known installed bundle without writing an engine manifest. Bind
   only the explicitly selected binary/resources and verify recorded identity.
2. Create a project from public synthetic meshes and named X1 Carbon/PLA presets.
   Verify a saved, loadable 3MF, private mesh/preset copies, source/preset hashes,
   and useful failure behavior for invalid inputs or unavailable presets.
3. Load and edit a private revision, then inspect its returned saved-3MF path.
   Verify unchanged source geometry, painting, IDs and unrelated ZIP payloads;
   reject stale edits atomically.
4. Slice that exact final revision. Inspect nonempty per-plate G-code, native
   diagnostics, source association, cache rejection cases and requested settings.
5. Inspect the final output in the official GUI only when that separate
   validation is required. Record it separately from headless and contract tests.

Creation and discovery have their own acceptance paths. The property runner
must not be described as complete creation/UI coverage merely because it passed.
No test in this workflow sends a print job.

## Adaptive layers and benchmarks

The adaptive mathematical port is inherited from the Snapmaker source. Its pure
Python regression tests preserve the original numerical contract; they do not
prove equivalence to Bambu `926a719`. The public adaptive generator remains
explicitly unavailable until separately validated. Reset and height-range
persistence are independent behaviors.

`scripts/benchmark_wrapper.py` can compare deterministic synthetic fixtures with
an explicitly bound Bambu app. It reports synchronous wrapper latency and Python
allocations separately from native slice time. Reports require matching engine,
fixture hashes and methodology. No historical Snapmaker benchmark is a Bambu
baseline, and Python allocations do not measure native process memory or
transport latency. Keep raw reports and generated models/G-code outside Git.

## Publication and physical limits

Do not distribute private models, installed/user presets, access tokens, native
runtime bundles, G-code or raw local acceptance logs. Review any published
summary for personal paths and credentials. Keep repeatable synthetic fixtures
and attribution in the repository.

A digital slice does not establish adhesion, dimensional fit, color, material
behavior or a successful physical print. GUI/CLI equivalence and production
service readiness need their own evidence. See [contribution workflow](contribution-workflow.md)
for required checks and review.

Native fixtures carry the Bambu application marker. Property fixtures disable arc fitting so their bounded linear-extrusion oracle cannot mistake arcs for measured linear paths. Required creation coverage checks native filament IDs, immutable revisions and a fresh slice. Required layout coverage runs native arrange and auto-orient, verifies geometry correspondence and re-slices the saved revision.
