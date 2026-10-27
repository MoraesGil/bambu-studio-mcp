"""Bambu CLI layout measurements and source-pinned variable layer generation.

Layout uses Bambu's CPU STL export, never a renderer or a replacement project.
Every measured vertex must fit one rigid transform of the preserved 3MF graph.
Adaptive generation follows BambuStudio 926a719 Slicing.cpp/SlicingAdaptive.cpp;
explicit height-range overlays preserve this wrapper's editing contract.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import struct
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict
from xml.etree import ElementTree as ET

from ...domain.entities.project import NativeLayout
from ...domain.types.json import JsonObject, JsonValue, json_object
from ...engine import Engine as Engine, file_hash as file_hash
from ..three_mf.geometry import (
    Geometry as _Geometry, matrix as _matrix, point as _point,
    compose as _compose, read_metadata as _metadata,
    Point as Point, Triangle as Triangle, InstanceKey as InstanceKey,
)
from ..three_mf.xml import XML_LOCK, native_xml
from .project_format import ensure_bambu_project
from .runner import run_command

__all__ = ['_Geometry', '_matrix', '_point', '_compose', '_metadata',
           'Engine', 'file_hash', 'native_deltas', 'adaptive_layer_profile']
_XML_LOCK = XML_LOCK
_CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
_PRODUCTION = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
BAMBU_ADAPTIVE_SOURCE = '926a7192574bcb9b3a732e1ec59a46d79cb45466'


class LayoutState(TypedDict):
    source_plate_count: int
    native_plate_count: int
    bed_dimensions_mm: list[float]


class PlateFrame(LayoutState):
    source_origin: list[float]
    native_origin: list[float]
    world_correction: list[float]


class RunRecord(TypedDict):
    argv: list[str]
    log_path: str
    status: str
    exit_code: NotRequired[int]
    elapsed_seconds: NotRequired[float]
    log_sha256: NotRequired[str]


class ArtifactRecord(TypedDict):
    path: str
    sha256: str


class VerifiedInstance(TypedDict):
    object_id: int
    instance_id: int
    delta: list[float]
    vertices_verified: int
    max_error_mm: float
    tolerance_mm: float


class LockedInstance(TypedDict):
    object_id: int
    instance_id: int
    plate: int
    vertices_verified: int
    relative_pose_error_mm: float


class AuditRecord(TypedDict):
    status: str
    source_path: str
    source_sha256: str
    engine: JsonObject
    arrange: bool
    orient: bool
    plate: int
    compatibility_flags: dict[str, bool]
    runs: list[RunRecord]
    artifacts: list[ArtifactRecord]
    instances: list[VerifiedInstance]
    native_input_sha256: NotRequired[str]
    plate_frame: NotRequired[PlateFrame]
    native_layout_state: NotRequired[LayoutState]
    layout: NotRequired[NativeLayout]
    locked_instances_verified: NotRequired[list[LockedInstance]]
    error: NotRequired[str]


def _native_xml(tree: ET.Element) -> bytes:
    serialized = native_xml(tree, _CORE, _PRODUCTION)
    return serialized


def _read_stl(path: Path) -> list[Point]:
    if path.is_symlink():
        raise ValueError('Native STL output cannot be a symlink')
    payload = path.read_bytes()
    if len(payload) < 84:
        raise ValueError('Native output is not a binary STL')
    facets, = struct.unpack_from('<I', payload, 80)
    if facets == 0 or len(payload) != facets * 50 + 84:
        raise ValueError('Native STL facet count is invalid')
    points: list[Point] = []
    for index in range(facets):
        coordinates = struct.unpack_from('<9f', payload, 84 + index * 50 + 12)
        points.extend(tuple(coordinates[start:start + 3]) for start in (0, 3, 6))
    if any(not math.isfinite(coordinate) for vertex in points for coordinate in vertex):
        raise ValueError('Native STL has non-finite coordinates')
    return points


def _subtract(a: Sequence[float], b: Sequence[float]) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return math.fsum(a[index] * b[index] for index in range(3))


def _cross(a: Sequence[float], b: Sequence[float]) -> Point:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _tolerance(*meshes: Sequence[Sequence[float]]) -> float:
    # Bambu export_models(IO::STL) writes float32 coordinates. Use four ulps
    # at the largest world coordinate, with a 0.2 micron absolute floor.
    largest = max((abs(value) for mesh in meshes for vertex in mesh for value in vertex), default=1.)
    return max(2e-4, math.ldexp(largest, -21))


def _verify_baseline(points: list[Point], triangles: list[Triangle], instance: list[float]) -> None:
    if len(points) != len(triangles) * 3:
        raise ValueError('Native baseline changed the source facet count')
    tolerance = _tolerance(points)
    for ordinal, source in enumerate(triangles):
        world = tuple(_point(vertex, instance) for vertex in source)
        measured = points[ordinal * 3:ordinal * 3 + 3]
        matched = any(max(math.dist(left, right) for left, right in zip(measured, candidate)) <= tolerance
                      for candidate in itertools.permutations(world))
        if not matched:
            raise ValueError('Native baseline changed the source pose or facet sequence; no delta can be certified')


def _unit(vector: Point) -> Point:
    length = math.sqrt(_dot(vector, vector))
    if length <= 0:
        raise ValueError('A non-coplanar source basis is required to certify a 3D delta')
    return tuple(value / length for value in vector)


def _recover(before: list[Point], after: list[Point]) -> tuple[list[float], float, float]:
    """Construct orthonormal source/target frames, then certify all vertices.

    A three-axis frame avoids solving an unconstrained affine fit. Scale, shear,
    reflection and reordered facets fail vertex certification; no least-squares
    approximation can hide a geometry change.
    """
    if len(before) != len(after) or len(before) < 4:
        raise ValueError('Native operations changed facet correspondence')
    origin = before[0]
    first = max(range(len(before)), key=lambda n: math.dist(origin, before[n]))
    axis = _subtract(before[first], origin)
    second = max(range(len(before)), key=lambda n: _dot(_cross(axis, _subtract(before[n], origin)),
                                                         _cross(axis, _subtract(before[n], origin))))
    perpendicular = _cross(axis, _subtract(before[second], origin))
    extent = math.dist(origin, before[first])
    if extent == 0 or max(abs(_dot(perpendicular, _subtract(vertex, origin))) for vertex in before) <= extent ** 3 * 1e-10:
        raise ValueError('A non-coplanar source basis is required to certify a 3D delta')

    def frame(vertices: list[Point]) -> tuple[Point, Point, Point]:
        x = _unit(_subtract(vertices[first], vertices[0]))
        z = _unit(_cross(x, _subtract(vertices[second], vertices[0])))
        return x, _cross(z, x), z

    left, right = frame(before), frame(after)
    rotation = [math.fsum(left[axis][i] * right[axis][j] for axis in range(3))
                for i in range(3) for j in range(3)]
    translation = [after[0][j] - math.fsum(origin[i] * rotation[3 * i + j] for i in range(3)) for j in range(3)]
    transform = rotation + translation
    tolerance = _tolerance(before, after)
    error = max(math.dist(_point(vertex, transform), observed) for vertex, observed in zip(before, after))
    if error > tolerance:
        raise ValueError('Native operation is non-rigid or changed vertex correspondence')
    return transform, error, tolerance


def _write_audit(work: Path, value: AuditRecord) -> None:
    content = json.dumps(value, indent=2, ensure_ascii=False)
    (work / 'native_ops.json').write_text(content + '\n', encoding='utf-8')


def _layout_state(geometry: _Geometry, log_path: Path) -> LayoutState:
    plates = geometry.settings.findall('plate')
    ids = [int(_metadata(node).get('plater_id', str(index))) for index, node in enumerate(plates, 1)]
    if ids != list(range(1, len(ids) + 1)):
        raise ValueError('Native plate frame requires sequential, ordered plate ids')
    initial: tuple[int, int, int] | None = None
    final: int | None = None
    arranging = False
    for line in log_path.read_text(errors='replace').splitlines():
        arranging = arranging or 'before arrange, need_arrange=' in line
        shape = re.search(r'm_plate_width (\d+), m_plate_depth (\d+), m_plate_height \d+, plate count (\d+)', line)
        count = re.search(r'rebuild_plates_after_arrangement:after rebuild, plates count (\d+)', line)
        if shape and not arranging:
            initial = int(shape[1]), int(shape[2]), int(shape[3])
        if count:
            final = int(count[1])
    if initial is None or final is None:
        raise ValueError('Native plate frame could not be observed from its layout diagnostics')
    width, depth, source_count = initial
    if min(width, depth) <= 0 or source_count != (len(plates) or 1) or not 1 <= final <= 36:
        raise ValueError('Native layout changed the plate grid unexpectedly')
    return dict(source_plate_count=source_count, native_plate_count=final, bed_dimensions_mm=[float(width), float(depth)])


def _plate_origin(index: int, count: int, dimensions: Sequence[float]) -> list[float]:
    # PartPlate.cpp::compute_shape_position uses a sqrt grid and logical gap .2.
    row, column = divmod(index, math.ceil(math.sqrt(count)))
    return [column * (dimensions[0] + dimensions[0] / 5), -row * (dimensions[1] + dimensions[1] / 5), 0.]


def _plate_frame(state: LayoutState, plate: int) -> PlateFrame:
    if plate != state['native_plate_count']:
        raise ValueError('Native layout spilled or changed the selected plate unexpectedly')
    dimensions = state['bed_dimensions_mm']
    before = _plate_origin(plate - 1, state['source_plate_count'], dimensions)
    after = _plate_origin(plate - 1, state['native_plate_count'], dimensions)
    correction = list(_subtract(before, after))
    return {**state, 'source_origin': before, 'native_origin': after, 'world_correction': correction}


def _fit_plate(points: list[Point], origin: Sequence[float], dimensions: Sequence[float]) -> bool:
    allowance = _tolerance(points)
    for axis in (0, 1):
        low, high = min(vertex[axis] for vertex in points), max(vertex[axis] for vertex in points)
        if low < origin[axis] - allowance or high > origin[axis] + dimensions[axis] + allowance:
            return False
    return True


def _run(engine: Engine, project: Path, output: Path, arrange: bool, orient: bool,
         timeout: float, audit: AuditRecord, work: Path) -> Path:
    output.mkdir(mode=0o700)
    data = output / 'native-data'
    data.mkdir(mode=0o700)
    destination = output / 'stls'
    # export-stls is a directory-valued action in BambuStudio.cpp:6388.
    # Do not invoke --slice/select_plate: CPU layout needs no GUI singleton.
    command = [str(engine.executable), '--allow-newer-file', '--debug', '5',
               '--arrange', '1' if arrange else '0', '--orient', '1' if orient else '0',
               '--ensure-on-bed=0', '--datadir', str(data), '--outputdir', str(output),
               '--export-stls', str(destination), str(project)]
    record: RunRecord = dict(argv=command, log_path=str(output / 'engine.log'), status='running')
    audit['runs'].append(record)
    _write_audit(work, audit)
    engine.verify()
    started = time.perf_counter()
    try:
        code, _ = run_command(command, output, timeout)
        record['exit_code'] = code
        record['status'] = 'failed' if code else 'complete'
        if code:
            raise RuntimeError(f'Native edit failed ({code}); diagnostics: {work}')
    except TimeoutError:
        record['status'] = 'timeout'
        raise
    except BaseException:
        record['status'] = 'failed'
        raise
    finally:
        record['elapsed_seconds'] = time.perf_counter() - started
        log = output / 'engine.log'
        if log.is_file():
            record['log_sha256'] = file_hash(log)
        engine.verify()
    return destination


def _exports(directory: Path, expected: set[int], audit: AuditRecord) -> dict[int, list[Point]]:
    measured: dict[int, list[Point]] = {}
    ordinals: set[int] = set()
    for path in sorted(directory.iterdir()):
        match = re.fullmatch(r'obj_(\d+)_mcp_object_(\d+)\.stl', path.name)
        if match is None or int(match[1]) in ordinals or int(match[2]) in measured:
            raise ValueError('Unexpected or ambiguous native STL output')
        ordinals.add(int(match[1]))
        measured[int(match[2])] = _read_stl(path)
        audit['artifacts'].append(dict(path=str(path), sha256=file_hash(path)))
    if set(measured) != expected or ordinals != set(range(1, len(expected) + 1)):
        raise ValueError('Native STL object count does not match the selected source roots')
    return measured


def _locked_check(key: InstanceKey, before: list[Point], after: list[Point], plate: int,
                  state: LayoutState, audit: AuditRecord) -> None:
    if plate > state['native_plate_count']:
        raise ValueError('Native layout removed an occupied locked plate')
    dimensions = state['bed_dimensions_mm']
    old = _plate_origin(plate - 1, state['source_plate_count'], dimensions)
    new = _plate_origin(plate - 1, state['native_plate_count'], dimensions)
    correction = _subtract(new, old)
    error = max(math.dist(tuple(vertex[i] + correction[i] for i in range(3)), target)
                for vertex, target in zip(before, after))
    if error > _tolerance(before, after) or not _fit_plate(after, new, dimensions):
        raise ValueError("Native operation changed a locked instance's relative pose or plate fit")
    audit.setdefault('locked_instances_verified', []).append(dict(
        object_id=key[0], instance_id=key[1], plate=plate,
        vertices_verified=len(before), relative_pose_error_mm=error))


def native_deltas(engine: Engine, project: Path, work: Path, arrange: bool = False, orient: bool = False,
                  plate: int = 0, timeout: float = 1800) -> dict[tuple[int, int], list[float]]:
    """Measure arrange/orient with preserved root IDs and original instance ordinals.

    Baseline and edited exports must have identical facet correspondence. The
    measured plate grid certifies global assignments and selected-plate reflow;
    locked anchors are measured but never returned as edits. Source and native
    input hashes must remain unchanged throughout the two native invocations.
    """
    if type(arrange) is not bool or type(orient) is not bool or not (arrange or orient):
        raise ValueError('Request arrange and/or orient as booleans')
    if type(plate) is not int or plate < 0:
        raise ValueError('plate must be zero (all) or a positive plate number')
    if type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be a finite positive number')
    project = project.resolve(strict=True)
    work = work.resolve()
    ensure_bambu_project(project)
    engine.verify()
    digest = file_hash(project)
    geometry = _Geometry(project)
    if digest != file_hash(project):
        raise ValueError('Source changed while reading its geometry; retry a stable saved project')
    locked, selected = geometry.locked_instances(), geometry.selected(plate, omit_locked=True)
    if arrange and orient and plate == 0 and locked:
        raise ValueError('Combined global arrange/orient with locked plates requires separate per-plate operations')
    included = {key for key, _ in geometry.items} if arrange and plate == 0 else selected
    # Read geometry before native_input sanitizes the private export graph.
    meshes = {oid: geometry.mesh(oid) for oid in geometry.instances if any(key[0] == oid for key in included)}
    work.mkdir(mode=0o700, parents=True, exist_ok=True)
    if any((work / name).exists() for name in ('native_input.3mf', 'native_ops.json', 'baseline', 'edited')):
        raise ValueError('Native operation scratch outputs already exist; choose a fresh work directory')
    audit: AuditRecord = dict(status='running', source_path=str(project), source_sha256=digest,
        engine=json_object(engine.identity()), arrange=arrange, orient=orient, plate=plate,
        compatibility_flags={'allow_newer_file': True}, runs=[], artifacts=[], instances=[])
    _write_audit(work, audit)
    try:
        native_input = work / 'native_input.3mf'
        grouped = geometry.native_input(native_input, included)
        input_digest = file_hash(native_input)
        audit['native_input_sha256'] = input_digest
        baseline = _exports(_run(engine, native_input, work / 'baseline', False, False, timeout, audit, work), set(grouped), audit)
        edited = _exports(_run(engine, native_input, work / 'edited', arrange, orient, timeout, audit, work), set(grouped), audit)
        state = _layout_state(geometry, work / 'edited/engine.log') if arrange else None
        frame = _plate_frame(state, plate) if state is not None and plate else None
        if frame is not None:
            audit['plate_frame'] = frame
        elif state is not None:
            audit['native_layout_state'] = state
            audit['layout'] = dict(plate_count=state['native_plate_count'], instances=[])
        if file_hash(project) != digest or file_hash(native_input) != input_digest:
            raise ValueError('Source or native scratch input changed during the operation')
        result: dict[InstanceKey, list[float]] = {}
        for oid, keys in grouped.items():
            stride = 3 * len(meshes[oid])
            if len(baseline[oid]) != len(keys) * stride or len(edited[oid]) != len(baseline[oid]):
                raise ValueError('Native operation changed the object or instance facet count')
            for ordinal, key in enumerate(keys):
                start = ordinal * stride
                before, after = baseline[oid][start:start + stride], edited[oid][start:start + stride]
                _verify_baseline(before, meshes[oid], geometry.instances[oid][key[1]])
                if key not in selected:
                    assert state is not None
                    _locked_check(key, before, after, locked[key], state, audit)
                    continue
                if frame is not None:
                    if not _fit_plate(after, frame['native_origin'], frame['bed_dimensions_mm']):
                        raise ValueError('Native arranged geometry does not fit the selected plate')
                    after = [tuple(vertex[i] + frame['world_correction'][i] for i in range(3)) for vertex in after]
                elif state is not None:
                    assignment = [index + 1 for index in range(state['native_plate_count'])
                                  if _fit_plate(after, _plate_origin(index, state['native_plate_count'], state['bed_dimensions_mm']), state['bed_dimensions_mm'])]
                    if len(assignment) != 1:
                        raise ValueError('Native arranged instance does not fit exactly one printable plate')
                    audit['layout']['instances'].append(dict(object_id=key[0], instance_id=key[1], plate=assignment[0]))
                delta, error, tolerance = _recover(before, after)
                result[key] = delta
                audit['instances'].append(dict(object_id=key[0], instance_id=key[1], delta=delta,
                                               vertices_verified=stride, max_error_mm=error, tolerance_mm=tolerance))
        engine.verify()
        audit['status'] = 'verified'
        return result
    except BaseException as error:
        audit['status'] = 'failed'
        audit['error'] = str(error)
        raise
    finally:
        _write_audit(work, audit)


def _f32(value: float) -> float:
    packed = struct.pack('<f', value)
    rounded, = struct.unpack('<f', packed)
    return float(rounded)


def _number(config: Mapping[str, JsonValue], key: str, default: JsonValue) -> float:
    value = config.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f'Invalid numeric setting: {key}')
    try:
        number = float(str(value).removesuffix('%'))
    except (TypeError, ValueError) as error:
        raise ValueError(f'Invalid numeric setting: {key}') from error
    if not math.isfinite(number):
        raise ValueError(f'Non-finite setting: {key}')
    return number


def _numbers(config: Mapping[str, JsonValue], key: str, default: JsonValue) -> list[float]:
    supplied = config.get(key, default)
    values = supplied if isinstance(supplied, list) else re.split('[;,]', supplied) if isinstance(supplied, str) else [supplied]
    if not values:
        raise ValueError(f'Empty per-nozzle setting: {key}')
    return [_number({key: item}, key, 0.) for item in values]


def _height_ranges(geometry: _Geometry, object_id: int, minimum: float, maximum: float) -> list[tuple[float, float, float]]:
    data = geometry.data.get('Metadata/layer_config_ranges.xml')
    if data is None:
        return []
    ordinal = list(geometry.instances).index(object_id) + 1
    selected = [obj for obj in ET.fromstring(data).findall('object') if int(obj.attrib['id']) == ordinal]
    ranges = []
    for obj in selected:
        for entry in obj.findall('range'):
            low, high = float(entry.attrib['min_z']), float(entry.attrib['max_z'])
            options: JsonObject = {node.attrib['opt_key']: node.text for node in entry.findall('option')}
            if 'layer_height' not in options:
                raise ValueError('Adaptive height ranges must contain layer_height')
            height = _number(options, 'layer_height', 0.)
            if not (math.isfinite(low) and math.isfinite(high) and 0 <= low < high and minimum <= height <= maximum):
                raise ValueError('Adaptive height range is outside the effective layer limits')
            ranges.append((low, high, height))
    return sorted(ranges)


@dataclass(frozen=True)
class _FaceSpan:
    bottom: float
    top: float
    cosine: float
    sine: float

    @classmethod
    def measure(cls, triangle: Triangle) -> _FaceSpan:
        normal = _unit(_cross(_subtract(triangle[1], triangle[0]), _subtract(triangle[2], triangle[0])))
        x, y, z = map(_f32, normal)
        levels = [point[2] for point in triangle]
        return cls(min(levels), max(levels), abs(z), _f32(math.sqrt(_f32(_f32(x * x) + _f32(y * y)))))

    def cusp(self, deviation: float) -> float:
        roughness = _f32(deviation / _f32(.184))
        if self.cosine <= 1e-5:
            return roughness
        return min(roughness, _f32(1.44 * deviation * math.sqrt(_f32(self.sine / self.cosine))))


class _AdaptiveSweep:
    """Bambu SlicingAdaptive::next_layer_height's sorted facet event sweep."""
    def __init__(self, triangles: list[Triangle], minimum: float, maximum: float, deviation: float):
        self.faces = sorted((_FaceSpan.measure(face) for face in triangles), key=lambda f: (f.bottom, f.top))
        self.minimum, self.maximum, self.deviation = _f32(minimum), _f32(maximum), _f32(deviation)
        self.cursor = 0

    def next(self, previous_top: float) -> float:
        z = _f32(previous_top)
        height, index = self.maximum, self.cursor
        first: int | None = None
        while index < len(self.faces) and self.faces[index].bottom < z:
            face = self.faces[index]
            if face.top > z:
                if first is None:
                    first = index
                if face.top >= z + .0001:
                    height = min(height, face.cusp(self.deviation))
            index += 1
        if first is not None:
            self.cursor = first
        height = max(height, self.minimum)
        while height > self.minimum and index < len(self.faces):
            face = self.faces[index]
            if face.bottom >= _f32(z + height):
                break
            if face.top >= z + .0001:
                cusp, distance = face.cusp(self.deviation), _f32(face.bottom - z)
                height = distance if cusp < distance else min(cusp, height)
                height = max(height, self.minimum)
            index += 1
        return height


def adaptive_layer_profile(project_path: Path, object_id: int, config: JsonObject, quality: float) -> list[float]:
    """Generate a Bambu profile for uniform nozzles and unmodified Z scale.

    Source: BambuStudio 926a719 Slicing.cpp:247–337 and SlicingAdaptive.cpp:72–198.
    Bambu's generation ignores explicit ranges; this API intentionally overlays
    them after the cusp/gradation step to preserve user-authored height ranges.
    The editor gates this source-derived generator against the bound engine SHA.
    """
    if type(quality) not in (int, float) or not math.isfinite(quality) or not 0 <= quality <= 1:
        raise ValueError('quality must be a finite native parameter in 0..1 (0 is finest)')
    if type(object_id) is not int or not isinstance(config, dict):
        raise ValueError('Specify a root object id and effective configuration')
    geometry = _Geometry(project_path.resolve(strict=True))
    if object_id not in geometry.instances:
        raise ValueError('Unknown source root object id')
    node = geometry.object_settings.get(object_id)
    settings = dict(config)
    if node is not None:
        settings.update(_metadata(node))
    nominal = _number(settings, 'layer_height', .2)
    first = _number(settings, 'initial_layer_print_height', .2)
    if first <= 0:
        first = nominal
    defaults: list[tuple[str, JsonValue]] = [('nozzle_diameter', [.4]), ('min_layer_height', [0.]), ('max_layer_height', [0.])]
    vectors = [_numbers(settings, name, default) for name, default in defaults]
    if any(any(abs(item - values[0]) > 1e-12 for item in values) for values in vectors):
        raise ValueError('Adaptive profile currently requires uniform per-nozzle layer limits')
    if any(item != 100. for item in _numbers(settings, 'filament_shrinkage_compensation_z', [100.])):
        raise ValueError('Adaptive profile with non-default Z shrinkage compensation is unsupported')
    nozzle, low, high = (values[0] for values in vectors)
    if nozzle <= 0 or low < 0 or high < 0 or nominal <= 0 or first <= 0:
        raise ValueError('Layer and nozzle dimensions must be positive')
    minimum = .07 if low == 0 else max(.01, low)
    maximum = max(minimum, .75 * nozzle if high == 0 else high)
    minimum, maximum = min(minimum, nominal), max(maximum, nominal)
    raft = _number(settings, 'raft_layers', 0.)
    if raft < 0 or not raft.is_integer():
        raise ValueError('raft_layers must be a non-negative integer')
    if raft:
        first = nominal
    pose = geometry.instances[object_id][0]
    triangles = [tuple(tuple(_f32(value) for value in _point(point, pose)) for point in face)
                 for face in geometry.mesh(object_id)]
    top = max(vertex[2] for face in triangles for vertex in face)
    if top <= 0:
        raise ValueError('Adaptive profiling requires object geometry above Z=0')
    ranges = _height_ranges(geometry, object_id, minimum, maximum)
    q = _f32(quality)
    endpoint, factor = (minimum, 2 * q) if q < .5 else (maximum, 2 * (1 - q))
    sweep = _AdaptiveSweep(triangles, minimum, maximum, endpoint + (nominal - endpoint) * factor)
    profile = [0., first] if raft else [0., first, first, first]
    z = first
    while z + .0001 < top:
        height = min(_f32(maximum), sweep.next(z))
        previous = profile[-1]
        if abs(height - previous) > .04:
            height = _f32(previous + math.copysign(.04, height - previous))
        override = next((value for low, high, value in ranges if low <= z <= high), None)
        if override is not None:
            height = _f32(override)
        profile.extend((z, height))
        z += height
    gap = top - profile[-2]
    if gap > 0:
        profile.extend((top, min(maximum, max(minimum, gap))))
    return profile
