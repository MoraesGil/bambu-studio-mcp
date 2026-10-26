"""Validated row-vector affine values used by Bambu's 3MF importer.

The 12 stored values are the first three columns of a homogeneous 4x4
matrix. The native writer emits those columns in column-major order for its
column-vector Eigen representation (bbs_3mf.cpp::add_transformation).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

IDENTITY_VALUES = (1., 0., 0., 0., 1., 0., 0., 0., 1., 0., 0., 0.)


@dataclass(frozen=True)
class Affine:
    values: tuple[float, ...] = IDENTITY_VALUES

    def __post_init__(self) -> None:
        if len(self.values) != 12 or not all(math.isfinite(v) for v in self.values):
            raise ValueError('A 3MF transform must contain 12 finite numbers')

    @classmethod
    def of(cls, values: Sequence[float]) -> Affine:
        return cls(tuple(float(v) for v in values))

    @classmethod
    def parse(cls, text: str | None) -> Affine:
        return cls(tuple(map(float, text.split()))) if text else cls()

    def rows(self) -> tuple[tuple[float, ...], ...]:
        return tuple(tuple(self.values[i:i + 3]) + (float(i == 9),) for i in range(0, 12, 3))

    def then(self, other: Affine) -> Affine:
        columns = tuple(zip(*other.rows()))
        product = [math.fsum(a * b for a, b in zip(row, col))
                   for row in self.rows() for col in columns[:3]]
        return Affine.of(product)

    def apply(self, xyz: Sequence[float]) -> tuple[float, ...]:
        vector = (*xyz[:3], 1.)
        return tuple(math.fsum(a * b for a, b in zip(vector, column))
                     for column in tuple(zip(*self.rows()))[:3])

    def shift(self, offset: Sequence[float]) -> Affine:
        return Affine(self.values[:9] + tuple(self.values[9 + i] + offset[i] for i in range(3)))

    def serialize(self) -> str:
        return ' '.join(format(0. if abs(v) < 1e-14 else v, '.17g') for v in self.values)

    @classmethod
    def pose_delta(cls, scale: Sequence[float], angles: Sequence[float]) -> Affine:
        if any(v == 0 for v in scale):
            raise ValueError('Zero scale would make the object degenerate')
        current = cls(tuple(scale[i] if i == j else 0. for i in range(3) for j in range(3)) + (0., 0., 0.))
        for axis, angle in enumerate(angles):
            c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            indices = [(axis + 1) % 3, (axis + 2) % 3]
            entries = list(IDENTITY_VALUES)
            for i in indices:
                entries[3 * i + i] = c
            entries[3 * indices[0] + indices[1]] = s
            entries[3 * indices[1] + indices[0]] = -s
            current = current.then(cls.of(entries))
        return current
