# Bambu implementation scope

The public project retains the Snapmaker wrapper's organization and external
contracts. Its executable subsystems were reimplemented for the installed Bambu
Studio CLI and Bambu 3MF/preset formats. It does not import or invoke Snapmaker.

| Area | Implementation |
| --- | --- |
| 3MF documents | Opaque ZIP preservation, graph edits, affine operations, native option codec and layer records |
| Native engine | Bambu discovery/binding, measured identity, subprocess lifecycle, preset exports, creation and pose recovery |
| Adaptive profiles | Pinned Bambu Slicing/SlicingAdaptive algorithm with explicit wrapper range overlay |
| Services/storage | Atomic revisions and drafts, immutable sources, verified cache/artifacts, shared native admission |
| Settings/schema | Bambu material/extruder variant routing, source metadata extraction and runtime validation |
| Transports | Signature-only MCP declarations, capability dispatch, compatible envelopes, authenticated REST and verified download snapshots |

Retained code consists of API protocols, DTO/type declarations, compatibility
import aliases and minimal composition/forwarding boilerplate. Acceptance tests
and their public wire fixture remain shared specifications. This is a runtime
reimplementation, not a claim that no original line remains or a legal
clean-room process. Historical and source-derived attribution remains in
[THIRD_PARTY.md](../THIRD_PARTY.md).

## Bambu-specific differences

The official source is Bambu Studio `926a7192574bcb9b3a732e1ec59a46d79cb45466`
(`v02.08.02.61`). Profiles use native material/extruder maps and preserve
`filament_id`/`filament_ids`. Polygon groups use native `#` separation. Foreign
3MF projects are rejected before slicing rather than relabeled.

Bambu's `bbs_3mf.cpp` importer/exporter uses ordinal layer-profile records. The
old wrapper's version-2 root-ID output was not a native Bambu format. The new
reader accepts both and the writer emits native ordinals, recalculated after
object edits. Public editing calls continue to identify objects by stable ID.

Adaptive generation derives from Bambu `Slicing.cpp` and `SlicingAdaptive.cpp`.
Explicit range overlay is wrapper behavior; it is not represented as Bambu GUI
equivalence. Actual G-code layer spacing is the native regression criterion.

## Acceptance boundary

The required native runner has 15 named cases, including one stdio workflow
calling all 35 MCP tools on a synthetic X1 Carbon/PLA project. Contract tests
cover schema/transport compatibility and saved persistence separately. A tool
invocation in one workflow does not validate all parameter combinations.

Native acceptance covers macOS Bambu Studio `02.08.02.61`. Linux/Windows native
execution, GUI equivalence and physical printing remain unvalidated. Every code
or engine change requires current evidence from the final wrapper revision;
historical pass counts are not transferred. See [TEST.md](../TEST.md).
