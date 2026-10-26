"""Bambu ordinal metadata, with migration of the old wrapper's profile v2.

BambuStudio 926a719 bbs_3mf.cpp reads profiles, cut records and height ranges
by 1-based ModelObject order, determined by first root build occurrence.
"""
from __future__ import annotations

import copy
import math
import re
from collections.abc import Sequence
from xml.etree import ElementTree as ET
from .package import BRIM, CUT, PROFILE, RANGES
from .xml import children, read_xml, tree_state, xml_bytes
from ...domain.types.values import HeightBand


class HeightStore:
    def __init__(self, payloads: dict[str, bytes], roots: Sequence[int]) -> None:
        self.payloads = payloads
        self.roots = list(roots)

    def profiles(self) -> dict[int, list[float]]:
        payload = self.payloads.get(PROFILE)
        if payload is None:
            return {}
        records = payload.decode('utf-8-sig').splitlines()
        keyed_by_root = False
        if records and records[0].startswith('layer_heights_profile_format_version='):
            keyed_by_root = int(records.pop(0).partition('=')[2]) >= 2
        result: dict[int, list[float]] = {}
        for record in filter(str.strip, records):
            identifier, separator, text = record.partition('|')
            if not separator or not re.fullmatch(r'object_id=\d+', identifier):
                raise ValueError('Invalid native layer height profile record')
            native_id = int(identifier.partition('=')[2])
            if keyed_by_root:
                oid = native_id
            else:
                if not 1 <= native_id <= len(self.roots):
                    raise ValueError('Layer profile ordinal does not identify a root object')
                oid = self.roots[native_id - 1]
            if oid in result:
                raise ValueError('Duplicate layer height profile record')
            result[oid] = [float(field) for field in text.split(';')]
        return result

    def write_profiles(self, profiles: dict[int, list[float]]) -> None:
        unknown = set(profiles).difference(self.roots)
        if unknown:
            raise ValueError('Layer profile does not identify an active root object')
        lines = []
        for ordinal, oid in enumerate(self.roots, 1):
            if oid in profiles:
                lines.append(f'object_id={ordinal}|' + ';'.join(format(v, '.17g') for v in profiles[oid]))
        if lines:
            self.payloads[PROFILE] = ('\n'.join(lines) + '\n').encode()
        else:
            self.payloads.pop(PROFILE, None)

    @staticmethod
    def validate_profile(profile: Sequence[float]) -> list[float]:
        values = list(map(float, profile))
        pairs = list(zip(values[::2], values[1::2]))
        valid = len(values) >= 6 and len(values) % 2 == 0 and all(math.isfinite(v) for v in values)
        valid = valid and all(z >= 0 and height > 0 for z, height in pairs)
        valid = valid and all(a[0] <= b[0] for a, b in zip(pairs, pairs[1:]))
        if not valid or pairs[-1][0] <= pairs[0][0]:
            raise ValueError('A native layer profile needs at least three ordered Z/positive-height pairs')
        return values

    def ranges(self) -> tuple[ET.Element, list[tuple[str, str]]]:
        return read_xml(self.payloads[RANGES]) if RANGES in self.payloads else (ET.Element('objects'), [])

    def write_ranges(self, tree: ET.Element, namespaces: Sequence[tuple[str, str]]) -> None:
        if children(tree, 'object'):
            self.payloads[RANGES] = xml_bytes(tree, namespaces)
        else:
            self.payloads.pop(RANGES, None)

    def set_band(self, oid: int, low: float | None, high: float | None, height: float | None, clear: bool) -> None:
        ordinal = self.roots.index(oid) + 1
        tree, namespaces = self.ranges()
        entry = next((node for node in children(tree, 'object') if node.get('id') == str(ordinal)), None)
        if clear:
            if entry is not None:
                tree.remove(entry)
                self.write_ranges(tree, namespaces)
            return
        if low is None or high is None or height is None:
            raise ValueError('A band needs finite min_z, max_z and a positive layer_height')
        band = HeightBand(float(low), float(high), float(height))
        exact = None
        for candidate in children(entry, 'range'):
            start, end = float(candidate.attrib['min_z']), float(candidate.attrib['max_z'])
            if (start, end) == (band.low, band.high):
                exact = candidate
            elif start < band.high and band.low < end:
                raise ValueError("Height ranges must be disjoint; clear the object's bands first")
        profiles = self.profiles()
        if entry is None:
            entry = ET.SubElement(tree, 'object', {'id': str(ordinal)})
        if exact is None:
            exact = ET.SubElement(entry, 'range', {'min_z': str(low), 'max_z': str(high)})
        options = [node for node in children(exact, 'option') if node.get('opt_key') == 'layer_height']
        option = options[0] if options else ET.SubElement(exact, 'option', {'opt_key': 'layer_height'})
        option.text = str(height)
        for duplicate in options[1:]:
            exact.remove(duplicate)
        profiles.pop(oid, None)
        self.write_ranges(tree, namespaces)
        self.write_profiles(profiles)

    def remap(self, previous_roots: Sequence[int], clone: tuple[int, int] | None = None) -> None:
        """Resolve ordinals to identities before rebuilding the ordinal index."""
        new_ordinals = {oid: i for i, oid in enumerate(self.roots, 1)}
        for name in (RANGES, CUT):
            if name not in self.payloads:
                continue
            tree, namespaces = read_xml(self.payloads[name])
            before = tree_state(tree)
            for entry in children(tree, 'object'):
                ordinal = int(entry.attrib['id'])
                if not 1 <= ordinal <= len(previous_roots):
                    raise ValueError(f'{name}: ordinal does not identify a root object')
                identity = previous_roots[ordinal - 1]
                duplicate = copy.deepcopy(entry) if clone and identity == clone[0] else None
                if identity in new_ordinals:
                    entry.set('id', str(new_ordinals[identity]))
                else:
                    tree.remove(entry)
                if duplicate is not None:
                    assert clone is not None
                    duplicate.set('id', str(new_ordinals[clone[1]]))
                    tree.append(duplicate)
            if not children(tree, 'object'):
                self.payloads.pop(name)
            elif before != tree_state(tree):
                self.payloads[name] = xml_bytes(tree, namespaces)
        if BRIM in self.payloads:
            output: list[bytes] = []
            copied: bytes | None = None
            for line in self.payloads[BRIM].splitlines(keepends=True):
                if line.startswith(b'brim_points_format_version=') or not line.strip():
                    output.append(line)
                    continue
                match = re.fullmatch(rb'object_id=(\d+)\|(.*)', line, re.DOTALL)
                if match is None or not 1 <= int(match[1]) <= len(previous_roots):
                    raise ValueError('Brim point ordinal does not identify a root object')
                identity = previous_roots[int(match[1]) - 1]
                if clone and identity == clone[0]:
                    copied = match[2]
                if identity in new_ordinals:
                    output.append(f'object_id={new_ordinals[identity]}|'.encode() + match[2])
            if copied is not None:
                assert clone is not None
                if output and not output[-1].endswith(b'\n'):
                    output[-1] += b'\n'
                output.append(f'object_id={new_ordinals[clone[1]]}|'.encode() + copied)
            if any(line.startswith(b'object_id=') for line in output):
                self.payloads[BRIM] = b''.join(output)
            else:
                self.payloads.pop(BRIM)
