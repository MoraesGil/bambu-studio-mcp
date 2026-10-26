"""ConfigOption serialization, based on the pinned Bambu Config.hpp types."""
from __future__ import annotations

import re
from ...domain.types.json import JsonValue


def escape_cstyle(value: str) -> str:
    escapes = {'\\': '\\\\', '"': '\\"', '\r': '\\r', '\n': '\\n'}
    return ''.join(escapes.get(character, character) for character in value)


def option_text(key: str, value: JsonValue | tuple[JsonValue, ...], kind: str | None) -> str | None:
    if value is None:
        return None

    def scalar(item: JsonValue) -> str:
        text = str(int(item)) if isinstance(item, bool) else str(item)
        if kind in ('coBool', 'coBools') and text.lower() in ('true', 'false'):
            return str(int(text.lower() == 'true'))
        if kind in ('coPercent', 'coPercents') and text != 'nil':
            return text if text.endswith('%') else text + '%'
        return text

    def coordinate(item: JsonValue | tuple[JsonValue, ...], count: int, separator: str) -> str:
        coordinates = re.split('[,x]', item) if isinstance(item, str) else item
        if not isinstance(coordinates, (list, tuple)) or len(coordinates) != count:
            raise ValueError(f'{key}: expected a {count}-coordinate point')
        return separator.join(map(str, coordinates))

    if kind in ('coPoint', 'coPoint3'):
        return coordinate(value, 2 if kind == 'coPoint' else 3, ',')
    if kind == 'coStrings' and not isinstance(value, (tuple, list)):
        value = [str(value)]
    if isinstance(value, (tuple, list)):
        if kind == 'coPointsGroups':
            if any(not isinstance(entry, str) for entry in value):
                raise ValueError(f'{key}: point groups require serialized polygon strings')
            return '#'.join(map(str, value))
        if kind == 'coPoints':
            return ','.join(coordinate(entry, 2, 'x') for entry in value)
        if kind == 'coStrings':
            result = []
            for entry in value:
                if not isinstance(entry, str):
                    raise ValueError(f'{key}: string vectors require strings')
                complex_value = (not entry and len(value) == 1) or any(c.isspace() or c in '\\";' for c in entry)
                result.append('"' + escape_cstyle(entry) + '"' if complex_value else entry)
            return ';'.join(result)
        if kind in ('coFloats', 'coInts', 'coBools', 'coPercents', 'coFloatsOrPercents', 'coFloatOrPercents', 'coEnums'):
            return ','.join(map(scalar, value))
        raise ValueError(f'{key}: no native vector serialization is defined')
    return escape_cstyle(str(value)) if kind == 'coString' else scalar(value)
