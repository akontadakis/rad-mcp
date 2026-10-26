"""Radiance adapter. Owns all engine knowledge: command construction, invocation, output parsing."""

from .advisories import (
    RULES,
    UNCLASSIFIED_CODE,
    EngineAdvisory,
    Rule,
    Severity,
    classify,
    classify_line,
)
from .convert import (
    ConvertedObj,
    GeometryConversionError,
    ScratchOutsideRootsError,
    account,
    convert_obj,
    face_number,
    obj2rad_argv,
    rules_text,
    scale_scene_text,
    scratch_directory,
    transform_points,
    triangulate_obj,
    xform_argv,
)
from .env import RadianceEnv, RadianceNotFound, discover
from .ies import (
    SOURCE_CAL_BYTES,
    SOURCE_CAL_DIGEST,
    ConvertedIes,
    IesConversionError,
    convert_ies,
    ies2rad_argv,
)
from .evalglare import (
    DETAILED_FIELDS,
    PLAIN_FIELDS,
    EvalglareResult,
    GlareValues,
    build_evalglare_argv,
    parse_detailed,
    parse_plain,
    run_evalglare,
)
from .image import ImageInfo, ImageStatistics, parse_resolution, parse_view_line, read_image
from .jobs import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    AdvisoryRecord,
    ArtifactSpec,
    CommittedArtifact,
    JobError,
    JobNotFoundError,
    JobRecord,
    JobRecordExpiredError,
    JobState,
    JobStore,
    Launcher,
    RadianceLauncher,
    Step,
    StepRecord,
    SystemLauncher,
    is_same_process,
    kill_group,
    new_job_id,
    status_payload,
    worker_cores,
)
from .materials import (
    EmittedDefaults,
    EmittedMaterial,
    MaterialsEmissionError,
    MaterialsFile,
    emit_assignment,
    emit_defaults,
    scene_files_with_materials,
    write_materials,
)
from .octree import (
    EmptyOctreeError,
    Octree,
    OctreeSetOverflowError,
    OctreeTooManyFilesError,
    bounding_cube,
    build_ase_octree,
    build_black_octree,
    build_octree,
    build_suns_octree,
    refuse_inline_commands_in_files,
    refuse_inline_commands_in_text,
)
from .project import (
    ARTIFACT_DIRECTORY,
    LAYOUT_DIRECTORIES,
    MANIFEST_NAME,
    NORTH_INPUT_ID,
    REQUIRED_EDGES,
    Description,
    EdgeRequirement,
    EdgesIncompleteError,
    GeometryUnitsNotAppliedError,
    LoadedProject,
    ManifestUnreadableError,
    ProjectError,
    ProjectExistsError,
    ProjectNameError,
    ProjectNotEmptyError,
    ProjectNotFoundError,
    ProjectPaths,
    ProjectSummary,
    ProjectUnitsUnsetError,
    ProvenanceError,
    RunningJobError,
    UpdateResult,
    archive_project,
    bytes_digest,
    check_edges,
    create_project,
    delete_project,
    describe,
    drifted_inputs,
    evaluate_loaded,
    file_digest,
    file_refs,
    is_reusable,
    list_projects,
    open_project,
    refuse_unscaled_geometry,
    register_artifact,
    register_input,
    register_input_and_set_role,
    rescan_inputs,
    reuse_advisory,
    save_manifest,
    unscaled_geometry_records,
    update_project,
)
from .rpict import GLARE_QUALITY_PRESETS, RenderedFisheye, build_rpict_argv, render_fisheye
from .run import RadianceError, RadianceTimeoutError, Result, run, run_to_file
from .safe_io import FileTooLargeError, read_capped
from .scene import (
    ASE_BLACK_MODIFIER,
    ASE_BLACK_PRIMITIVE,
    AseScene,
    FlattenedScene,
    ReadScene,
    SceneAssembly,
    SceneAssemblyError,
    SceneFileNotFoundError,
    SceneInlineCommandError,
    StrippedScene,
    TRANSMITTING_PRIMITIVE_TYPES,
    assemble_scene,
    build_ase_scene,
    flatten_scene,
    glass_modifiers,
    non_polygon_surfaces,
    parse_polygons,
    read_scene,
    scene_without_modifiers,
    strip_primitives_by_modifier,
    unclaimed_transmitting_modifiers,
)
from .sensors import PointsFile, PointsFileEmptyError, stage_points, write_points
from .sky import sky_integer_token

__all__ = [
    "RadianceEnv", "RadianceNotFound", "discover",
    "Result", "RadianceError", "RadianceTimeoutError", "run", "run_to_file",
    # HDR header/resolution/statistics reader (Phase 8 Task 2).
    "ImageInfo", "ImageStatistics", "parse_resolution", "parse_view_line", "read_image",
    # `evalglare`'s argv, both output forms, and the notices (Phase 8 Task 3).
    "GlareValues", "EvalglareResult", "PLAIN_FIELDS", "DETAILED_FIELDS",
    "build_evalglare_argv", "parse_plain", "parse_detailed", "run_evalglare",
    # The restricted fisheye renderer `run_glare` needs (Phase 8 Task 5): the two DGP-table
    # presets (ruling R262), the argv builder, and `render_fisheye` itself.
    "GLARE_QUALITY_PRESETS", "RenderedFisheye", "build_rpict_argv", "render_fisheye",
    "Octree", "EmptyOctreeError", "build_octree", "bounding_cube",
    "OctreeSetOverflowError", "OctreeTooManyFilesError",
    # The two black-backed octrees the 5-phase direct terms need (Task 8, Phase 7a):
    # `Vd`/`Dd` (all-black) and `Cds` (all-black + suns + BSDF proxy).
    "build_black_octree", "build_suns_octree",
    # The R391 guard's public wrapper, over text no scene file holds -- a converter stage's output
    # (Phase 10 Task 5).
    "refuse_inline_commands_in_text",
    # The same guard over a list of scene files (R747). `build_octree` and the two black-backed
    # builders call it immediately before each spawn that reads the files;
    # `rad_mcp.point_in_time_tool._scan_scene_for_inline_commands` calls it earlier, above the
    # `dry_run` exit, as an advisory scan.
    "refuse_inline_commands_in_files",
    # `rad_engine.convert`: the obj2rad/robjutil/xform staged spawns for Phase 10's OBJ import
    # (Task 5) -- one spawn per stage, each guarded by `refuse_inline_commands_in_text` above.
    "ConvertedObj", "GeometryConversionError", "account", "convert_obj", "face_number",
    "obj2rad_argv", "rules_text", "scale_scene_text", "transform_points", "triangulate_obj", "xform_argv",
    # The per-import private directory every stage above writes into, and the refusal when its
    # parent chain leaves the roots (Phase 10 Task 9).
    "ScratchOutsideRootsError", "scratch_directory",
    # `rad_engine.ies`: rad_mcp's own packaged `source.cal` and the one-way `ies2rad` conversion +
    # compile check (Phase 11a Task 8). No `PREVIOUS_SOURCE_CAL_DIGESTS` here -- R503 keeps the
    # previous package digests on the `luminaire_support` record (Task 9), not a module constant.
    "SOURCE_CAL_BYTES", "SOURCE_CAL_DIGEST", "ConvertedIes", "IesConversionError",
    "convert_ies", "ies2rad_argv",
    # LM-83 sec 3.3's ASE octree (Task 9, Phase 7c): the registered scene plus the suns scene,
    # aperture and fixed shading kept -- not the blackened, aperture-stripped, proxy-bearing one
    # above. See `rad_engine.octree.build_ase_octree` for why each difference is required.
    "build_ase_octree",
    # Stderr classification (Task 2). Re-exported so callers reach one surface, not two modules.
    "EngineAdvisory", "Rule", "RULES", "Severity", "UNCLASSIFIED_CODE", "classify", "classify_line",
    # Scene assembly and `.rad` import -- octree + geometry inventory, R5's two-code split (Task 5).
    "SceneAssembly", "SceneAssemblyError", "SceneFileNotFoundError",
    "SceneInlineCommandError", "assemble_scene",
    "parse_polygons", "read_scene", "ReadScene",
    # The precondition for reasoning from the parsed polygon soup to the octree cube
    # (whole-branch review Critical 4): empty means the soup is the whole scene.
    "non_polygon_surfaces",
    "scene_without_modifiers", "strip_primitives_by_modifier", "StrippedScene",
    # The flattened scene with its includes exposed, and a scene's `glass` modifiers (Phase 10
    # Task 6) -- `geometry(action="add")` records a non-metre `.rad` import's includes as `sources`.
    "FlattenedScene", "flatten_scene", "glass_modifiers",
    # A whole-file read capped before it is taken into memory, and the error it raises over the
    # cap (Phase 10 Task 6) -- `rad_engine.safe_io`'s own guard, re-exported for `geometry`'s import
    # path.
    "FileTooLargeError", "read_capped",
    # The per-group ASE scene (Phase 7d-1 Task 8): every OTHER window group's aperture blacked, so
    # LM-83 sec 2.2.6's residual has a run per group to be read off.
    "ASE_BLACK_MODIFIER", "ASE_BLACK_PRIMITIVE", "AseScene", "build_ase_scene",
    # R147's checkable half: transmitting modifiers no window group claims, which break the
    # precondition LM-83 sec 2.2.6's subtraction rests on.
    "TRANSMITTING_PRIMITIVE_TYPES", "unclaimed_transmitting_modifiers",
    # Materials emission and the ordering guarantee (Task 6). `scene_files_with_materials` is
    # re-exported because `rad_mcp.geometry_tool` is its caller: it is the one place the `oconv`
    # argument list is built with every materials path ahead of every geometry path, and a
    # hand-built list is exactly what it exists to make impossible (Task 7b).
    "EmittedMaterial", "EmittedDefaults", "MaterialsFile", "MaterialsEmissionError",
    "emit_assignment", "emit_defaults", "write_materials", "scene_files_with_materials",
    # The `.pts` sensor file (Task 7b) -- `sensor_grid(action="create")`'s only artifact.
    "PointsFile", "PointsFileEmptyError", "stage_points", "write_points",
    # `gensky`/`gendaylit`'s integer-argument coercion and range check, made public by R765 (it was
    # `rad_engine.sky._date_token`): `rad_mcp.sky_site` checks a Perez sky's month and day with it
    # before reading the weather file's row, and R736 keeps `rad_mcp` off private engine names.
    "sky_integer_token",
    # Project layout and manifest IO (Task 3).
    "MANIFEST_NAME", "LAYOUT_DIRECTORIES", "ARTIFACT_DIRECTORY", "REQUIRED_EDGES",
    "NORTH_INPUT_ID",
    "ProjectPaths", "LoadedProject", "Description", "UpdateResult", "ProjectSummary",
    "EdgeRequirement",
    "ProjectError", "ProjectExistsError", "ProjectNotFoundError", "ProjectNameError",
    "ProjectNotEmptyError", "RunningJobError", "ManifestUnreadableError",
    "ProjectUnitsUnsetError", "ProvenanceError", "EdgesIncompleteError",
    "file_digest", "file_refs", "bytes_digest",
    "create_project", "open_project", "save_manifest", "describe", "evaluate_loaded", "update_project",
    "register_input", "register_input_and_set_role", "rescan_inputs", "drifted_inputs",
    "register_artifact", "check_edges",
    "is_reusable",
    "reuse_advisory",
    "list_projects", "delete_project", "archive_project",
    # Phase 10 Task 6: the pre-Phase-10 unscaled-geometry refusal, recorded in the same commit as
    # `InputRecord.sources`/`.server_written` above it lets a project check for.
    "GeometryUnitsNotAppliedError", "unscaled_geometry_records", "refuse_unscaled_geometry",
    # Job supervision, the state machine and the FIFO queue (Task 4). One store: the `job` tool and
    # the Tasks extension are two front doors onto `JobStore` and neither keeps state of its own
    # (docs/tool-surface.md#long-running-work-job-is-the-fallback-not-the-interface).
    "JobStore", "JobRecord", "JobState", "StepRecord", "Step", "ArtifactSpec",
    "CommittedArtifact", "AdvisoryRecord", "TERMINAL_STATES", "LEGAL_TRANSITIONS",
    "status_payload", "kill_group", "is_same_process",
    "Launcher", "RadianceLauncher", "SystemLauncher",
    "JobError", "JobNotFoundError", "JobRecordExpiredError",
    "new_job_id", "worker_cores",
]
