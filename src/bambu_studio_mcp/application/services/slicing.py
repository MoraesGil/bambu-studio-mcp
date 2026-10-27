"""One slicing transaction over artifact, engine and runner capabilities."""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable

from ...domain.types.json import JsonObject
from ..contracts.slicing import ArtifactRepository, NativeRunner, SliceEngine, SliceRequest
from .admission import NativeAdmission


class Slicing:
    def __init__(self, engine: SliceEngine, artifacts: ArtifactRepository, runner: NativeRunner,
                 admission: NativeAdmission, clock: Callable[[], float] = time.perf_counter) -> None:
        self.engine = engine
        self.artifacts = artifacts
        self.runner = runner
        self.admission = admission
        self.clock = clock

    def slice_3mf(self, input_3mf: str, plate: int = 0, force: bool = False,
                  allow_newer_file: bool = True, *, timeout: float = 1800) -> JsonObject:
        request = SliceRequest(plate=plate, force=force, allow_newer_file=allow_newer_file)
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be a positive finite number')
        started_at = self.clock()
        source = self.artifacts.prepare_source(input_3mf)
        engine = self.engine.identity()
        cache_contract = {'engine': engine, 'input': source.sha256, 'contract': 1,
                          'allow_newer_file': request.allow_newer_file, 'plate': request.plate}
        serialized = json.dumps(cache_contract, sort_keys=True, allow_nan=False).encode('utf-8')
        cache_key = hashlib.sha256(serialized).hexdigest()
        with self.admission.session():
            self.engine.verify()
            if not request.force:
                hit = self.artifacts.cached(cache_key, request, source, engine)
                if hit is not None:
                    return dict(hit, cached=True, request_elapsed_seconds=self.clock() - started_at,
                                engine_status=self.engine.status())
            workspace = self.artifacts.create(source)
            self.engine.verify()
            elapsed = self.runner.run(workspace, request, engine, timeout)
            self.engine.verify()
            return self.artifacts.publish(workspace, cache_key, request, engine, self.engine.status(),
                                          elapsed, self.clock() - started_at)
