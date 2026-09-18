"""
Transform / animation composition for MBAC + MTRA.

Pure Python (lists of floats), no Blender dependency.

TRA4 semantics (from the official specification):
    each bone animation is a difference from the BAC6 bone frame, composed as
        Delta = Translate . Rotate(+Z -> v) . Roll(about Z) . Scale
    and applied in the bone's local frame:
        L_bone(t) = L_bone_rest . Delta(t)
        W_bone(t) = W_parent(t) . L_bone(t)

All matrices here are row-major 3x4 (12 floats).  Rest bone matrices come from
MBAC in 4.12 fixed point; this module converts them to unit scale and works in
unit scale throughout (so they can be handed straight to Blender).

Angle units (verified against the MascotCapsule V3 reference runtime,
ActionTable.rotate/roll + Util3D.sin/cos):
    rotate  : 4.12 fixed point *unit direction vector* (the new bone +Z axis)
    roll    : 4.12 fixed point *turns*, i.e. 4096 == 360 deg == 2*pi rad
              (Util3D.sin(i) == sin(i * pi / 2048); 1024 == 90 deg)
    scale   : 4.12 fixed point, 4096 == 100%
    translate: raw integer model units
"""

import math

from .mbac_parse import mat_to_unit

UNIT_IDENTITY = [1.0, 0.0, 0.0, 0.0,
                 0.0, 1.0, 0.0, 0.0,
                 0.0, 0.0, 1.0, 0.0]

FIX12 = 4096.0


# ---------------------------------------------------------------------------
# unit-scale 3x4 helpers
# ---------------------------------------------------------------------------

def mat_mul(a, b):
    """Multiply two unit-scale row-major 3x4 matrices: result = a . b."""
    out = [0.0] * 12
    for r in range(3):
        for c in range(3):
            out[r * 4 + c] = (a[r * 4 + 0] * b[0 * 4 + c] +
                              a[r * 4 + 1] * b[1 * 4 + c] +
                              a[r * 4 + 2] * b[2 * 4 + c])
        out[r * 4 + 3] = (a[r * 4 + 0] * b[0 * 4 + 3] +
                          a[r * 4 + 1] * b[1 * 4 + 3] +
                          a[r * 4 + 2] * b[2 * 4 + 3] + a[r * 4 + 3])
    return out


def mat_apply(m, v):
    x, y, z = v
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def mat3_mul(a, b):
    return [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
            for r in range(3) for c in range(3)]


def vec_normalize(v):
    length = math.sqrt(sum(c * c for c in v))
    if length < 1e-9:
        return (0.0, 0.0, 1.0)
    return (v[0] / length, v[1] / length, v[2] / length)


def rot_from_z(v):
    """Shortest-arc rotation (3x3) taking +Z to the normalized vector v."""
    vx, vy, vz = vec_normalize(v)
    # cross((0,0,1), v) = (-vy, vx, 0)
    cx, cy, cz = (-vy, vx, 0.0)
    s = math.sqrt(cx * cx + cy * cy + cz * cz)
    c = vz
    if s < 1e-9:
        if c > 0:
            return [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        return [1.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, -1.0]  # 180 deg about X
    kx, ky, kz = cx / s, cy / s, cz / s
    K = [0.0, -kz, ky, kz, 0.0, -kx, -ky, kx, 0.0]
    R = [0.0] * 9
    for r in range(3):
        for col in range(3):
            R[r * 3 + col] = (c * (1.0 if r == col else 0.0) +
                              s * K[r * 3 + col] +
                              (1.0 - c) * (kx if r == 0 else ky if r == 1 else kz) *
                              (kx if col == 0 else ky if col == 1 else kz))
    return R


def rot_z(angle_deg):
    a = math.radians(angle_deg)
    ca, sa = math.cos(a), math.sin(a)
    return [ca, -sa, 0.0, sa, ca, 0.0, 0.0, 0.0, 1.0]


def roll_to_degrees(raw):
    """Convert an MTRA roll value (4.12 fixed point turns) to degrees.

    4096 raw == one full turn (360 deg), so a raw value of 1024 is 90 deg.
    """
    return raw * (360.0 / FIX12)


def scale_translation(m, factor):
    """Uniformly scale the translation part of a 3x4 matrix (rotation kept)."""
    if factor == 1.0:
        return list(m)
    return [m[0], m[1], m[2], m[3] * factor,
            m[4], m[5], m[6], m[7] * factor,
            m[8], m[9], m[10], m[11] * factor]


# ---------------------------------------------------------------------------
# MTRA -> delta matrix
# ---------------------------------------------------------------------------

def segment_delta(seg, frame):
    """Return a unit-scale 3x4 bone-local delta for a Segment at a frame.

    Returns None for identity / matrix segments (caller keeps the rest frame).
    """
    if seg.type in (0, 1):  # matrix / identity
        return None

    tr = seg.translate and _sample(seg.translate, frame) or (0, 0, 0)
    sc = seg.scale and _sample(seg.scale, frame) or (FIX12, FIX12, FIX12)
    ro = seg.rotate and _sample(seg.rotate, frame) or (0, 0, FIX12)
    rl = seg.roll and _sample(seg.roll, frame) or (0,)

    R = mat3_mul(rot_from_z(ro), rot_z(roll_to_degrees(rl[0])))
    S = [sc[0] / FIX12, 0.0, 0.0,
         0.0, sc[1] / FIX12, 0.0,
         0.0, 0.0, sc[2] / FIX12]
    RS = mat3_mul(R, S)
    return [RS[0], RS[1], RS[2], tr[0],
            RS[3], RS[4], RS[5], tr[1],
            RS[6], RS[7], RS[8], tr[2]]


def _sample(entries, frame):
    if frame <= entries[0][0]:
        return entries[0][1:]
    if frame >= entries[-1][0]:
        return entries[-1][1:]
    for i in range(1, len(entries)):
        if entries[i][0] >= frame:
            f0, f1 = entries[i - 1][0], entries[i][0]
            t = (frame - f0) / float(f1 - f0) if f1 != f0 else 0.0
            a = entries[i - 1][1:]
            b = entries[i][1:]
            return tuple(a[j] + (b[j] - a[j]) * t for j in range(len(a)))
    return entries[-1][1:]


# ---------------------------------------------------------------------------
# whole-skeleton animation
# ---------------------------------------------------------------------------

def animated_world_matrices(mbac, mtra, frame, action_index=0):
    """Unit-scale world matrices for every bone at a given frame."""
    result = [None] * len(mbac.bones)
    action = mtra.actions[action_index]
    for bone in mbac.bones:
        i = bone.index
        parent_world = result[bone.parent] if bone.parent >= 0 else UNIT_IDENTITY
        local = mat_to_unit(bone.local)
        delta = segment_delta(action.segments[i], frame)
        if delta is not None:
            local = mat_mul(local, delta)
        result[i] = mat_mul(parent_world, local)
    return result


def bone_order(mbac):
    """Bone indices sorted so parents always precede children."""
    order = []
    remaining = list(mbac.bones)
    while remaining:
        progressed = False
        for bone in list(remaining):
            if bone.parent < 0 or bone.parent in order:
                order.append(bone.index)
                remaining.remove(bone)
                progressed = True
        if not progressed:  # malformed hierarchy: keep going anyway
            order.extend(b.index for b in remaining)
            break
    return order


# ---------------------------------------------------------------------------
# axis conversion (M3D Y-up -> Blender Z-up), as a 4x4 rotation
# ---------------------------------------------------------------------------

def yup_to_zup_matrix():
    """Return a 4x4 (list of 16, row-major) +90deg rotation about X.

    Maps M3D (x, y, z) -> (x, -z, y), i.e. Y-up becomes Z-up.
    """
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 0.0, -1.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mat4_from_3x4(m):
    return [m[0], m[1], m[2], m[3],
            m[4], m[5], m[6], m[7],
            m[8], m[9], m[10], m[11],
            0.0, 0.0, 0.0, 1.0]


def mat3x4_from_4x4(m):
    return [m[0], m[1], m[2], m[3],
            m[4], m[5], m[6], m[7],
            m[8], m[9], m[10], m[11]]


def mat4_mul(a, b):
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
    return out


def conjugate_3x4(m, rot4):
    """Return rot4 . m . rot4^-1 for a rigid rotation rot4 (3x4 in, 3x4 out)."""
    rot4_t = [rot4[0], rot4[4], rot4[8], 0.0,
              rot4[1], rot4[5], rot4[9], 0.0,
              rot4[2], rot4[6], rot4[10], 0.0,
              0.0, 0.0, 0.0, 1.0]
    return mat3x4_from_4x4(mat4_mul(rot4, mat4_mul(mat4_from_3x4(m), rot4_t)))


def transform_point(v, rot4):
    x, y, z = v
    return (rot4[0] * x + rot4[1] * y + rot4[2] * z,
            rot4[4] * x + rot4[5] * y + rot4[6] * z,
            rot4[8] * x + rot4[9] * y + rot4[10] * z)