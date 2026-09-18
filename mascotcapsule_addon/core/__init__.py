"""
Core (Blender-independent) parsers and math for the MascotCapsule add-on.

Keeping this package free of any ``bpy`` import means it can be unit-tested
stand-alone and swapped/extended without touching the UI layer.
"""

from . import mbac_parse
from . import mtra_parse
from . import transform

from .mbac_parse import MBAC, MBACError
from .mtra_parse import MTRA, MTRAError, SEG_NAMES
from .transform import animated_world_matrices, bone_order

__all__ = [
    'mbac_parse', 'mtra_parse', 'transform',
    'MBAC', 'MBACError', 'MTRA', 'MTRAError', 'SEG_NAMES',
    'animated_world_matrices', 'bone_order',
]
