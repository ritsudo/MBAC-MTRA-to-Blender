"""
Blender scene construction from parsed MBAC + MTRA data.

This is the only module (besides ui.py) that touches ``bpy``.  All parsing and
matrix math lives in :mod:`addon_output.core`.
"""

import math
import os

import bpy
from mathutils import Matrix, Vector

from .core.mbac_parse import MBAC, mat_to_unit
from .core.mtra_parse import MTRA
from .core import transform


IMAGE_EXTS = ('.bmp', '.BMP', '.png', '.PNG', '.gif', '.GIF')


# ---------------------------------------------------------------------------
# public entry point
# ---------------------------------------------------------------------------

def import_mascotcapsule(context,
                         mbac_path,
                         mtra_path=None,
                         texture_path=None,
                         import_animation=True,
                         import_texture=True,
                         fps=12,
                         convert_axis=True,
                         bake_every_frame=True,
                         scale=0.01,
                         collection=None):
    """Import an MBAC model (optionally with MTRA animation) into the scene.

    ``scale`` is applied uniformly to the mesh, the armature and the animation
    (the original converter emits values ~100x too large for Blender units).

    Returns (armature_object, mesh_object).
    """
    mbac = MBAC.load(mbac_path)
    mtra = None
    if import_animation and mtra_path and os.path.isfile(mtra_path):
        mtra = MTRA.load(mtra_path)

    collection = collection or context.collection
    s = float(scale) if scale else 1.0

    rot4 = transform.yup_to_zup_matrix() if convert_axis else list(Matrix.Identity(4))

    # rest-pose bone matrices (unit scale, optionally axis-converted/scaled)
    rest_world = []
    for bone in mbac.bones:
        w = mat_to_unit(bone.world)
        if convert_axis:
            w = transform.conjugate_3x4(w, rot4)
        rest_world.append(transform.scale_translation(w, s))

    # mesh vertices in rest pose (axis-converted, scaled)
    vertices = []
    for v in mbac.world_vertices():
        if convert_axis:
            v = transform.transform_point(v, rot4)
        vertices.append((v[0] * s, v[1] * s, v[2] * s))

    texture_paths = None
    if import_texture:
        texture_paths = _find_textures(mbac_path, mbac.num_textures, texture_path)

    arm_obj = _build_armature(context, collection, mbac, rest_world, os.path.basename(mbac_path))
    mesh_obj = _build_mesh(context, collection, mbac, vertices, arm_obj, texture_paths)
    mesh_obj.parent = arm_obj

    if mtra is not None and mtra.actions:
        _build_animation(context, arm_obj, mbac, mtra, rest_world, rot4,
                         fps=fps, bake_every_frame=bake_every_frame, scale=s)

    return arm_obj, mesh_obj


# ---------------------------------------------------------------------------
# textures
# ---------------------------------------------------------------------------

def _find_textures(mbac_path, num_textures, explicit=None):
    """Resolve one image path per MBAC texture ('' when nothing is found).

    Candidates, in order, for texture 1: the operator's explicit path, a
    sibling ``<model>.*``, ``texture.*``, ``texture01.*``, ``texture1.*``,
    ``fx.*``.  For texture N>1: ``textureNN.*`` / ``textureN.*`` /
    ``texture_N.*``.  A missing file yields an empty entry so the caller can
    still create a material stub.
    """
    num_textures = max(1, int(num_textures or 1))
    folder = os.path.dirname(mbac_path)
    base = os.path.splitext(os.path.basename(mbac_path))[0]

    paths = []
    for tex in range(1, num_textures + 1):
        found = ''
        if explicit and tex == 1 and os.path.isfile(explicit):
            found = explicit
        if not found:
            if tex == 1:
                if num_textures > 1:
                    stems = (base, 'texture01', 'texture1', 'texture', 'fx')
                else:
                    stems = (base, 'texture', 'texture01', 'texture1', 'fx')
            else:
                stems = ('texture%02d' % tex, 'texture%d' % tex, 'texture_%d' % tex)
            for stem in stems:
                for ext in IMAGE_EXTS:
                    candidate = os.path.join(folder, stem + ext)
                    if os.path.isfile(candidate):
                        found = candidate
                        break
                if found:
                    break
        paths.append(found)
    return paths


# ---------------------------------------------------------------------------
# armature
# ---------------------------------------------------------------------------

def _build_armature(context, collection, mbac, rest_world, name):
    arm_data = bpy.data.armatures.new(name + '_armature')
    arm_obj = bpy.data.objects.new(name + '_armature', arm_data)
    collection.objects.link(arm_obj)

    view_layer = context.view_layer
    view_layer.objects.active = arm_obj
    arm_obj.select_set(True)

    bpy.ops.object.mode_set(mode='EDIT')
    try:
        edit_bones = []
        for bone in mbac.bones:
            w = rest_world[bone.index]
            head = Vector((w[3], w[7], w[11]))
            y_axis = Vector((w[1], w[5], w[9]))
            z_axis = Vector((w[2], w[6], w[10]))
            if y_axis.length < 1e-6:
                y_axis = Vector((0.0, 1.0, 0.0))
            y_axis.normalize()
            if z_axis.length < 1e-6:
                z_axis = Vector((0.0, 0.0, 1.0))
            z_axis.normalize()

            children = [c for c in mbac.bones if c.parent == bone.index]
            if children:
                child_w = rest_world[children[0].index]
                length = (Vector((child_w[3], child_w[7], child_w[11])) - head).length
            else:
                length = 1.0

            eb = arm_data.edit_bones.new('bone_%d' % bone.index)
            eb.head = head
            eb.tail = head + y_axis * max(length, 0.001)
            eb.align_roll(z_axis)
            edit_bones.append(eb)

        for bone, eb in zip(mbac.bones, edit_bones):
            if bone.parent >= 0:
                eb.parent = edit_bones[bone.parent]
                eb.use_connect = False
    finally:
        bpy.ops.object.mode_set(mode='OBJECT')

    arm_obj.select_set(False)
    return arm_obj


# ---------------------------------------------------------------------------
# mesh
# ---------------------------------------------------------------------------

def _build_mesh(context, collection, mbac, vertices, arm_obj, texture_paths):
    name = arm_obj.name.replace('_armature', '') + '_mesh'
    mesh = bpy.data.meshes.new(name)

    polygons = []
    uvs_per_poly = []
    for face in mbac.faces:
        if len(face) == 3:
            polygons.append((face[0], face[1], face[2]))
            uvs_per_poly.append(None)
        elif len(face) == 4:
            a, b, c, d = face
            polygons.append((a, b, d, c))
            uvs_per_poly.append(None)
        elif len(face) == 9:
            a, b, c, u1, v1, u2, v2, u3, v3 = face
            polygons.append((a, b, c))
            uvs_per_poly.append(((u1, v1), (u2, v2), (u3, v3)))
        else:
            a, b, c, d, u1, v1, u2, v2, u3, v3, u4, v4 = face
            polygons.append((a, b, d, c))
            uvs_per_poly.append(((u1, v1), (u2, v2), (u4, v4), (u3, v3)))

    mesh.from_pydata([Vector(v) for v in vertices], [], polygons)
    mesh.update()

    face_texture = list(mbac.face_texture) or [1] * len(polygons)
    num_textures = max(1, mbac.num_textures)
    make_materials = texture_paths is not None

    # --- materials (one per texture, plus a plain one for colored faces) ----
    has_colored = any(t == 0 for t in face_texture)
    slot_of = {}
    slot = 0
    if make_materials and has_colored:
        mesh.materials.append(_make_base_material(name + '_colored'))
        slot_of[0] = 0
        slot = 1
    if make_materials:
        for tex in range(1, num_textures + 1):
            path = texture_paths[tex - 1] if tex - 1 < len(texture_paths) else ''
            mesh.materials.append(_make_texture_material(
                '%s_texture%02d' % (name, tex), path, _uv_name(tex)))
            slot_of[tex] = slot
            slot += 1

    def slot_for(tex):
        if tex in slot_of:
            return slot_of[tex]
        return slot_of.get(1, 0)

    # --- UV layers (one per texture) ---------------------------------------
    uv_layers = {}
    for tex in range(1, num_textures + 1):
        uv_layers[tex] = mesh.uv_layers.new(name=_uv_name(tex))
    if 1 in uv_layers:
        uv_layers[1].active_render = True

    sizes = {}
    for tex in range(1, num_textures + 1):
        path = ''
        if texture_paths and tex - 1 < len(texture_paths):
            path = texture_paths[tex - 1]
        w, h = _texture_size(path)
        if w <= 0:
            w = h = _infer_uv_extent(
                [uv for uv, ft in zip(uvs_per_poly, face_texture) if ft == tex])
        sizes[tex] = (w, h)

    for poly, uvs, tex in zip(mesh.polygons, uvs_per_poly, face_texture):
        poly.material_index = slot_for(tex)
        if not uvs:
            continue
        tex = tex if tex in uv_layers else 1
        w, h = sizes.get(tex, (0, 0))
        if w <= 0 or h <= 0:
            continue
        layer = uv_layers[tex]
        for loop_index, (u, v) in zip(poly.loop_indices, uvs):
            layer.data[loop_index].uv = (u / float(w), 1.0 - v / float(h))

    mesh_obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(mesh_obj)

    # rigid skin weights (one bone per vertex)
    groups = {b.index: mesh_obj.vertex_groups.new(name='bone_%d' % b.index)
              for b in mbac.bones}
    for i in range(len(vertices)):
        groups[mbac.vertex_bone(i)].add([i], 1.0, 'REPLACE')

    mod = mesh_obj.modifiers.new('Armature', 'ARMATURE')
    mod.object = arm_obj

    return mesh_obj


def _texture_size(texture_path):
    if texture_path and os.path.isfile(texture_path):
        try:
            img = bpy.data.images.load(texture_path, check_existing=True)
            if img.size[0] > 0:
                return int(img.size[0]), int(img.size[1])
        except Exception:
            pass
    return 0, 0


def _infer_uv_extent(uvs_per_poly):
    top = 1
    for uvs in uvs_per_poly:
        if not uvs:
            continue
        for (u, v) in uvs:
            top = max(top, u + 1, v + 1)
    # round up to a power of two (texture sizes in this family always are)
    p = 1
    while p < top:
        p <<= 1
    return p


def _uv_name(tex):
    return 'UVMap' if tex == 1 else 'UVMap_texture%02d' % tex


def _make_texture_material(name, texture_path, uv_map_name):
    """A material with an Image Texture node (Closest / pixelated).

    The texture is fed by a UV Map node bound to ``uv_map_name`` so that each
    material samples its own texture's coordinates.  When the image file is
    missing the node is left without an image so the texture can be attached
    later in the Shading editor.
    """
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True

    image = None
    if texture_path and os.path.isfile(texture_path):
        image = bpy.data.images.load(texture_path, check_existing=True)

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    uv_node = nodes.new('ShaderNodeUVMap')
    uv_node.uv_map = uv_map_name
    tex = nodes.new('ShaderNodeTexImage')
    tex.image = image
    tex.interpolation = 'Closest'
    tex.label = os.path.basename(texture_path) if texture_path else 'missing texture'
    links.new(tex.inputs['Vector'], uv_node.outputs['UV'])
    bsdf = nodes.get('Principled BSDF')
    if bsdf is not None:
        links.new(bsdf.inputs['Base Color'], tex.outputs['Color'])
    return mat


def _make_base_material(name):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.diffuse_color = (0.8, 0.8, 0.8, 1.0)
    return mat


# ---------------------------------------------------------------------------
# animation
# ---------------------------------------------------------------------------

def _build_animation(context, arm_obj, mbac, mtra, rest_world, rot4, fps,
                     bake_every_frame, scale=1.0):
    action_data = mtra.actions[0]
    nframes = action_data.num_keyframes

    scene = context.scene
    scene.render.fps = fps
    scene.frame_start = 0
    scene.frame_end = max(nframes - 1, 0)

    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    action = bpy.data.actions.new(arm_obj.name + '_action')
    arm_obj.animation_data.action = action

    for bone in arm_obj.pose.bones:
        bone.rotation_mode = 'QUATERNION'

    rest_local = {b.index: arm_obj.data.bones['bone_%d' % b.index].matrix_local
                  for b in mbac.bones}
    order = transform.bone_order(mbac)
    animated = {seg.index for seg in action_data.segments if seg.is_animated}
    key_frames = None if bake_every_frame else _frame_union(action_data)

    for frame in range(nframes):
        # when not baking, only key at the union of authored key times
        if key_frames is not None and frame not in key_frames:
            continue

        mats = transform.animated_world_matrices(mbac, mtra, frame)
        if rot4 is not None:
            mats = [transform.conjugate_3x4(m, rot4) for m in mats]
        if scale != 1.0:
            mats = [transform.scale_translation(m, scale) for m in mats]

        targets = {}
        for i in order:
            bone = mbac.bones[i]
            target = _matrix_from_3x4(mats[i])
            targets[i] = target
            if i not in animated:
                continue
            rest_i = rest_local[i]
            if bone.parent >= 0:
                parent_rest = rest_local[bone.parent]
                parent_target = targets[bone.parent]
            else:
                parent_rest = Matrix.Identity(4)
                parent_target = Matrix.Identity(4)
            basis = (rest_i.inverted() @ parent_rest @
                     parent_target.inverted() @ target)
            pose_bone = arm_obj.pose.bones['bone_%d' % i]
            pose_bone.matrix_basis = basis
            pose_bone.keyframe_insert('location', frame=frame)
            pose_bone.keyframe_insert('rotation_quaternion', frame=frame)
            pose_bone.keyframe_insert('scale', frame=frame)

    for fcurve in action.fcurves:
        for keyframe in fcurve.keyframe_points:
            keyframe.interpolation = 'LINEAR'


def _frame_union(action_data):
    times = set()
    for seg in action_data.segments:
        times.update(seg.key_times())
    times.add(0)
    times.add(action_data.num_keyframes - 1)
    return times


def _matrix_from_3x4(m):
    return Matrix(((m[0], m[1], m[2], m[3]),
                   (m[4], m[5], m[6], m[7]),
                   (m[8], m[9], m[10], m[11]),
                   (0.0, 0.0, 0.0, 1.0)))
