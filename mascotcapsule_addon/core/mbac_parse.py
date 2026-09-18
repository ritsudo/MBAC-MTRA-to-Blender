"""
MBAC parser (MascotCapsule Micro3D binary model, format version 5).

Pure Python, no Blender dependency, so it can be unit-tested and reused.

Layout (little-endian), see REVERSE_ENGINEERING_NOTES.txt for details:

    char   magic[2]            "MB"
    u16    formatversion        5
    u8     vertexformat         2
    u8     normalformat         0|2
    u8     polygonformat        3
    u8     boneformat           1
    u16    num_vertices
    u16    num_polyT3
    u16    num_polyT4
    u16    num_bones
    # when polygonformat >= 3:
    u16    num_polyF3
    u16    num_polyF4
    u16    matcnt
    u16    unk21
    u16    num_color
    repeat(unk21) { u16; u16; repeat(matcnt) { u16; u16 } }
    <vertex bitstream>
    <normal bitstream>
    <polygon bitstream>
    repeat(num_bones) { u16 seg_vertices; s16 parent; s16 matrix[3][4] }
    <20-byte obfuscation trailer>
"""

import struct
import math

MAGIC = 0x424D  # 'MB'

VERTEXFORMAT_PACKED = 2
NORMALFORMAT_NONE = 0
NORMALFORMAT_PACKED = 2
POLYGONFORMAT_V3 = 3
BONEFORMAT_1 = 1

MAGNITUDE_BITS = (8, 10, 13, 16)
NORMAL_DIRECTIONS = (
    (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0),
    (-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), (0.0, 0.0, -1.0),
)

IDENTITY_MAT12 = [4096, 0, 0, 0,
                  0, 4096, 0, 0,
                  0, 0, 4096, 0]


class MBACError(Exception):
    pass


class Unpacker:
    """LSB-first bit reader used by the M3D runtime."""

    def __init__(self, data, offset=0):
        self.data = data
        self.pos = offset
        self.havebits = 0
        self.buf = 0

    def u(self, nbits):
        while nbits > self.havebits:
            if self.pos >= len(self.data):
                raise MBACError('unexpected end of vertex bitstream')
            self.buf |= self.data[self.pos] << self.havebits
            self.pos += 1
            self.havebits += 8
        value = self.buf & ((1 << nbits) - 1)
        self.havebits -= nbits
        self.buf >>= nbits
        return value

    def s(self, nbits):
        value = self.u(nbits)
        if value & (1 << (nbits - 1)):
            value -= 1 << nbits
        return value


# ---------------------------------------------------------------------------
# matrix helpers (4.12 fixed point row-major 3x4)
# ---------------------------------------------------------------------------

def mat_mul_412(a, b):
    out = [0.0] * 12
    for r in range(3):
        for c in range(3):
            out[r * 4 + c] = (a[r * 4 + 0] * b[0 * 4 + c] +
                              a[r * 4 + 1] * b[1 * 4 + c] +
                              a[r * 4 + 2] * b[2 * 4 + c]) / 4096.0
        out[r * 4 + 3] = (a[r * 4 + 0] * b[0 * 4 + 3] +
                          a[r * 4 + 1] * b[1 * 4 + 3] +
                          a[r * 4 + 2] * b[2 * 4 + 3]) / 4096.0 + a[r * 4 + 3]
    return out


def mat_apply_412(m, v):
    x, y, z = v
    return ((m[0] * x + m[1] * y + m[2] * z) / 4096.0 + m[3],
            (m[4] * x + m[5] * y + m[6] * z) / 4096.0 + m[7],
            (m[8] * x + m[9] * y + m[10] * z) / 4096.0 + m[11])


def mat_to_unit(m):
    """4.12 3x4 ints -> unit-scale float 3x4 (rotation divided by 4096)."""
    return [m[0] / 4096.0, m[1] / 4096.0, m[2] / 4096.0, float(m[3]),
            m[4] / 4096.0, m[5] / 4096.0, m[6] / 4096.0, float(m[7]),
            m[8] / 4096.0, m[9] / 4096.0, m[10] / 4096.0, float(m[11])]


# ---------------------------------------------------------------------------
# model
# ---------------------------------------------------------------------------

class Bone:
    __slots__ = ('index', 'parent', 'vertices', 'local', 'world', 'start', 'end')

    def __init__(self, index, parent, vertices, local, world, start, end):
        self.index = index
        self.parent = parent
        self.vertices = vertices
        self.local = local       # 4.12 ints, relative to parent
        self.world = world       # 4.12 floats, rest world transform
        self.start = start
        self.end = end

    @property
    def children(self):
        return []


class MBAC:
    def __init__(self):
        self.version = 0
        self.vertexformat = 0
        self.normalformat = 0
        self.polygonformat = 0
        self.boneformat = 0
        self.vertices = []        # bone-local integer positions
        self.normals = []         # floats
        self.faces = []           # tuples (a,b,c) / (a,b,c,d) / with uvs
        self.face_texture = []    # parallel to faces: 0=colored, 1..N=texture idx
        self.bones = []           # list[Bone]
        self.colors = []          # RGB from vertex-color faces
        self.materials = []
        self.texture_sizes = []
        self.num_textures = 0
        self.num_patterns = 0
        # patterns[p] = [ (t3off, t4off, t3cnt, t4cnt) ] indexed by
        # 0 = colored polygons, 1..num_textures = texture groups
        self.patterns = []
        self.trailer = None       # [(key_bytes, plaintext_bytes), ...]
        self._world_vertices = None

    # -- loading -----------------------------------------------------------
    @classmethod
    def load(cls, path):
        with open(path, 'rb') as f:
            return cls.frombytes(f.read())

    @classmethod
    def frombytes(cls, data):
        self = cls()
        self._parse(data)
        return self

    def _parse(self, data):
        if len(data) < 16:
            raise MBACError('file too short')
        magic, version = struct.unpack_from('<HH', data, 0)
        if magic != MAGIC:
            raise MBACError('not an MBAC file (magic %04X)' % magic)
        if version < 4:
            raise MBACError('unsupported MBAC version %d' % version)
        self.version = version

        o = 4
        (self.vertexformat, self.normalformat,
         self.polygonformat, self.boneformat) = struct.unpack_from('<BBBB', data, o)
        o += 4
        (num_vertices, num_polyt3, num_polyt4, num_bones) = \
            struct.unpack_from('<HHHH', data, o)
        o += 8

        if self.polygonformat >= 3:
            (num_polyf3, num_polyf4, self.num_textures, self.num_patterns,
             num_color) = struct.unpack_from('<HHHHH', data, o)
            o += 10
            # per-pattern polygon-count table: for each pattern, one group for
            # colored polygons plus one per texture.  The file stores only the
            # counts; the offsets are cumulative (see MascotCapsule V3 runtime,
            # Figure.loadMBAC -> patterns[]).
            for _ in range(self.num_patterns):
                row = []
                c3off = c4off = t3off = t4off = 0
                for group in range(self.num_textures + 1):
                    cnt3, cnt4 = struct.unpack_from('<HH', data, o)
                    o += 4
                    if group == 0:
                        row.append((c3off, c4off, cnt3, cnt4))
                        c3off += cnt3
                        c4off += cnt4
                    else:
                        row.append((t3off, t4off, cnt3, cnt4))
                        t3off += cnt3
                        t4off += cnt4
                self.patterns.append(row)

        if self.vertexformat != VERTEXFORMAT_PACKED:
            raise MBACError('unsupported vertex format %d' % self.vertexformat)

        unp = Unpacker(data, o)
        while len(self.vertices) < num_vertices:
            header = unp.u(8)
            magnitude = MAGNITUDE_BITS[header >> 6]
            count = (header & 0x3F) + 1
            for _ in range(count):
                self.vertices.append((unp.s(magnitude),
                                      unp.s(magnitude),
                                      unp.s(magnitude)))
        o = unp.pos

        if self.normalformat == NORMALFORMAT_PACKED:
            unp = Unpacker(data, o)
            have = 0
            while have < num_vertices:
                x = unp.s(7)
                if x == -64:
                    self.normals.append(NORMAL_DIRECTIONS[unp.u(3)])
                else:
                    fx = x / 64.0
                    fy = unp.s(7) / 64.0
                    zneg = unp.u(1)
                    t = 1.0 - fx * fx - fy * fy
                    fz = math.sqrt(t) * (-1.0 if zneg else 1.0) if t >= 0 else 0.0
                    self.normals.append((fx, fy, fz))
                have += 1
            o = unp.pos

        if self.polygonformat != POLYGONFORMAT_V3:
            raise MBACError('unsupported polygon format %d' % self.polygonformat)

        unp = Unpacker(data, o)
        if num_polyf3 + num_polyf4 > 0:
            unk_bits = unp.u(8)
            vbits = unp.u(8)
            cbits = unp.u(8)
            cidbits = unp.u(8)
            unp.u(8)
            for _ in range(num_color):
                self.colors.append((unp.u(cbits), unp.u(cbits), unp.u(cbits)))
            for _ in range(num_polyf3):
                unp.u(unk_bits)
                a, b, c = unp.u(vbits), unp.u(vbits), unp.u(vbits)
                unp.u(cidbits)
                self.faces.append((a, b, c))
                self.face_texture.append(0)
            for _ in range(num_polyf4):
                unp.u(unk_bits)
                a, b, c, d = (unp.u(vbits), unp.u(vbits), unp.u(vbits), unp.u(vbits))
                unp.u(cidbits)
                self.faces.append((a, b, c, d))
                self.face_texture.append(0)

        if num_polyt3 + num_polyt4 > 0:
            unk_bits = unp.u(8)
            vbits = unp.u(8)
            uvbits = unp.u(8)
            unp.u(8)
            for t3_index in range(num_polyt3):
                unp.u(unk_bits)
                a, b, c = unp.u(vbits), unp.u(vbits), unp.u(vbits)
                uv = [unp.u(uvbits) for _ in range(6)]
                self.faces.append((a, b, c, uv[0], uv[1], uv[2], uv[3], uv[4], uv[5]))
                self.face_texture.append(self._texture_of_t3(t3_index))
            for t4_index in range(num_polyt4):
                unp.u(unk_bits)
                a, b, c, d = (unp.u(vbits), unp.u(vbits), unp.u(vbits), unp.u(vbits))
                uv = [unp.u(uvbits) for _ in range(8)]
                self.faces.append((a, b, c, d,
                                   uv[0], uv[1], uv[2], uv[3],
                                   uv[4], uv[5], uv[6], uv[7]))
                self.face_texture.append(self._texture_of_t4(t4_index))
        o = unp.pos

        # bones
        for i in range(num_bones):
            (seg_vertices, parent) = struct.unpack_from('<Hh', data, o)
            o += 4
            local = list(struct.unpack_from('<hhhhhhhhhhhh', data, o))
            o += 24
            parent_world = self.bones[parent].world if parent >= 0 else IDENTITY_MAT12
            world = mat_mul_412(parent_world, local)
            self.bones.append(Bone(i, parent, seg_vertices, local, world,
                                   sum(b.vertices for b in self.bones),
                                   sum(b.vertices for b in self.bones) + seg_vertices))

        self.trailer = decode_trailer(data, o)

    # -- derived -----------------------------------------------------------
    def world_vertices(self):
        """Rest-pose positions with bone matrices applied (cached)."""
        if self._world_vertices is None:
            out = [None] * len(self.vertices)
            for b in self.bones:
                for i in range(b.start, b.end):
                    out[i] = mat_apply_412(b.world, self.vertices[i])
            for i, v in enumerate(out):
                if v is None:
                    out[i] = self.vertices[i]
            self._world_vertices = out
        return self._world_vertices

    def vertex_bone(self, index):
        for b in self.bones:
            if b.start <= index < b.end:
                return b.index
        return 0

    def _texture_of_group(self, index, t3):
        """Texture index (1-based) of a textured polygon, or 1 if unknown.

        Uses the default pattern (pattern 0), which is the one the runtime
        selects unless a dynamic-polygon pattern is requested.
        """
        if not self.patterns:
            return 1
        pattern = self.patterns[0]
        for tex in range(1, len(pattern)):
            t3off, t4off, t3cnt, t4cnt = pattern[tex]
            off = t3off if t3 else t4off
            cnt = t3cnt if t3 else t4cnt
            if cnt and off <= index < off + cnt:
                return tex
        return 1

    def _texture_of_t3(self, index):
        return self._texture_of_group(index, True)

    def _texture_of_t4(self, index):
        return self._texture_of_group(index, False)


# ---------------------------------------------------------------------------
# trailer
# ---------------------------------------------------------------------------

def decode_trailer(data, offset):
    """20-byte trailer == 2x { u16 key; u8 enc[8] }.

    plain[i] = ((enc[i] ^ key[i & 1]) + 127) & 0xFF
    The two halves must decode equal; stock files yield an ASCII vendor id
    such as "HI000000" / "SE000000".
    """
    out = []
    o = offset
    for _ in range(2):
        key = data[o:o + 2]
        o += 2
        plain = bytearray()
        for i in range(8):
            plain.append(((data[o] ^ key[i & 1]) + 127) & 0xFF)
            o += 1
        out.append((key, bytes(plain)))
    return out