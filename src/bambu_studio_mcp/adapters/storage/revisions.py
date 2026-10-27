"""CAS publication of private, immutable 3MF revisions."""
from __future__ import annotations

import copy
import re
import shutil
import threading
import uuid
from contextlib import AbstractContextManager
from pathlib import Path

from ...application.contracts.presets import EngineIdentity
from ...application.contracts.projects import ProjectDocument
from ...domain.settings.values import integer, text
from ...domain.types.json import JsonObject, JsonValue, decode_object, is_json_value, json_object
from ..bambu.engine import file_hash
from ..three_mf.document import Project
from .artifacts import validate_project, write_json


class StoredProjectDocument:
    """Adapt document operations to the JSON application port."""
    def __init__(self, path: Path):
        self._origin = path.resolve()
        self.project = Project(path)

    @property
    def config(self) -> JsonObject:
        return json_object(self.project.config, 'Project configuration')

    def describe(self) -> JsonObject:
        description: object = self.project.describe()
        return json_object(description, 'Project description')

    def get_config(self, keys: list[str] | None = None) -> JsonObject:
        configuration: object = self.project.get_config(keys)
        return json_object(configuration, 'Project configuration')

    def list_objects(self) -> list[JsonValue]:
        objects: list[JsonValue] = []
        for item in self.project.list_objects():
            objects.append(json_object(item, 'Project object'))
        return objects

    def list_plates(self) -> list[JsonValue]:
        plates: list[JsonValue] = []
        for item in self.project.list_plates():
            plates.append(json_object(item, 'Project plate'))
        return plates

    def execute(self, name: str, arguments: JsonObject) -> JsonValue:
        handler: object = getattr(self.project, name, None)
        if not callable(handler):
            raise ValueError(f'Unsupported project operation: {name}')
        returned: object = handler(**arguments)
        if is_json_value(returned):
            return returned
        raise ValueError('Project operation result must be JSON')

    def save(self, path: Path) -> None:
        self.project.save(path=path)


class FilesystemRevisionRepository:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def transaction(self) -> AbstractContextManager[object]:
        return self._lock

    def _directory(self, project_id: str) -> Path:
        if not isinstance(project_id, str) or re.fullmatch('[0-9a-f]{32}', project_id) is None:
            raise ValueError('Invalid project_id')
        path = self.root / project_id
        if path.resolve() != path:
            raise ValueError('Working project directory cannot be a symlink')
        return path

    def state(self, project_id: str) -> JsonObject:
        with self._lock:
            directory = self._directory(project_id)
            state_file = directory / 'state.json'
            if state_file.is_symlink():
                raise ValueError('Working project changed outside the editor')
            loaded = decode_object(state_file.read_bytes(), 'Project state')
            current = Path(text(loaded['path']))
            if (loaded.get('project_id') != project_id or current.parent != directory or current.is_symlink()
                    or current.resolve() != current or file_hash(current) != loaded.get('sha256')):
                raise ValueError('Working project changed outside the editor')
            return loaded

    def _fresh(self, expected: JsonObject) -> JsonObject:
        actual = self.state(text(expected['project_id']))
        for key in ('sha256', 'path', 'revision', 'selected_plate'):
            if actual.get(key) != expected.get(key):
                raise ValueError('Stale project revision; reload the current sha256')
        return actual

    def load(self, path: str, identity: JsonObject) -> JsonObject:
        with self._lock:
            source = Path(path).expanduser().resolve(strict=True)
            validate_project(source)
            digest = file_hash(source)
            project_id = uuid.uuid4().hex
            directory = self._directory(project_id)
            directory.mkdir()
            snapshot = directory / 'source.3mf'
            try:
                shutil.copyfile(source, snapshot)
                if digest != file_hash(snapshot):
                    raise ValueError('Source changed while loading; retry the saved file')
                StoredProjectDocument(snapshot)
                initial: JsonObject = {
                    'project_id': project_id, 'path': str(snapshot), 'sha256': digest,
                    'source': {'path': str(source), 'sha256': digest}, 'revision': 0,
                    'selected_plate': 1, 'engine': copy.deepcopy(identity)}
                write_json(directory / 'state.json', initial)
            except BaseException:
                shutil.rmtree(directory)
                raise
            return initial

    def open(self, state: JsonObject) -> StoredProjectDocument:
        with self._lock:
            current = self._fresh(state)
            return StoredProjectDocument(Path(text(current['path'])))

    def select_plate(self, state: JsonObject, plate: int) -> JsonObject:
        with self._lock:
            current = self._fresh(state)
            updated = {**current, 'selected_plate': plate}
            write_json(self._directory(text(current['project_id'])) / 'state.json', updated)
            return updated

    def commit(self, state: JsonObject, document: ProjectDocument, operations: list[JsonObject],
               results: list[JsonValue], engine: EngineIdentity) -> JsonObject:
        with self._lock:
            current = self._fresh(state)
            if not isinstance(document, StoredProjectDocument) or document._origin != Path(text(current['path'])):
                raise ValueError('Revision document belongs to another repository')
            directory = self._directory(text(current['project_id']))
            target = directory / f'{uuid.uuid4().hex}.3mf'
            try:
                document.save(target)
                validate_project(target)
                published = copy.deepcopy(current)
                published.update({
                    'path': str(target), 'sha256': file_hash(target),
                    'revision': integer(current['revision']) + 1,
                    'selected_plate': min(integer(current['selected_plate']), len(document.list_plates())),
                    'changes': [copy.deepcopy(operation) for operation in operations], 'results': copy.deepcopy(results)})
                engine.verify()
                self._fresh(current)
                write_json(directory / 'state.json', published)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            return {**published, 'requires_slice': True}

    def workspace(self, state: JsonObject) -> Path:
        return self._directory(text(state['project_id']))
