"""Compatibility editor facade composed with concrete 3MF adapters."""
from __future__ import annotations
from collections.abc import Iterable
from pathlib import Path
from typing import Protocol
from .application.services.projects import ProjectService
from .application.contracts.projects import ProjectSlicer
from .adapters.bambu.editing import NativeHost
from .bootstrap import project_parts
from .presets import Catalog
from . import presets
from .engine import Engine
from .domain.types.json import JsonObject
from .adapters.bambu.creation import BambuCreation

class EditorHost(ProjectSlicer, NativeHost, Protocol):
    @property
    def root(self) -> Path: ...
    @property
    def engine(self) -> Engine: ...

class Editor(ProjectService):
    catalog: Catalog

    def __init__(self, slicer: EditorHost, preset_dirs: Iterable[str | Path] = ()):
        parts = project_parts(slicer, slicer.root, preset_dirs)
        self.root = parts.revisions.root
        super().__init__(slicer, parts.revisions, parts.catalog, parts.native, presets._schema_doc())
        self._creation = BambuCreation(slicer, parts.catalog, slicer.root)

    def create_project(self, mesh_paths: list[str], printer_preset: str, process_preset: str,
                       filament_presets: list[str]) -> JsonObject:
        created = self._creation.create(mesh_paths, printer_preset, process_preset, filament_presets)
        path = created['path']
        if not isinstance(path, str):
            raise ValueError('Native creation returned an invalid path')
        return {**created, **self.load_model(path)}
