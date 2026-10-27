"""Require the Application marker used by Bambu's complete-project importer."""
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


def ensure_bambu_project(path: Path) -> None:
    try:
        with ZipFile(path) as archive:
            model = ET.fromstring(archive.read('3D/3dmodel.model'))
        for metadata in model.findall('{*}metadata'):
            if metadata.get('name') == 'Application':
                if (metadata.text or '').startswith('BambuStudio-'):
                    return
    except (BadZipFile, KeyError, ET.ParseError) as error:
        raise ValueError('Input must be a complete native Bambu Studio project') from error
    # bbs_3mf.cpp::_handle_metadata selects settings preservation by this exact
    # prefix. A generic or foreign 3MF otherwise imports as geometry silently.
    raise ValueError('Input is not recognized as a Bambu Studio project; native import would ignore its saved settings. Create a native project from meshes or supply a saved Bambu Studio 3MF.')
