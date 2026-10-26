"""Composition root for shared application services and concrete adapters."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .adapters.bambu.editing import NativeHost, BambuNativeEditing
from .adapters.bambu.engine import Engine
from .adapters.bambu.runner import BambuProcess
from .adapters.storage.artifacts import FilesystemArtifacts
from .adapters.storage.revisions import FilesystemRevisionRepository
from .application.services.admission import NativeAdmission
from .presets import Catalog

if TYPE_CHECKING:
    from .editor import Editor
    from .service import Slicer


@dataclass(frozen=True)
class SlicingParts:
    admission: NativeAdmission
    artifacts: FilesystemArtifacts
    runner: BambuProcess


def slicing_parts(engine: Engine, root: Path) -> SlicingParts:
    return SlicingParts(NativeAdmission(), FilesystemArtifacts(root), BambuProcess(engine.executable))


@dataclass(frozen=True)
class ProjectParts:
    revisions: FilesystemRevisionRepository
    catalog: Catalog
    native: BambuNativeEditing


def project_parts(host: NativeHost, root: Path, preset_dirs: Iterable[str | Path]) -> ProjectParts:
    revisions = FilesystemRevisionRepository(root / 'projects')
    catalog = Catalog(host.engine, [Path(path) for path in preset_dirs], root / 'preset-drafts')
    return ProjectParts(revisions, catalog, BambuNativeEditing(host, revisions))


def build_slicer(engine: Engine, root: Path, timeout: float = 1800) -> Slicer:
    from .service import Slicer
    return Slicer(engine, root, timeout)


def build_editor(slicer: Slicer, preset_dirs: Iterable[str | Path] = ()) -> Editor:
    from .editor import Editor
    return Editor(slicer, preset_dirs)
