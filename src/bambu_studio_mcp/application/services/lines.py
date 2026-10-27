"""Streaming G-code line windows with bounded consumption."""
from collections.abc import Iterable

from ...domain.types.values import LineRange
from ..contracts.artifacts import GcodeLines


def bounded_lines(stream: Iterable[str], job_id: str, plate: int,
                  start: int = 1, count: int = 200) -> GcodeLines:
    if type(start) is not int or type(count) is not int or count not in range(1, 1001) or start <= 0:
        raise ValueError('Line range must start at 1 or later and contain 1..1000 lines')
    requested = LineRange(start, start + count - 1)
    source = iter(stream)
    for _ in range(requested.first - 1):
        if next(source, None) is None:
            break
    lines: list[str] = []
    for _ in range(count):
        line = next(source, None)
        if line is None:
            break
        lines.append(line.rstrip('\r\n'))
    return {'job_id': job_id, 'plate': plate, 'start_line': requested.first, 'lines': lines}
