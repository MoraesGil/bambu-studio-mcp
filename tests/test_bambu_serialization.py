"""Bambu ConfigOptionPointsGroups preserves the polygon/extruder boundary."""
def test_point_groups_xml_uses_native_hash_separator(monkeypatch):
    from bambu_studio_mcp.adapters.three_mf import document
    monkeypatch.setattr(document, '_option_types', lambda: {'extruder_printable_area': 'coPointsGroups'})
    polygons = ['0x0,256x0,256x256,0x256', '10x10,20x10,20x20,10x20']
    assert document._native_option_value('extruder_printable_area', polygons) == '#'.join(polygons)
