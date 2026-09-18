"""
User interface for the MascotCapsule add-on.

Only this module and :mod:`addon_output.importer` depend on ``bpy``.
"""

import os

import bpy
from bpy.props import StringProperty, BoolProperty, IntProperty, FloatProperty
from bpy_extras.io_utils import ImportHelper

from .importer import import_mascotcapsule


def _sibling(path, ext):
    base, _ = os.path.splitext(path)
    candidate = base + ext
    return candidate if os.path.isfile(candidate) else ''


class IMPORT_OT_mascotcapsule(bpy.types.Operator, ImportHelper):
    """Import a MascotCapsule Micro3D model (MBAC) and its animation (MTRA)"""

    bl_idname = 'import_scene.mascotcapsule'
    bl_label = 'MascotCapsule (.mbac / .mtra)'
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = '.mbac'
    filter_glob: StringProperty(
        default='*.mbac;*.mtra', options={'HIDDEN'},
        description='MascotCapsule model and animation files')

    mbac_path: StringProperty(
        name='MBAC model', subtype='FILE_PATH', default='',
        description='Model file; auto-detected when a .mtra is selected')
    mtra_path: StringProperty(
        name='MTRA animation', subtype='FILE_PATH', default='',
        description='Animation file; defaults to the sibling .mtra')
    texture_path: StringProperty(
        name='Texture (override)', subtype='FILE_PATH', default='',
        description='Optional override for the first texture; otherwise '
                    'texture.bmp / texture01.bmp / texture02.bmp / <model>.bmp '
                    'next to the .mbac are used automatically')

    import_animation: BoolProperty(name='Import animation', default=True)
    import_texture: BoolProperty(name='Import texture', default=True)
    convert_axis: BoolProperty(
        name='Convert Y-up to Z-up', default=True,
        description='Convert MascotCapsule coordinates to Blender Z-up')
    bake_every_frame: BoolProperty(
        name='Bake every frame', default=True,
        description='Insert a key on every frame (exact) instead of only at '
                    'authored keyframes (lighter)')
    fps: IntProperty(name='FPS', default=12, min=1, max=120)
    scale: FloatProperty(
        name='Scale', default=0.01, min=0.000001, soft_max=1.0,
        description='Uniform scale applied to mesh, armature and animation '
                    '(the converter output is ~100x too large for Blender)')

    def execute(self, context):
        path = self.filepath
        mbac_path = self.mbac_path
        mtra_path = self.mtra_path

        if path.lower().endswith('.mtra'):
            mtra_path = mtra_path or path
            mbac_path = mbac_path or _sibling(path, '.mbac')
        else:
            mbac_path = mbac_path or path
            mtra_path = mtra_path or _sibling(path, '.mtra')

        if not mbac_path or not os.path.isfile(mbac_path):
            self.report({'ERROR'}, 'MBAC model not found: %r' % mbac_path)
            return {'CANCELLED'}

        try:
            arm_obj, mesh_obj = import_mascotcapsule(
                context,
                mbac_path,
                mtra_path=mtra_path,
                texture_path=self.texture_path,
                import_animation=self.import_animation,
                import_texture=self.import_texture,
                fps=self.fps,
                convert_axis=self.convert_axis,
                bake_every_frame=self.bake_every_frame,
                scale=self.scale,
            )
        except Exception as exc:  # surface parser errors in the UI
            self.report({'ERROR'}, 'MascotCapsule import failed: %s' % exc)
            return {'CANCELLED'}

        self.report({'INFO'}, 'Imported %s (%d bones, %d materials)' %
                    (os.path.basename(mbac_path), len(arm_obj.data.bones),
                     len(mesh_obj.data.materials)))
        return {'FINISHED'}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_mascotcapsule.bl_idname,
                         text='MascotCapsule (.mbac / .mtra)')


classes = (IMPORT_OT_mascotcapsule,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)