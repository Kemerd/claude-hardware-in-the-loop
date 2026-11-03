"""
gland_access.py - plug-in for build_enclosure.py: the cable gland's nut and its wrench space inside the end wall must
stay clear of every part. A keep-out only (no part, no mass), checked by the builder.

Interpreter: the CadQuery virtual environment (loaded by the builder through cad/cad_plugins.py; never run alone).
"""
import cadquery as cq

NUT_AF = 15.0           # M12 gland lock nut across flats, mm (gland supplier drawing class)
WRENCH_EXTRA = 4.0      # radial room for a thin wrench around the nut, mm
DEPTH = 8.0             # nut height + room to turn it, mm


def build(ctx):
    """No parts: this plug-in only declares space."""
    return []


def keepouts(ctx):
    """Cylinder inside the +X wall, coaxial with the gland hole."""
    x_in = ctx["L"] / 2 - ctx["WALL"] - ctx["LEDGE_W"]             # inner face of the thickened end wall
    zc = 2.0 + (ctx["ledge_z"] - 2.0) / 2                          # gland centre height (floor 2.0 mm)
    r = NUT_AF / 2 / 0.866 + WRENCH_EXTRA                          # across-corners radius + wrench room
    space = cq.Workplane("YZ").workplane(offset=x_in - DEPTH).center(0, zc).circle(r).extrude(DEPTH - 0.05)
    return [("gland_nut_space", space)]
