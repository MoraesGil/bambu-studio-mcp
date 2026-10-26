"""Lossless tree parsing and scoped namespace serialization for Bambu 3MF."""
from __future__ import annotations

import copy
import io
import re
from collections.abc import Sequence
from threading import RLock
from typing import TypeAlias, cast
from xml.etree import ElementTree as ET

XML_LOCK = RLock()
TreeState: TypeAlias = tuple[object, tuple[tuple[str, str], ...], str | None, str | None, tuple['TreeState', ...]]


def local_name(tag: object) -> str:
    return str(tag).split('}')[-1] if isinstance(tag, str) else ''


def children(parent: ET.Element | None, tag: str) -> list[ET.Element]:
    return [] if parent is None else [node for node in parent if local_name(node.tag) == tag]


def child(parent: ET.Element | None, tag: str) -> ET.Element | None:
    return next((node for node in children(parent, tag)), None)


def metadata(parent: ET.Element | None) -> dict[str, str]:
    values: dict[str, str] = {}
    for node in children(parent, 'metadata'):
        values[node.attrib['key']] = node.get('value', '')
    return values


def set_metadata(parent: ET.Element, key: str, value: object) -> None:
    existing = [node for node in children(parent, 'metadata') if node.get('key') == key]
    if value is None:
        for node in existing:
            parent.remove(node)
        return
    if existing:
        target, *duplicates = existing
        for node in duplicates:
            parent.remove(node)
    else:
        target = ET.Element('metadata', {'key': key})
        parent.insert(0, target)
    target.set('value', ('1' if value else '0') if isinstance(value, bool) else str(value))


def read_xml(payload: bytes) -> tuple[ET.Element, list[tuple[str, str]]]:
    bindings: dict[tuple[str, str], None] = {}
    for event, binding in ET.iterparse(io.BytesIO(payload), events=('start-ns',)):
        bindings[binding] = None
    builder = ET.TreeBuilder(insert_comments=True, insert_pis=True)
    return ET.fromstring(payload, parser=ET.XMLParser(target=builder)), list(bindings)


def tree_state(root: ET.Element) -> TreeState:
    descendants = tuple(map(tree_state, root))
    return root.tag, tuple(root.items()), root.text, root.tail, descendants


def _serialize(root: ET.Element, bindings: Sequence[tuple[str, str]], retain_unused: bool) -> bytes:
    # Namespace registration is global to ElementTree. Both entry and restore
    # are protected, including exceptional serialization exits.
    with XML_LOCK:
        registry = cast(dict[str, str], getattr(ET, '_namespace_map'))
        before = registry.copy()
        try:
            for prefix, uri in bindings:
                if not re.fullmatch(r'ns\d+', prefix):
                    ET.register_namespace(prefix, uri)
            prepared = copy.deepcopy(root)
            if retain_unused:
                emitted = ET.tostring(prepared, encoding='unicode')
                declared = set(re.findall(r'\sxmlns(?::([^\s=]+))?=', emitted))
                for prefix, uri in bindings:
                    if prefix != 'xml' and prefix not in declared:
                        prepared.set('xmlns' + (':' + prefix if prefix else ''), uri)
            return ET.tostring(prepared, encoding='unicode', xml_declaration=True).encode('utf-8')
        finally:
            registry.clear()
            registry.update(before)


def xml_bytes(root: ET.Element, namespaces: Sequence[tuple[str, str]] = ()) -> bytes:
    return _serialize(root, namespaces, True)


def native_xml(tree: ET.Element, core: str, production: str) -> bytes:
    return _serialize(tree, (('', core), ('p', production)), False)
