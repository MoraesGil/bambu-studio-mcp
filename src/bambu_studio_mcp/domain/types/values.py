"""Immutable domain values with explicit constructor invariants."""
from __future__ import annotations

import math
from dataclasses import dataclass


def _finite_numbers(*values: object) -> bool:
    for value in values:
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        try:
            if not math.isfinite(value):
                return False
        except OverflowError:
            return False
    return True


@dataclass(frozen=True)
class ObjectId:
    value: int

    def __post_init__(self) -> None:
        if not (type(self.value) is int and self.value >= 1):
            raise ValueError('Object IDs must be positive integers')


@dataclass(frozen=True)
class Sha256:
    value: str

    def __post_init__(self) -> None:
        valid = isinstance(self.value, str) and len(self.value) == 64
        if not valid or set(self.value) - set('0123456789abcdef'):
            raise ValueError('SHA256 must contain 64 lowercase hexadecimal characters')

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True)
class XYZ:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if not _finite_numbers(self.x, self.y, self.z):
            raise ValueError('Coordinates must contain three finite XYZ numbers')

    def as_list(self) -> list[float]:
        return list(map(float, (self.x, self.y, self.z)))


@dataclass(frozen=True)
class BoxDimensions(XYZ):
    def __post_init__(self) -> None:
        XYZ.__post_init__(self)
        if min(self.x, self.y, self.z) <= 0:
            raise ValueError('dimensions must be strictly positive')


@dataclass(frozen=True)
class LineRange:
    first: int
    last: int

    def __post_init__(self) -> None:
        if not (type(self.first) is type(self.last) is int and 0 < self.first <= self.last):
            raise ValueError('Line ranges must be ordered positive integers')


@dataclass(frozen=True)
class HeightBand:
    low: float
    high: float
    height: float

    def __post_init__(self) -> None:
        valid = _finite_numbers(self.low, self.high, self.height)
        if not valid or not (0 <= self.low < self.high and self.height > 0):
            raise ValueError('A band needs 0 <= min_z < max_z and positive layer_height')
