"""Published tool declarations: signatures and documentation, no implementation."""
from typing import Annotated, Protocol
from pydantic import StrictFloat, StrictInt, WithJsonSchema
from ...domain.types.json import JsonObject
WireObject = Annotated[JsonObject, WithJsonSchema({"additionalProperties": True, "type": "object"})]

class BambuTools(Protocol):

    @staticmethod
    async def create_project(mesh_paths: list[str], printer_preset: str, process_preset: str, filament_presets: list[str]) -> JsonObject:
        """Create a private native 3MF from STL/OBJ meshes and named Bambu presets.

        Select a printer, process and one or more filament presets explicitly.
        Meshes and installed presets stay read-only. Returns project_id, revision
        and source hashes for the usual edit/export/slice tools. Native thumbnails
        may require an available graphics context; no Studio window is opened.
        """
        ...

    @staticmethod
    async def load_model(path: str) -> JsonObject:
        """Load a complete saved 3MF into a private, versioned editing workspace.

        Returns project_id, revision sha256 and working path. The input and
        installed presets are read-only. Use project_id for the editing tools.
        """
        ...

    @staticmethod
    async def get_status(project_id: str) -> JsonObject:
        """Describe the working project, selected plate, objects and revision."""
        ...

    @staticmethod
    async def get_config(project_id: str, keys: list[str] | None=None) -> JsonObject:
        """Read saved project settings (optionally selected keys), with secrets redacted."""
        ...

    @staticmethod
    async def set_config(project_id: str, changes: WireObject, expected_sha256: str | None=None, allow_custom_gcode: bool=False) -> JsonObject:
        """Edit project settings and return a new 3MF revision; never edits global presets.

        Pass the last sha256 to reject stale edits. Filament arrays retain their
        slot count/order. Custom G-code requires allow_custom_gcode; external
        post-processing scripts and transport credentials are unsupported.
        """
        ...

    @staticmethod
    async def edit_project(project_id: str, operations: list[WireObject], expected_sha256: str | None=None, allow_custom_gcode: bool=False) -> JsonObject:
        """Apply several edits atomically, each with an operation name and its arguments.

        Supports set_config, set_object_config, add_modifier, duplicate/delete/transform_object,
        add/delete_plate, move_object_to_plate, arrange_plate, auto_orient,
        set_height_range, set_layer_height and reset_layer_height. Failure leaves
        the current revision unchanged; every successful edit needs a fresh slice.
        """
        ...

    @staticmethod
    async def list_objects(project_id: str) -> JsonObject:
        """List stable root object IDs, parts, instance ordinals and transforms."""
        ...

    @staticmethod
    async def set_object_config(project_id: str, object_id: int, changes: WireObject, part_id: int | None=None, expected_sha256: str | None=None) -> JsonObject:
        """Edit object/part overrides. extruder=0 inherits; 1..N selects a filament slot."""
        ...

    @staticmethod
    async def add_modifier(project_id: str, object_id: StrictInt, center: list[StrictFloat], dimensions: list[StrictFloat], changes: WireObject, name: str='Modifier', expected_sha256: str | None=None) -> JsonObject:
        """Add a box centered at absolute object-local XYZ mm before instance transforms.

        Affects all instances. Dimensions are positive XYZ mm. Supports regional
        outer_wall_speed, inner_wall_speed, sparse_infill_speed, top_surface_speed
        and internal_solid_infill_speed overrides as explicit arrays (e.g. [20]).
        Re-slice after editing.
        """
        ...

    @staticmethod
    async def duplicate_object(project_id: str, object_id: int, expected_sha256: str | None=None) -> JsonObject:
        """Duplicate a complete object while preserving its component meshes and painting."""
        ...

    @staticmethod
    async def delete_object(project_id: str, object_id: int, expected_sha256: str | None=None) -> JsonObject:
        """Remove the specified object from the working copy; preserves the input project."""
        ...

    @staticmethod
    async def transform_object(project_id: str, object_id: int, translate: list[float] | None=None, rotate: list[float] | None=None, scale: list[float] | None=None, instance_id: int | None=None, expected_sha256: str | None=None) -> JsonObject:
        """Apply XYZ translation deltas in mm, rotation deltas in degrees and scale factors.

        Rotation/scaling preserve the instance pivot. Omit instance_id to edit
        every instance of the object. Original component meshes remain intact.
        """
        ...

    @staticmethod
    async def arrange_plate(project_id: str, plate: int | None=None, expected_sha256: str | None=None) -> JsonObject:
        """Arrange a plate with the native engine; 0 reorganizes all unlocked plates.

        All-plate arrangement persists new plate counts and memberships.
        A single-plate arrangement must fit that plate; overflow fails atomically.
        """
        ...

    @staticmethod
    async def auto_orient(project_id: str, plate: int | None=None, expected_sha256: str | None=None) -> JsonObject:
        """Orient selected/explicit plate objects; 0 selects all unlocked plates."""
        ...

    @staticmethod
    async def list_plates(project_id: str) -> JsonObject:
        """List 1-based plates and their object/instance memberships."""
        ...

    @staticmethod
    async def add_plate(project_id: str, name: str='', expected_sha256: str | None=None) -> JsonObject:
        """Add an empty plate to the working project."""
        ...

    @staticmethod
    async def select_plate(project_id: str, plate: int) -> JsonObject:
        """Select the default target for subsequent editing and slice_project calls."""
        ...

    @staticmethod
    async def delete_plate(project_id: str, plate: int, expected_sha256: str | None=None) -> JsonObject:
        """Remove a working plate, parking its objects off-bed; retain at least one plate.

        Geometry is preserved. Use move_object_to_plate to restore parked objects.
        """
        ...

    @staticmethod
    async def move_object_to_plate(project_id: str, object_id: int, plate: int, instance_id: int | None=None, expected_sha256: str | None=None) -> JsonObject:
        """Move an object's instances to a 1-based plate, preserving their local offsets."""
        ...

    @staticmethod
    async def set_layer_height(project_id: str, object_id: int, mode: str, quality: float=0.5, expected_sha256: str | None=None) -> JsonObject:
        """Generate adaptive layers (quality 0=finer..1=coarser); reset clears profiles and Z bands."""
        ...

    @staticmethod
    async def set_height_range(project_id: str, object_id: int, min_z: float | None=None, max_z: float | None=None, layer_height: float | None=None, clear: bool=False, expected_sha256: str | None=None) -> JsonObject:
        """Set/update a Z-band's layer height in mm, relative to the object; clear removes bands."""
        ...

    @staticmethod
    async def list_presets(type: str | None=None, include_system: bool=False) -> JsonObject:
        """List declared read-only preset sources and working drafts; no global writes."""
        ...

    @staticmethod
    async def get_preset_config(type: str, name: str) -> JsonObject:
        """Resolve a named preset's inheritance with hashes/provenance; redact credentials."""
        ...

    @staticmethod
    async def select_preset(project_id: str, type: str, name: str, filament_slot: int=0, flow_mode: str='standard', expected_sha256: str | None=None) -> JsonObject:
        """Apply a resolved preset to the working project; filament_slot is 0-based.

        Explicit flow_mode selects standard/high_flow values where supported.
        Installed and canonical presets are never changed.
        """
        ...

    @staticmethod
    async def save_preset(project_id: str, type: str, name: str, filament_slot: int=0, detach: bool=False, allow_custom_gcode: bool=False) -> JsonObject:
        """Save current working settings as an artifact draft, never over an installed preset."""
        ...

    @staticmethod
    async def edit_preset(type: str, name: str, changes: WireObject, allow_custom_gcode: bool=False) -> JsonObject:
        """Edit an artifact draft only. Installed/canonical presets remain read-only."""
        ...

    @staticmethod
    async def rename_preset(type: str, name: str, new_name: str) -> JsonObject:
        """Rename an artifact draft only; cannot rename installed/canonical presets."""
        ...

    @staticmethod
    async def delete_preset(type: str, name: str) -> JsonObject:
        """Delete an artifact draft only; cannot remove installed/canonical presets."""
        ...

    @staticmethod
    def describe_setting(key: str) -> JsonObject | None:
        """Describe a setting from the Bambu Studio source catalog, including schema version."""
        ...

    @staticmethod
    def search_settings(query: str, limit: int=25) -> JsonObject:
        """Search Bambu Studio setting names, labels and descriptions."""
        ...

    @staticmethod
    async def slice_project(project_id: str, plate: int | None=None, force: bool=False, allow_newer_file: bool=True) -> JsonObject:
        """Slice the current working revision; omitted plate uses the selected plate, 0=all."""
        ...

    @staticmethod
    async def apply_and_slice(project_id: str, changes: WireObject, plate: int | None=None, expected_sha256: str | None=None) -> JsonObject:
        """Edit working settings and force a fresh official native slice of the new revision."""
        ...

    @staticmethod
    async def slice_3mf(input_3mf: str, plate: int=0, force: bool=False, allow_newer_file: bool=True) -> JsonObject:
        """Slice the saved 3MF using the bound official native engine. 0=all plates.

        Returns G-code paths/hashes, original source, settings and inspection
        resource URIs. force bypasses verified cache. allow_newer_file explicitly
        bypasses the native legacy producer-version gate and is recorded in the
        result/cache identity; disable it for strict version checking.
        No GUI or printer access.
        """
        ...

    @staticmethod
    async def recover_3mf(gcode_file: str) -> JsonObject:
        """Recover a preserved original for a known generated G-code hash.

        This does not reconstruct a mesh from G-code. Unknown/ambiguous sources
        fail explicitly; recovered source bytes are checked against their hash.
        """
        ...

    @staticmethod
    async def engine_status(check_updates: bool=True) -> JsonObject:
        """Report exact engine identity and optionally check upstream main.

        New commits produce an update/rebuild notice. Never downloads, compiles,
        switches or replaces the native engine.
        """
        ...
