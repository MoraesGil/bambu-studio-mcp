"""Bambu OPC package ownership, opaque members and derived-output removal."""
from __future__ import annotations

import copy
import os
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from .xml import children, local_name, read_xml, tree_state, xml_bytes

CORE = 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'
PRODUCTION = 'http://schemas.microsoft.com/3dmanufacturing/production/2015/06'
CONFIG = 'Metadata/project_settings.config'
SETTINGS = 'Metadata/model_settings.config'
RANGES = 'Metadata/layer_config_ranges.xml'
PROFILE = 'Metadata/layer_heights_profile.txt'
BRIM = 'Metadata/brim_ear_points.txt'
CUT = 'Metadata/cut_information.xml'
CUSTOM_GCODE = 'Metadata/custom_gcode_per_layer.xml'
PARKED = 'Metadata/slicer_mcp_parked_instances.xml'

OUTPUT_KEYS = frozenset(('gcode_file', 'gcode_file_md5', 'thumbnail_file',
    'thumbnail_no_light_file', 'top_file', 'pick_file', 'pattern_file',
    'pattern_bbox_file', 'prediction', 'weight', 'is_sliced_valid'))
PREVIEW_MEMBER = re.compile(r'Metadata/(?:plate_\d+(?:_small)?\.(?:png|json)|plate_no_light_\d+\.png|(?:top|pick|pattern)_\d+\.png)', re.I)
CONTENT_TYPES = {RANGES: 'application/xml', PROFILE: 'text/plain', BRIM: 'text/plain',
                 CUT: 'application/xml', PARKED: 'application/xml'}


def relationship_target(rels_name: str, target: str) -> str:
    base = posixpath.dirname(posixpath.dirname(rels_name))
    return posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join(base, target))


class BambuPackage:
    """Keep an ordered payload inventory; parse only members owned by an edit."""

    def __init__(self, source: Path) -> None:
        self.path = source.resolve()
        self.members: dict[str, bytes] = {}
        self.infos: dict[str, zipfile.ZipInfo] = {}
        with zipfile.ZipFile(self.path) as archive:
            self.comment = archive.comment
            for info in archive.infolist():
                if info.filename in self.members:
                    raise ValueError('Duplicate ZIP members make project edits ambiguous')
                self.infos[info.filename] = copy.copy(info)
                self.members[info.filename] = archive.read(info)
        self.root_name = '3D/3dmodel.model'
        if '_rels/.rels' in self.members:
            relationships, _ = read_xml(self.members['_rels/.rels'])
            models = [node for node in relationships
                      if node.get('Type', '').endswith('/3dmodel') and node.get('TargetMode') != 'External']
            if models:
                self.root_name = relationship_target('_rels/.rels', models[0].get('Target', ''))
        if self.root_name not in self.members:
            raise ValueError('The package has no root 3MF model')

    def write(self, target: Path, members: dict[str, bytes]) -> None:
        """Publish a complete new archive with exclusive destination creation."""
        target = Path(target)
        if target.resolve() == self.path:
            raise ValueError('Project.save never overwrites its source')
        if target.exists():
            raise FileExistsError(target)
        descriptor, staging_name = tempfile.mkstemp(prefix='.bambu-package-', dir=target.parent)
        staging = Path(staging_name)
        os.close(descriptor)
        try:
            with zipfile.ZipFile(staging, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.comment = self.comment
                for name, payload in members.items():
                    info = self.infos.get(name)
                    archive.writestr(copy.copy(info) if info is not None else name, payload)
            os.link(staging, target)
        finally:
            staging.unlink(missing_ok=True)


def without_slice_outputs(members: dict[str, bytes], settings: ET.Element, deleted_members: set[str] | None = None) -> tuple[dict[str, bytes], ET.Element]:
    """Invalidate saved slicer state while preserving opaque Auxiliaries data."""
    output = members.copy()
    clean_settings = copy.deepcopy(settings)
    removed = {name for name in output if not name.startswith('Auxiliaries/') and (
        name.lower().endswith(('.gcode', '.gcode.md5'))
        or name == 'Metadata/slice_info.config' or PREVIEW_MEMBER.fullmatch(name))}
    removed.update(deleted_members or ())
    for parent in clean_settings.iter():
        obsolete = [node for node in children(parent, 'metadata') if node.get('key') in OUTPUT_KEYS]
        for node in obsolete:
            referenced = node.get('value', '').lstrip('/')
            if referenced in output and not referenced.startswith('Auxiliaries/'):
                removed.add(referenced)
            parent.remove(node)
    for name, payload in tuple(output.items()):
        if not name.endswith('.rels'):
            continue
        root, namespaces = read_xml(payload)
        discarded = []
        for node in root:
            if node.get('TargetMode') == 'External':
                continue
            destination = relationship_target(name, node.get('Target', ''))
            thumbnail = 'thumbnail' in node.get('Type', '') and destination.startswith('Metadata/')
            if thumbnail:
                removed.add(destination)
            if thumbnail or destination in removed:
                discarded.append(node)
        if discarded:
            for node in discarded:
                root.remove(node)
            output[name] = xml_bytes(root, namespaces)
    for name in removed:
        output.pop(name, None)
    if '[Content_Types].xml' in output:
        payload = output['[Content_Types].xml']
        types, namespaces = read_xml(payload)
        before = tree_state(types)
        extensions = {name.rsplit('.', 1)[-1].lower() for name in output if '.' in name}
        for node in list(types):
            deleted_override = node.get('PartName', '').lstrip('/') in removed
            unused_default = local_name(node.tag) == 'Default' and node.get('Extension', '').lower() not in extensions
            if deleted_override or unused_default:
                types.remove(node)
        for name, mime in CONTENT_TYPES.items():
            extension = name.rsplit('.', 1)[-1]
            covered = any(node.get('PartName') == '/' + name or node.get('Extension') == extension for node in types)
            if name in output and not covered:
                namespace = types.tag.partition('}')[0] + '}' if '}' in types.tag else ''
                ET.SubElement(types, namespace + 'Override', {'PartName': '/' + name, 'ContentType': mime})
        if before != tree_state(types):
            output['[Content_Types].xml'] = xml_bytes(types, namespaces)
    return output, clean_settings
