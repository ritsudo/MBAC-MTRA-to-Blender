"""
Stand-alone tests for the bpy-free core of the MascotCapsule add-on.

Run with:  python -m unittest discover -s tests -v
(the tests skip any sample file that is not present)
"""

import os
import struct
import sys
import math
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, 'addon_output')
sys.path.insert(0, ADDON)

import core  # noqa: E402
from core.mbac_parse import MBAC, mat_to_unit, MBACError  # noqa: E402
from core.mtra_parse import MTRA, MTRAError  # noqa: E402
from core import transform  # noqa: E402

WORKSPACE = os.path.dirname(ROOT)
SAMPLES = {
    'aquar_mbac': os.path.join(WORKSPACE, 'testmodel-1-aquar', 'aquar.mbac'),
    'aquar_mtra': os.path.join(WORKSPACE, 'testmodel-1-aquar', 'aquar.mtra'),
    'forest_mbac': os.path.join(WORKSPACE, 'testmodel-4-forest', 'forest.mbac'),
    'forest_mtra': os.path.join(WORKSPACE, 'testmodel-4-forest', 'forest.mtra'),
    'works_mtra': os.path.join(WORKSPACE, 'samples_mtra', 'works.mtra'),
    'model1_mtra': os.path.join(WORKSPACE, 'samples_mtra', 'model1.mtra'),
    'model0_mtra': os.path.join(WORKSPACE, 'samples_mtra', 'model0.mtra'),
}


def have(key):
    return os.path.isfile(SAMPLES[key])


class TestMBAC(unittest.TestCase):
    @unittest.skipUnless(have('aquar_mbac'), 'sample missing')
    def test_aquar(self):
        m = MBAC.load(SAMPLES['aquar_mbac'])
        self.assertEqual(m.version, 5)
        self.assertEqual(m.vertexformat, 2)
        self.assertEqual(m.polygonformat, 3)
        self.assertEqual(len(m.vertices), 151)
        self.assertEqual(len(m.faces), 238)
        self.assertEqual(len(m.bones), 3)
        self.assertEqual([b.parent for b in m.bones], [-1, 0, -1])
        self.assertEqual(m.bones[0].start, 0)
        self.assertEqual(m.bones[0].end, 83)
        self.assertEqual(m.num_textures, 1)
        self.assertEqual(set(m.face_texture), {1})
        # trailer decodes to the HI CORP signature
        self.assertEqual(m.trailer[0][1], b'HI000000')
        self.assertEqual(m.trailer[1][1], b'HI000000')
        # skinned rest vertices are finite
        for v in m.world_vertices():
            self.assertTrue(all(math.isfinite(c) for c in v))

    @unittest.skipUnless(have('forest_mbac'), 'sample missing')
    def test_forest_two_textures(self):
        m = MBAC.load(SAMPLES['forest_mbac'])
        self.assertEqual(m.num_textures, 2)
        self.assertEqual(m.num_patterns, 1)
        # pattern 0: colored (0), texture1 = 1326 tris, texture2 = 78 tris
        self.assertEqual(m.patterns[0][1][2], 1326)
        self.assertEqual(m.patterns[0][2][2], 78)
        from collections import Counter
        counts = Counter(m.face_texture)
        self.assertEqual(counts[1], 1326)
        self.assertEqual(counts[2], 78)
        self.assertEqual(len(m.face_texture), len(m.faces))


class TestMTRA(unittest.TestCase):
    @unittest.skipUnless(have('aquar_mtra'), 'sample missing')
    def test_aquar(self):
        a = MTRA.load(SAMPLES['aquar_mtra'])
        self.assertEqual(a.version, 5)
        self.assertEqual(a.num_segments, 3)
        self.assertEqual(len(a.actions), 1)
        self.assertEqual(a.actions[0].num_keyframes, 8)
        self.assertEqual([s.type for s in a.actions[0].segments], [5, 5, 5])
        seg = a.actions[0].segments[0]
        self.assertEqual([e[0] for e in seg.rotate], [0, 2, 5, 6])
        # +Z unit vector at key 2 is (0, -1401, 3849)
        self.assertEqual(seg.rotate[1][1:], (0, -1401, 3849))

    @unittest.skipUnless(have('works_mtra'), 'sample missing')
    def test_works_type2(self):
        a = MTRA.load(SAMPLES['works_mtra'])
        self.assertEqual(a.actions[0].num_keyframes, 19)
        self.assertTrue(any(s.type == 2 for s in a.actions[0].segments))
        seg = a.actions[0].segments[0]
        self.assertTrue(seg.translate and seg.scale and seg.rotate and seg.roll)

    @unittest.skipUnless(have('model1_mtra'), 'sample missing')
    def test_model1_compressed_types(self):
        a = MTRA.load(SAMPLES['model1_mtra'])
        self.assertEqual(a.num_segments, 37)
        self.assertEqual(a.actions[0].num_keyframes, 1050)
        types = {s.type for s in a.actions[0].segments}
        self.assertTrue({1, 4, 5, 6}.issubset(types))
        # type 6 = translate + rotate + roll, type 4 = rotate + roll
        for s in a.actions[0].segments:
            if s.type == 6:
                self.assertTrue(s.translate and s.rotate and s.roll and not s.scale)
            if s.type == 4:
                self.assertTrue(s.rotate and s.roll and not s.translate and not s.scale)

    @unittest.skipUnless(have('model0_mtra'), 'sample missing')
    def test_v4_rejected(self):
        with self.assertRaises(MTRAError):
            MTRA.load(SAMPLES['model0_mtra'])

    def test_type3_constants(self):
        # Hand-built v5 MTRA with one type-3 segment:
        #   translate (const), rotate (key list), roll (const)
        body = bytearray()
        body += b'MT'
        body += struct.pack('<HH', 5, 1)          # version=5, num_actions=1
        body += struct.pack('<H', 1)              # num_segments=1
        body += b'\x00' * 16                      # transTypeCounts / reserved
        body += struct.pack('<I', 0)              # dataSize
        body += struct.pack('<H', 1)              # keyframes
        body += struct.pack('<B', 3)              # segment type 3
        body += struct.pack('<hhh', 10, -20, 30)  # constant translate
        body += struct.pack('<H', 1)              # rotate: 1 key
        body += struct.pack('<Hhhh', 0, 0, 0, 4096)
        body += struct.pack('<h', 1024)           # constant roll (90 deg)
        body += struct.pack('<H', 0)              # aux count
        body += b'\x00' * 20                      # trailer

        a = MTRA.frombytes(bytes(body))
        seg = a.actions[0].segments[0]
        self.assertEqual(seg.type, 3)
        self.assertEqual(seg.translate[0][1:], (10, -20, 30))
        self.assertEqual(seg.rotate[0][1:], (0, 0, 4096))
        self.assertEqual(seg.roll[0][1], 1024)


class TestTransform(unittest.TestCase):
    @unittest.skipUnless(have('aquar_mbac') and have('aquar_mtra'), 'samples missing')
    def test_frame0_matches_rest(self):
        mbac = MBAC.load(SAMPLES['aquar_mbac'])
        mtra = MTRA.load(SAMPLES['aquar_mtra'])
        mats = transform.animated_world_matrices(mbac, mtra, 0)
        for bone in mbac.bones:
            rest = mat_to_unit(bone.world)
            got = mats[bone.index]
            for r, c in zip(rest, got):
                self.assertAlmostEqual(r, c, places=4)

    @unittest.skipUnless(have('aquar_mbac') and have('aquar_mtra'), 'samples missing')
    def test_animation_changes(self):
        mbac = MBAC.load(SAMPLES['aquar_mbac'])
        mtra = MTRA.load(SAMPLES['aquar_mtra'])
        m0 = transform.animated_world_matrices(mbac, mtra, 0)
        m2 = transform.animated_world_matrices(mbac, mtra, 2)
        # bone 0 rotates about X at frame 2, so its matrix must differ
        self.assertNotAlmostEqual(m0[0][6], m2[0][6], places=3)

    def test_axis_conversion(self):
        rot = transform.yup_to_zup_matrix()
        x, y, z = transform.transform_point((1.0, 2.0, 3.0), rot)
        self.assertAlmostEqual(x, 1.0)
        self.assertAlmostEqual(y, -3.0)
        self.assertAlmostEqual(z, 2.0)

    def test_rot_from_z(self):
        # rotating +Z to (0, -sin20, cos20) must be a rotation about X
        R = transform.rot_from_z((0.0, -1401.0, 3849.0))
        self.assertAlmostEqual(R[4], math.cos(math.radians(20)), places=3)
        self.assertAlmostEqual(R[5], -math.sin(math.radians(20)), places=3)
        self.assertAlmostEqual(R[8], math.cos(math.radians(20)), places=3)

    def test_roll_units_are_turns(self):
        # Reference runtime: Util3D.sin(i) == sin(i * pi / 2048), so 4096 raw
        # is a full turn (360 deg), 1024 is 90 deg, 2048 is 180 deg.
        self.assertAlmostEqual(transform.roll_to_degrees(1024), 90.0)
        self.assertAlmostEqual(transform.roll_to_degrees(2048), 180.0)
        self.assertAlmostEqual(transform.roll_to_degrees(4096), 360.0)
        self.assertAlmostEqual(transform.roll_to_degrees(-1024), -90.0)

    def test_segment_delta_roll_90(self):
        # A type-4 segment with identity rotate and roll = 1024 (90 deg) must
        # rotate the bone frame 90 degrees about its Z axis.
        from core.mtra_parse import Segment, SEG_ROTATE_ROLL
        seg = Segment(0, SEG_ROTATE_ROLL)
        seg.rotate = [(0, 0, 0, 4096)]
        seg.roll = [(0, 1024)]
        d = transform.segment_delta(seg, 0)
        # column 0 of the rotation goes from +X to +Y
        self.assertAlmostEqual(d[0], 0.0, places=4)
        self.assertAlmostEqual(d[1], -1.0, places=4)
        self.assertAlmostEqual(d[4], 1.0, places=4)
        self.assertAlmostEqual(d[5], 0.0, places=4)

    def test_scale_translation(self):
        m = [1.0, 0, 0, 10.0, 0, 1.0, 0, 20.0, 0, 0, 1.0, 30.0]
        s = transform.scale_translation(m, 0.01)
        self.assertAlmostEqual(s[3], 0.1)
        self.assertAlmostEqual(s[7], 0.2)
        self.assertAlmostEqual(s[11], 0.3)
        self.assertAlmostEqual(s[0], 1.0)


if __name__ == '__main__':
    unittest.main()
