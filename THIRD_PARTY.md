# Third-party attribution

Bambu Studio MCP is distributed under the [GNU AGPLv3](LICENSE.txt). Copyright
and attribution of reused material remain with their respective authors. Native
Bambu Studio binaries and the separately distributed networking plugin are not
included in the Python wheel or source distribution.

## Snapmaker wrapper lineage

This repository adapts [MoraesGil/snapmaker-orca-mcp](https://github.com/MoraesGil/snapmaker-orca-mcp)
at commit `f100c44d11576558d1290f4c48330477bde238a8`. It retains the wrapper's
project/revision preservation, transport/storage architecture, tests and
compatible AGPL notices. The package/import namespace and native adapter target
are changed to Bambu Studio. No shared core library has been extracted.

The Snapmaker wrapper itself was extracted from `tools/slicer_mcp` in
[MoraesGil/OrcaSlicer](https://github.com/MoraesGil/OrcaSlicer) at
`ee087a5bd7ebbb5daf445211cea1a3a9e3c931ad`. See [EXTRACTION.json](EXTRACTION.json).
Historical Snapmaker native acceptance, binary hashes and deployment reports do
not establish Bambu support.

The inherited adaptive mathematical port came from Snapmaker source
`9bf2f18eaff7cc0655cbdeef6a215660085ecb0f`. Its public adaptive operation is not
validated for Bambu and fails explicitly; retaining mathematical regression
checks does not claim Bambu or GUI equivalence.

## Max Ellis: three adapted Python helpers

Portions derive from **Max Ellis's OrcaSlicer MCP**,
[MaxEllis/orcaslicer-mcp](https://github.com/MaxEllis/orcaslicer-mcp), package
0.1.17 at `ccfacb43c2fd5a23973bfdabce89c74b2f6933fe`, licensed
**AGPL-3.0-only**. Copyright remains with Max Ellis and the respective contributors.

| Original file | Adaptation retained here |
| --- | --- |
| `src/orcaslicer_mcp/schema_extract.py` | Source-metadata parser in `src/bambu_studio_mcp/schema_extract.py` |
| `src/orcaslicer_mcp/settings_schema.py` | Setting lookup/search through the preset service |
| `src/orcaslicer_mcp/guard.py` | Protected-setting classification and credential redaction |

Original-file SHA-256 at the pinned commit:

```text
schema_extract.py  bc4da448dc1aa238b856c15f8ae29577a1cfc329d40af196c03bf20dcff4d9ff
settings_schema.py 73c00b57b64d5d022e73a0ba3e162a5ea1bdf3902e425aa212c348686e6ed697
guard.py           72e99143a46ac717e06d9d19454d12fd76dd44771340934efed315cf56d5f6e9
LICENSE            0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0
```

Local adaptations use versioned file edits, explicit custom-G-code opt-in,
native arrays, artifact-local drafts and hash-recorded inheritance. The MaxEllis
tool design also informed feature names. This project does not incorporate its
fork binary, GUI/RemoteAPI controller or printer client, and does not claim its
complete diagnose/compare feature set.

## Bambu Studio: native schema and public fixtures

The separately supplied native engine is official
[bambulab/BambuStudio](https://github.com/bambulab/BambuStudio), tag
`v02.08.02.61`, commit `926a7192574bcb9b3a732e1ec59a46d79cb45466`.
That source is AGPLv3. Native runtime acquisition/building is separate from this
wrapper. The separately distributed networking plugin is not redistributed by
this package.

`src/bambu_studio_mcp/data/settings.json` is generated from the exact Bambu source,
not from the Snapmaker or MaxEllis generated catalog. The metadata identifies the
source commit, verified input hashes and extractor origin. C++ default expressions
remain metadata; they are never evaluated as code.

Four unchanged public Bambu preset fixtures under `tests/fixtures/bambu-profiles`
retain upstream provenance and license:

| Source fixture under `resources/profiles/BBL` | SHA-256 |
| --- | --- |
| `filament/Generic PLA @BBL A1.json` | `23c1aa2ebb152191ebc45b73ff997e0d91d8a2d4408f133e2dbb65560e014a6d` |
| `filament/Generic PLA @base.json` | `530b745e2062c4c577d7acce3601ac7586fc65de394726252354bec0ade3e4c5` |
| `filament/fdm_filament_pla.json` | `670d21ea6bdf1cd4b0a2bfddc743612ffd896cd6e5dbe767c25d2c4165c49a30` |
| `filament/fdm_filament_common.json` | `8c7105b7d59a28f600c43a946f067b8b4d707b9711c0bf6b88e9166e48878548` |

The [fixture manifest](tests/fixtures/bambu-profiles/PROVENANCE.json) records
repository, commit, paths and hashes. Fixtures test catalog behavior; they do
not install or alter user profiles. Native property tests select their own
explicit X1 Carbon presets from the bound app.

### Regenerate from an explicit official checkout

```sh
uv run python -m bambu_studio_mcp.schema_extract \
  --src /explicit/BambuStudio/src/libslic3r/PrintConfig.cpp \
  --out src/bambu_studio_mcp/data/settings.json \
  --source-commit 926a7192574bcb9b3a732e1ec59a46d79cb45466
```

The generator verifies Git blobs for `PrintConfig.cpp`, `PrintConfig.hpp`,
`Config.hpp`, `PresetBundle.cpp` and `version.inc` against the supplied commit.
It extracts native nullable entries, variant/extruder keys and source version;
CLI flags and G-code placeholders are excluded. The baseline contains 679
settings, including 122 nullable entries. Review the regenerated schema and
contract matrix deliberately rather than automatically accepting a new hash.

Source metadata and file inheritance do not establish compiled defaults,
complete effective per-region values, native toolpaths, GUI equivalence or
physical printing. See [TEST.md](TEST.md).

## Conceptual cover artwork

`docs/assets/hero.png` was generated specifically for this Bambu adaptation. It
replaces the inherited Snapmaker cover. It is a conceptual workflow illustration,
not Bambu product UI, a native preview or measured print output. It is not used
as Bambu validation evidence and does not imply official Bambu Lab endorsement.
