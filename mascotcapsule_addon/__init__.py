"""
MascotCapsule MBAC / MTRA importer -- Blender add-on.

Turns a MascotCapsule Micro3D binary model (.mbac) plus its binary animation
(.mtra) into a normal Blender scene: a skinned mesh, an armature rebuilt from
the MBAC bone hierarchy, and a standard action with keyframed pose bones.

Layout:
    addon_output/
        __init__.py      add-on metadata + registration (this file)
        ui.py            operators / menu integration
        importer.py      Blender scene construction (mesh, armature, action)
        core/            bpy-free parsers and math
            mbac_parse.py
            mtra_parse.py
            transform.py
"""

bl_info = {
    'name': 'MascotCapsule MBAC/MTRA Importer',
    'author': 'bactra reverse-engineering project',
    'version': (0, 2, 0),
    'blender': (4, 0, 0),
    'location': 'File > Import > MascotCapsule (.mbac / .mtra)',
    'description': 'Import MascotCapsule Micro3D models (.mbac) with their '
                   'binary animation (.mtra), rebuilding bones and keyframes.',
    'warning': 'MTRA v4 (older) is not yet decoded; only version 5 animates.',
    'doc_url': '',
    'tracker_url': '',
    'category': 'Import-Export',
}

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


if __name__ == '__main__':
    register()
