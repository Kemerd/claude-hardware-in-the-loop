"""
gasket.py - plug-in for build_enclosure.py: the lid seal, a solid EPDM cord in a groove cut into the ledge.

Interpreter: the CadQuery virtual environment (loaded by the builder through cad/cad_plugins.py; never run alone).

Contract use: cuts() removes the groove from the builder's "base" part, build() adds the cord, CONTACTS declares the
cord-to-base and cord-to-lid contacts as designed (the cord is compressed between them), checks() verifies the
compression ratio stays inside the band the cord supplier class recommends for static face seals (15 % .. 30 %).
"""
import cadquery as cq

CORD_D = 2.0            # cord diameter, mm
GROOVE_W = 2.4          # groove width, mm (room for the cord to spread when compressed)
GROOVE_DEPTH = 1.5      # groove depth below the ledge top, mm


def _ring(ctx, width, depth, z_top):
    """A rectangular ring centred on the ledge, `width` wide, from z_top - depth to z_top."""
    mid_l = ctx["L"] - 2 * ctx["WALL"] - ctx["LEDGE_W"]          # ring centre line runs mid-ledge
    mid_w = ctx["W"] - 2 * ctx["WALL"] - ctx["LEDGE_W"]
    outer = cq.Workplane("XY").workplane(offset=z_top - depth).rect(mid_l + width, mid_w + width).extrude(depth)
    inner = cq.Workplane("XY").workplane(offset=z_top - depth).rect(mid_l - width, mid_w - width).extrude(depth)
    return outer.cut(inner)


def cuts(ctx):
    """The groove in the base ledge."""
    return [("base", _ring(ctx, GROOVE_W, GROOVE_DEPTH, ctx["ledge_z"]))]


def build(ctx):
    """The cord, modelled at its installed (compressed) height so the stack-up closes: groove depth to lid underside."""
    height = GROOVE_DEPTH                                          # lid underside sits on the ledge top
    width = CORD_D * CORD_D / height * 0.785                       # equal cross-section area: pi d^2 / 4 = w * h
    return [("lid_gasket", _ring(ctx, min(width, GROOVE_W - 0.05), height, ctx["ledge_z"]), "EPDM", (0.08, 0.08, 0.09))]


CONTACTS = [("lid_gasket", "base"), ("lid_gasket", "lid")]


def checks(ctx):
    """Compression = 1 - installed height / free diameter, inside 15 .. 30 % for a static face seal."""
    comp = 1.0 - GROOVE_DEPTH / CORD_D
    return {"pass": 0.15 <= comp <= 0.30, "compression": round(comp, 3), "band": [0.15, 0.30]}
