"""
MascotCapsule MBAC / MTRA importer -- Blender extension.

Turns a MascotCapsule Micro3D binary model (.mbac) plus its binary animation
(.mtra) into a normal Blender scene: a skinned mesh, an armature rebuilt from
the MBAC bone hierarchy, and a standard action with keyframed pose bones.

This is an *extension* add-on: its metadata lives in ``blender_manifest.toml``
(next to this file) instead of the legacy ``bl_info`` dictionary.

Layout:
    mascotcapsule_addon/
        blender_manifest.toml   extension metadata (name, version, license...)
        __init__.py             registration (this file)
        ui.py                   operators / menu integration
        importer.py             Blender scene construction (mesh, armature, action)
        core/                   bpy-free parsers and math
            mbac_parse.py
            mtra_parse.py
            transform.py
"""

if 'core' in locals():
    import importlib
    importlib.reload(core.mbac_parse)
    importlib.reload(core.mtra_parse)
    importlib.reload(core.transform)
    importlib.reload(core)
    importlib.reload(importer)
    importlib.reload(ui)
else:
    from . import core
    from . import importer
    from . import ui


def register():
    ui.register()


def unregister():
    ui.unregister()
