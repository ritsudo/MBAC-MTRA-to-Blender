"""
MTRA parser (MascotCapsule Micro3D binary animation, format version 5).

Pure Python, no Blender dependency.

Layout (little-endian), version 5:

    char   magic[2]            "MT"
    u16    version             5
    u16    num_actions
    u16    num_segments        == number of BAC bones
    u8     unk3[16]            reserved
    u32    unk4                unknown (size/flag)
    repeat(num_actions):
        u16 num_keyframes      == TRA4 totalFrame
        repeat(num_segments):
            u8 type            segment subtype, see SEG_*
            <channel key-lists>
        u16 num_aux
        repeat(num_aux): u16 u16 u16
    <20-byte obfuscation trailer>

Segment subtypes (storage order is always translate, scale, rotate, roll;
a compressed subtype omits the channels that are constant):

    type 0  matrix                      static 3x4 matrix (24 bytes)
    type 1  identity
    type 2  translate + scale + rotate + roll
    type 3  translate(const) + rotate + roll(const)
    type 4  rotate + roll
    type 5  rotate
    type 6  translate + rotate + roll

Key-list entry layout:
    translate / scale / rotate : (u16 frame, i16 x, i16 y, i16 z)   8 bytes
    roll                       : (u16 frame, i16 angle)              4 bytes

Units:
    scale, rotate  4.12 fixed point (4096 == 1.0; rotate entries are unit +Z
                   direction vectors)
    translate      raw integer model units
    roll           4.12 fixed point turns (4096 == 360 deg); confirmed against
                   the MascotCapsule V3 reference runtime (Util3D.sin/cos)
"""

import struct

from .mbac_parse import decode_trailer

MAGIC = b'MT'

SEG_MATRIX = 0
SEG_IDENTITY = 1
SEG_FULL = 2
SEG_TRANS_ROTATE_ROLL_CONST = 3
SEG_ROTATE_ROLL = 4
SEG_ROTATE = 5
SEG_TRANS_ROTATE_ROLL = 6

SEG_NAMES = {
    SEG_MATRIX: 'matrix',
    SEG_IDENTITY: 'identity',
    SEG_FULL: 'translate+scale+rotate+roll',
    SEG_TRANS_ROTATE_ROLL_CONST: 'translate(const)+rotate+roll(const)',
    SEG_ROTATE_ROLL: 'rotate+roll',
    SEG_ROTATE: 'rotate',
    SEG_TRANS_ROTATE_ROLL: 'translate+rotate+roll',
}

# channels present in each subtype, in storage order; value is entry size
SEG_CHANNELS = {
    SEG_FULL: (('translate', 8), ('scale', 8), ('rotate', 8), ('roll', 4)),
    SEG_ROTATE_ROLL: (('rotate', 8), ('roll', 4)),
    SEG_ROTATE: (('rotate', 8),),
    SEG_TRANS_ROTATE_ROLL: (('translate', 8), ('rotate', 8), ('roll', 4)),
}

FIX12 = 4096.0


class MTRAError(Exception):
    pass


class Segment:
    __slots__ = ('index', 'type', 'matrix', 'translate', 'scale', 'rotate', 'roll')

    def __init__(self, index, segtype):
        self.index = index
        self.type = segtype
        self.matrix = None
        self.translate = None
        self.scale = None
        self.rotate = None
        self.roll = None

    @property
    def name(self):
        return SEG_NAMES.get(self.type, 'unknown')

    @property
    def is_animated(self):
        return any(getattr(self, c) for c in ('translate', 'scale', 'rotate', 'roll'))

    def channel(self, name):
        return getattr(self, name)

    def key_times(self):
        times = set()
        for c in ('translate', 'scale', 'rotate', 'roll'):
            entries = getattr(self, c)
            if entries:
                times.update(e[0] for e in entries)
        return sorted(times)


class Action:
    __slots__ = ('index', 'num_keyframes', 'segments', 'aux')

    def __init__(self, index, num_keyframes, segments, aux):
        self.index = index
        self.num_keyframes = num_keyframes
        self.segments = segments
        self.aux = aux


class MTRA:
    def __init__(self):
        self.version = 0
        self.num_actions = 0
        self.num_segments = 0
        self.unk3 = b''
        self.unk4 = 0
        self.actions = []
        self.trailer = None

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
        if len(data) < 8:
            raise MTRAError('file too short')
        magic, version = struct.unpack_from('<2sH', data, 0)
        if magic != MAGIC:
            raise MTRAError('not an MTRA file (magic %r); Fishlabs assets may '
                            'need deobfuscation first' % magic)
        self.version = version
        if version != 5:
            raise MTRAError('unsupported MTRA version %d (only v5 is decoded; '
                            'v4 uses a different layout)' % version)

        (self.num_actions, self.num_segments, self.unk3, self.unk4) = \
            struct.unpack_from('<HH16sI', data, 4)
        o = 4 + 24

        for ai in range(self.num_actions):
            num_keyframes, = struct.unpack_from('<H', data, o)
            o += 2
            segments = []
            for si in range(self.num_segments):
                segtype = data[o]
                o += 1
                seg = Segment(si, segtype)
                if segtype == SEG_MATRIX:
                    seg.matrix = list(struct.unpack_from('<hhhhhhhhhhhh', data, o))
                    o += 24
                elif segtype == SEG_IDENTITY:
                    pass
                elif segtype == SEG_TRANS_ROTATE_ROLL_CONST:
                    # constant translate (3x s16), rotate key-list, constant roll
                    seg.translate = [(0,) + struct.unpack_from('<hhh', data, o)]
                    o += 6
                    seg.rotate, o = _read_channel(data, o, 8)
                    seg.roll = [(0, struct.unpack_from('<h', data, o)[0])]
                    o += 2
                elif segtype in SEG_CHANNELS:
                    for chname, size in SEG_CHANNELS[segtype]:
                        entries, o = _read_channel(data, o, size)
                        setattr(seg, chname, entries)
                else:
                    raise MTRAError('unknown segment type %d at offset 0x%X '
                                    '(bone %d)' % (segtype, o - 1, si))
                segments.append(seg)

            num_aux, = struct.unpack_from('<H', data, o)
            o += 2
            aux = []
            for _ in range(num_aux):
                aux.append(struct.unpack_from('<HHH', data, o))
                o += 6
            self.actions.append(Action(ai, num_keyframes, segments, aux))

        self.trailer = decode_trailer(data, o)

    # -- animation queries -------------------------------------------------
    def segment(self, action_index, bone_index):
        return self.actions[action_index].segments[bone_index]

    def sample_channel(self, bone_index, channel, frame, action_index=0):
        seg = self.actions[action_index].segments[bone_index]
        entries = seg.channel(channel)
        if not entries:
            return None
        return sample_keys(entries, frame)


# ---------------------------------------------------------------------------
# channel reading / sampling
# ---------------------------------------------------------------------------

def _read_channel(data, o, size):
    count, = struct.unpack_from('<H', data, o)
    o += 2
    entries = []
    for _ in range(count):
        if size == 8:
            entries.append(struct.unpack_from('<Hhhh', data, o))
        else:
            entries.append(struct.unpack_from('<Hh', data, o))
        o += size
    return entries, o


def sample_keys(entries, frame):
    """Linearly interpolate a key-list at an arbitrary (float) frame."""
    if not entries:
        return None
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