# Python service architecture

This is a separate Bambu adaptation of the public Snapmaker wrapper. No shared
`slicer-mcp-core` package has been extracted. The native Bambu engine performs
slicing and initial mesh-to-3MF project generation in its own process. The Bash
launcher remains independent of the Python service.

| Package | Responsibility |
| --- | --- |
| `domain/types`, `domain/entities`, `domain/settings` | Typed values and setting/variant validation, without filesystem/process/transport imports |
| `application/contracts` | Document, revision, preset, execution and artifact capabilities |
| `application/services` | Project/preset/slice use cases and shared native admission |
| `adapters/three_mf` | Preserved 3MF graph, geometry and XML serialization |
| `adapters/bambu` | Engine identity, native process and layout/editing boundary |
| `adapters/storage` | Immutable revisions, atomic publication, preset drafts and verified artifacts/cache |
| `adapters/mcp`, `adapters/http` | MCP tools/resources and authenticated ASGI/REST |
| `bootstrap.py` | Composition of concrete adapters and shared services |

Top-level modules remain compatibility facades. Application/domain code depends
on narrow protocols and values; adapters implement them. Strict architecture and
type tests enforce these boundaries, including imports under `TYPE_CHECKING`.
External JSON/native file contents still need runtime validation.

## Creation and revision editing

`create_project` uses explicit named presets and private copies of mesh inputs.
Resolved preset exports are temporary native CLI inputs. The native engine
arranges and exports a new 3MF, which becomes a private editable project.
This initial export is distinct from editing an existing painted 3MF.

Existing-project edits preserve the archive graph and unrelated payloads rather
than re-exporting the whole project through the slicer. A revision transaction
checks the expected SHA and publishes atomically. Source projects and installed
presets remain read-only. Each committed revision is already a saved 3MF at its
returned path; no separate MCP export endpoint is required.

Native arrangement/orientation measure exported STL poses and certify affine
deltas before applying them to the preserved graph. The adaptive mathematical
port is inherited; its public generator rejects Bambu bindings until validated.
Pure mathematical tests do not establish Bambu native compatibility.

## Native execution and storage

One admission coordinator belongs to each service instance. MCP and REST share
that instance, lock, engine, artifacts and cache. Async waiters do not occupy
worker threads. Caller cancellation cannot release native admission while the
worker still runs. Native operations share serialized process access.

Slicing validates the request/source, verifies bound engine assets and cache,
then captures an immutable source snapshot. The runner owns subprocess timeout
handling, job-local profile/temp directories and diagnostics. Storage verifies
produced plates and persists hashes. Cache reads verify manifests, source,
settings and every generated G-code; stored absolute paths cannot redirect reads
outside canonical job locations.

The Bambu adapter owns native repository/ref, bundle discovery/layout, CLI
behavior, profile roots and source schema. Bambu material variants use native
extruder/material maps; Snapmaker segment metadata is not a portable format.
These engine-specific boundaries remain local to this repository.

## Consumer contracts and evidence

The service exposes 35 MCP tools and three resource templates. REST retains
eight slicing/artifact OpenAPI paths on the same authenticated listener; creation
and revision editing use MCP/Python, with a CLI create command for mesh inputs.
Golden tests compare schemas/discovery/errors across stdio and HTTP. Framework
updates do not authorize an accidental public contract change.

Strict typing checks the first-party package and deliberate invalid consumer
examples. SDK-v1 interop uses an isolated client environment. These checks use
native-boundary doubles where appropriate; real engine acceptance is separate.

`scripts/benchmark_wrapper.py` compares deterministic synthetic small/medium/large
inputs with matching engine/fixture identity. It reports synchronous wrapper
latency and Python allocations separately from native execution. It excludes
transport latency and native process memory; there is no inherited Bambu
performance claim. See [validation](../TEST.md),
[property cases](property-regressions.md) and the
[contribution workflow](../contribution-workflow.md) for repeatable commands.
