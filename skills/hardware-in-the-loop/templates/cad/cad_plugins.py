"""
=======================================================================================================================
 cad_plugins.py - the plug-in part contract shared by every CadQuery builder.

 Interpreter: the CadQuery virtual environment (see docs/TOOLS.md of the method); this module itself imports only the
 standard library, so the contract checks also run under any Python >= 3.9.

 Why: when several people or agents add parts to one assembly, editing the builder core turns every addition into a
 merge conflict. Instead, a part is a FILE dropped into the assembly's parts/ folder:

     cad/<assembly>/parts/<name>.py   -> picked up by cad/<assembly>/build_<assembly>.py

 A plug-in module exposes (only build is required):

     build(ctx) -> list of (name, workplane, material, color)
         name       unique part name (must not collide with a builder part or another plug-in part)
         workplane  cq.Workplane holding the solid, in the assembly frame (mm)
         material   a key of the builder's density table (mass = volume x density) or a catalogue mass in grams (float)
         color      (r, g, b) floats 0..1
         ctx        a dict of the builder's frozen geometry (frames, key dimensions, pose); never mutated by a plug-in,
                    except ctx["plugin_parts"], which this module fills so later plug-ins can see earlier ones
     keepouts(ctx) -> list of (name, workplane)        volumes that must stay clear (checked, never exported, no mass)
     cuts(ctx)     -> list of (target_part, workplane) material removed from a BUILDER part (a cable hole through a wall)
     CONTACTS      =  iterable of (part_a, part_b)     designed contacts the builder's interference check must accept
     checks(ctx)   -> dict with at least {"pass": bool} AND-ed into the builder's overall pass

 Rules:
   - files whose name starts with "_" are ignored (helpers);
   - modules load in sorted file-name order, so the build is deterministic;
   - a broken plug-in fails the build loudly (import error, bad row, name collision): never a silent skip;
   - with no plug-ins every function returns an empty result and the builder's output is unchanged.

 Usage inside a builder:
     import cad_plugins as cp                      # the builder adds cad/ to sys.path or loads this file by path
     mods = cp.load(os.path.join(HERE, "parts"), "myasm_parts")
     for target, wp in cp.cuts(mods, ctx): ...     # apply to the builder's own parts first
     extra = cp.parts(mods, ctx, existing=builder_part_names)
     ko = cp.keepouts(mods, ctx); ok = all(r["pass"] for r in cp.checks(mods, ctx).values())
=======================================================================================================================
"""
import glob
import importlib.util
import os


def _stem(mod):
    """File stem of a loaded plug-in (for messages and result keys)."""
    return os.path.splitext(os.path.basename(getattr(mod, "__hil_plugin_path__", "?")))[0]


def load(parts_dir, namespace):
    """
    Import every parts_dir/*.py (sorted, skipping _*) as <namespace>.<stem>.

    @param parts_dir  folder holding the plug-ins; a missing folder means "no plug-ins" (empty list)
    @param namespace  module-name prefix, unique per assembly, so two assemblies' plug-ins never clash in sys.modules
    @return list of loaded modules, in load order
    @raise ImportError / AttributeError when a plug-in cannot be imported or has no callable build
    """
    mods = []
    if not parts_dir or not os.path.isdir(parts_dir):
        return mods
    if not isinstance(namespace, str) or not namespace:
        raise ValueError("cad_plugins.load: namespace must be a non-empty string")
    for path in sorted(glob.glob(os.path.join(parts_dir, "*.py"))):
        stem = os.path.splitext(os.path.basename(path))[0]
        if stem.startswith("_"):
            continue                                            # private helpers and __init__
        spec = importlib.util.spec_from_file_location(f"{namespace}.{stem}", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cad plug-in {path}: cannot create an import spec")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)                            # any exception propagates: a broken plug-in fails loudly
        if not callable(getattr(mod, "build", None)):
            raise AttributeError(f"cad plug-in {path}: missing build(ctx)")
        mod.__hil_plugin_path__ = path
        mods.append(mod)
    return mods


def _is_workplane(obj):
    """Duck-typed CadQuery workplane check (a .val() method), so this module never imports CadQuery."""
    return obj is not None and callable(getattr(obj, "val", None))


def parts(mods, ctx, existing=()):
    """
    Collect every plug-in's parts for this ctx, validated.

    @param mods      modules from load()
    @param ctx       the builder's context dict; ctx["plugin_parts"] is filled with {name: (wp, material, color)}
    @param existing  names of the builder's own parts (a plug-in may never replace one)
    @return list of (name, workplane, material, color) with material as str or float grams and color as 3 floats
    @raise ValueError on any malformed row or name collision
    """
    if not isinstance(ctx, dict):
        raise ValueError("cad_plugins.parts: ctx must be a dict")
    out, seen = [], set(existing or ())
    shared = ctx.setdefault("plugin_parts", {})
    for mod in mods or ():
        rows = mod.build(ctx)
        where = f"cad plug-in {getattr(mod, '__hil_plugin_path__', '?')}"
        if rows is None:
            raise ValueError(f"{where}: build() returned None (return [] for no parts)")
        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 4:
                raise ValueError(f"{where}: a part row must be (name, workplane, material, color), got {row!r}")
            name, wp, mat, col = row
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{where}: bad part name {name!r}")
            if name in seen:
                raise ValueError(f"{where}: part name {name!r} already exists")
            if not _is_workplane(wp):
                raise ValueError(f"{where}: part {name!r} is not a cq.Workplane")
            if isinstance(mat, bool) or not isinstance(mat, (str, float, int)):
                raise ValueError(f"{where}: part {name!r} material must be a density key or a mass in grams")
            if isinstance(mat, (int, float)) and not (mat > 0 and mat == mat and mat != float("inf")):
                raise ValueError(f"{where}: part {name!r} catalogue mass must be a positive finite number of grams")
            if not (isinstance(col, (tuple, list)) and len(col) == 3
                    and all(isinstance(c, (int, float)) and 0.0 <= float(c) <= 1.0 for c in col)):
                raise ValueError(f"{where}: part {name!r} color must be (r, g, b) floats in 0..1")
            seen.add(name)
            rec = (name, wp, float(mat) if isinstance(mat, (int, float)) else mat, tuple(float(c) for c in col))
            out.append(rec)
            shared[name] = rec[1:]
    return out


def keepouts(mods, ctx):
    """Every plug-in keep-out as (module_stem, name, workplane); a malformed row raises ValueError."""
    out = []
    for mod in mods or ():
        fn = getattr(mod, "keepouts", None)
        if not callable(fn):
            continue
        for row in fn(ctx) or ():
            if not isinstance(row, (tuple, list)) or len(row) != 2 or not isinstance(row[0], str) or not _is_workplane(row[1]):
                raise ValueError(f"cad plug-in {_stem(mod)}: keepout row must be (name, workplane)")
            out.append((_stem(mod), row[0], row[1]))
    return out


def cuts(mods, ctx):
    """Every plug-in cut as (module_stem, target_part, workplane); the builder refuses targets it does not own."""
    out = []
    for mod in mods or ():
        fn = getattr(mod, "cuts", None)
        if not callable(fn):
            continue
        for row in fn(ctx) or ():
            if not isinstance(row, (tuple, list)) or len(row) != 2 or not isinstance(row[0], str) or not _is_workplane(row[1]):
                raise ValueError(f"cad plug-in {_stem(mod)}: cut row must be (target_part_name, workplane)")
            out.append((_stem(mod), row[0], row[1]))
    return out


def contacts(mods):
    """Every plug-in's CONTACTS as a frozenset of frozenset({a, b}); a row that is not two different names raises."""
    out = set()
    for mod in mods or ():
        for row in getattr(mod, "CONTACTS", ()) or ():
            if (not isinstance(row, (tuple, list, set, frozenset)) or len(row) != 2
                    or not all(isinstance(x, str) and x for x in row) or len(set(row)) != 2):
                raise ValueError(f"cad plug-in {_stem(mod)}: CONTACTS row must be two different part names, got {row!r}")
            out.add(frozenset(row))
    return frozenset(out)


def checks(mods, ctx):
    """Run every plug-in's checks(ctx); {module_stem: result}. A result without a boolean "pass" counts as a failure."""
    res = {}
    for mod in mods or ():
        fn = getattr(mod, "checks", None)
        if not callable(fn):
            continue
        r = fn(ctx)
        if not isinstance(r, dict) or not isinstance(r.get("pass"), bool):
            r = {"pass": False, "error": "checks() must return a dict with a boolean 'pass'"}
        res[_stem(mod)] = r
    return res
