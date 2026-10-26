"""Reject inputs for which the native importer discards project settings."""
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


def ensure_bambu_project(path: Path) -> None:
    with ZipFile(path) as archive:
        try:
            root = ET.fromstring(archive.read('3D/3dmodel.model'))
        except (KeyError, ET.ParseError) as error:
            raise ValueError('Input must be a complete native Bambu Studio project') from error
    # Match Bambu 926a719 bbs_3mf.cpp::_handle_metadata: a generic 3MF
    # can contain project_settings.config yet still import as geometry only.
    recognized = any(node.get('name') == 'Application' and (node.text or '').startswith('BambuStudio-')
                     for node in root.findall('{*}metadata'))
    if not recognized:
        raise ValueError('Input is not recognized as a Bambu Studio project; native import would ignore its saved settings. Create a native project from meshes or supply a saved Bambu Studio 3MF.')
