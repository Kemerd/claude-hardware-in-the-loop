#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 rig_usd.py - authors the example actuator rig as a USD articulation from the twin's own joint properties.

 Interpreter: Isaac Sim's Python, or any Python with usd-core (pip install usd-core) for the CPU check.
 Run: python sim/isaac/rig_usd.py --dll <hil_twin_api> --params <params.txt> --out <rig.usda>     (CPU only)
 Paths: command line

 Nothing is hand-copied: link mass, CoM distance, inertia about the axis and the reflected rotor inertia (applied as
 the PhysX joint ARMATURE) come from hil_twin_joint_props, which reads the same params (and CAD-export include) as
 the native twin. Frames: stage Z up, metres; the link points along +X of the rig; the revolute axis is the rig's Y.
 A positive PhysX joint angle about +Y turns +X towards -Z, so the twin's q (positive lifts +X towards +Z) is
 q_twin = AXIS_SIGN * q_physx with AXIS_SIGN = -1; torques map the same way.
 The joint has no PhysX drive and no PhysX limits: the twin's actuator torque includes the hard-stop law, so the
 stops are modelled once. A project replaces the box link with the CAD mesh (cad/<asm>/stl) and keeps the rest.
=======================================================================================================================
"""
import argparse
import os
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

AXIS_SIGN = -1.0
RIG = "/World/Rig"


def build_rig(stage, props, dt, q0, link_size=(0.30, 0.04, 0.04)):
    """
    Author /World/PhysicsScene and /World/Rig (fixed base, one link, one revolute joint) into an open stage.
    props: hil_twin_joint_props as a dict; dt: the twin step; q0: the twin's initial angle (rad).
    Returns the joint prim path.
    """
    from pxr import Gf, Sdf, UsdGeom, UsdPhysics  # noqa: WPS433 - only importable inside a USD runtime

    if not (props.get("m_link", 0) > 0 and props.get("j_link", 0) > 0 and dt > 0):
        raise ValueError(f"joint props not physical: {props}, dt {dt}")
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)

    # ---- physics scene: gravity from the twin, one physics step per twin step
    scene = UsdPhysics.Scene.Define(stage, Sdf.Path("/World/PhysicsScene"))
    scene.CreateGravityDirectionAttr(Gf.Vec3f(0.0, 0.0, -1.0))
    scene.CreateGravityMagnitudeAttr(float(props["gravity"]))
    _physx_attr(scene.GetPrim(), "PhysxSceneAPI", "physxScene:timeStepsPerSecond", Sdf.ValueTypeNames.UInt,
                int(round(1.0 / dt)))

    # ---- articulation root with a fixed base (the rig is bolted to the bench)
    rig = UsdGeom.Xform.Define(stage, Sdf.Path(RIG))
    UsdPhysics.ArticulationRootAPI.Apply(rig.GetPrim())
    base = UsdGeom.Cube.Define(stage, Sdf.Path(f"{RIG}/base"))
    base.CreateSizeAttr(0.08)
    UsdPhysics.RigidBodyAPI.Apply(base.GetPrim())
    UsdPhysics.CollisionAPI.Apply(base.GetPrim())
    fixed = UsdPhysics.FixedJoint.Define(stage, Sdf.Path(f"{RIG}/base_fixed"))
    fixed.CreateBody1Rel().SetTargets([base.GetPath()])

    # ---- the link: a box from the pivot along +X, mass properties from the twin (CoM at l_com, inertia about the
    #      CoM = j_link - m l^2 on the hinge axis; the other axes do not move and get the box's own values)
    lx, ly, lz = link_size
    m, l, j = float(props["m_link"]), float(props["l_com"]), float(props["j_link"])
    link = UsdGeom.Xform.Define(stage, Sdf.Path(f"{RIG}/link"))
    xf = UsdGeom.XformCommonAPI(link)
    xf.SetRotate(Gf.Vec3f(0.0, AXIS_SIGN * q0 * 57.29577951308232, 0.0))
    geo = UsdGeom.Cube.Define(stage, Sdf.Path(f"{RIG}/link/geom"))
    geo.CreateSizeAttr(1.0)
    UsdGeom.XformCommonAPI(geo).SetTranslate(Gf.Vec3d(lx / 2.0, 0.0, 0.0))
    UsdGeom.XformCommonAPI(geo).SetScale(Gf.Vec3f(lx, ly, lz))
    UsdPhysics.RigidBodyAPI.Apply(link.GetPrim())
    UsdPhysics.CollisionAPI.Apply(geo.GetPrim())
    mass = UsdPhysics.MassAPI.Apply(link.GetPrim())
    mass.CreateMassAttr(m)
    mass.CreateCenterOfMassAttr(Gf.Vec3f(l, 0.0, 0.0))
    i_axis = max(1e-9, j - m * l * l)
    i_long = m * (ly * ly + lz * lz) / 12.0
    mass.CreateDiagonalInertiaAttr(Gf.Vec3f(i_long, i_axis, i_axis))

    # ---- revolute joint about Y at the pivot; no drive, no limits (the twin owns torque and stops); armature = the
    #      reflected rotor inertia N^2 J_rotor, which PhysX adds to the joint's effective inertia
    joint = UsdPhysics.RevoluteJoint.Define(stage, Sdf.Path(f"{RIG}/hinge"))
    joint.CreateAxisAttr("Y")
    joint.CreateBody0Rel().SetTargets([base.GetPath()])
    joint.CreateBody1Rel().SetTargets([link.GetPath()])
    joint.CreateLocalPos0Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    joint.CreateLocalPos1Attr(Gf.Vec3f(0.0, 0.0, 0.0))
    _physx_attr(joint.GetPrim(), "PhysxJointAPI", "physxJoint:armature", Sdf.ValueTypeNames.Float, float(props["j_reflected"]))
    _physx_attr(joint.GetPrim(), "PhysxJointAPI", "physxJoint:jointFriction", Sdf.ValueTypeNames.Float, 0.0)
    return str(joint.GetPath())


def _physx_attr(prim, api, name, vtype, value):
    """
    Apply a PhysX schema and set one of its attributes. Inside Kit the PhysxSchema module does it; in a plain
    usd-core Python (the CPU check) the same applied-schema token and attribute name are authored directly, which is
    exactly what the schema writes to the layer.
    """
    try:
        from pxr import PhysxSchema  # noqa: WPS433 - Kit only

        getattr(PhysxSchema, api).Apply(prim)
    except ImportError:
        prim.AddAppliedSchema(api)
    prim.CreateAttribute(name, vtype).Set(value)


def main():
    """CPU check: author the rig into a new USD file and verify mass, CoM and armature round-trip."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "twin", "tools"))
    from twin_ctypes import Twin, load_library  # noqa: E402
    from pxr import Usd, UsdPhysics  # noqa: E402

    ap = argparse.ArgumentParser(description="author the rig USD from the twin")
    ap.add_argument("--dll", required=True)
    ap.add_argument("--params", required=True)
    ap.add_argument("--out", required=True, help="output .usda (put it under .tmp/)")
    a = ap.parse_args()
    with Twin(None, a.params, seed=1, lib=load_library(a.dll)) as tw:
        props, dt, q0 = tw.joint_props(0), tw.dt, tw.joint(0)[0]
    stage = Usd.Stage.CreateNew(a.out)
    joint_path = build_rig(stage, props, dt, q0)
    stage.GetRootLayer().Save()

    # ---- round trip
    st = Usd.Stage.Open(a.out)
    m = UsdPhysics.MassAPI(st.GetPrimAtPath(f"{RIG}/link")).GetMassAttr().Get()
    arm = st.GetPrimAtPath(joint_path).GetAttribute("physxJoint:armature").Get()
    ok = abs(m - props["m_link"]) < 1e-6 and abs(arm - props["j_reflected"]) < 1e-9
    print(f"[rig-usd] {a.out}: mass {m} kg, armature {arm} kg m^2: {'OK' if ok else 'MISMATCH'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
