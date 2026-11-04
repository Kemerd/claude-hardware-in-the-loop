#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 twin_ctypes.py - Python binding of the twin's C ABI (sim/twin/include/hil_twin/hil_twin_api.h) over ctypes.

 Interpreter: any Python >= 3.9 (stdlib only), including the Python that ships with Isaac Sim.
 Paths: command line (the caller passes the shared library and the params file; nothing is found by guessing)

 Python is the host here, never the plant: every physics, sensor and firmware step runs in the C library. This module
 only marshals arguments, checks every return code and turns failures into exceptions that carry the library's own
 message (hil_twin_last_error).

 Usage
     from twin_ctypes import Twin
     with Twin(dll_path, params_path, seed=1) as tw:
         tw.command("arm", 1)
         tw.step(5000)
         q, qd, tau = tw.joint(0)
=======================================================================================================================
"""
import ctypes
import math
import os
import shutil
import struct
import tempfile

ABI_VERSION = 1


class TwinError(RuntimeError):
    """A call into the twin failed; the message is the library's hil_twin_last_error()."""


def _bind(lib):
    """Declare every exported function's signature (a wrong signature corrupts the stack, so all are explicit)."""
    c_dp = ctypes.POINTER(ctypes.c_double)
    sig = {
        "hil_twin_abi_version": (ctypes.c_int, []),
        "hil_twin_last_error": (ctypes.c_char_p, []),
        "hil_twin_create": (ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_uint64, ctypes.c_char_p]),
        "hil_twin_destroy": (None, [ctypes.c_void_p]),
        "hil_twin_dt": (ctypes.c_double, [ctypes.c_void_p]),
        "hil_twin_time": (ctypes.c_double, [ctypes.c_void_p]),
        "hil_twin_step": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int]),
        "hil_twin_step_external": (ctypes.c_int, [ctypes.c_void_p, c_dp, c_dp, c_dp, ctypes.c_int]),
        "hil_twin_joint_count": (ctypes.c_int, [ctypes.c_void_p]),
        "hil_twin_get_joint": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, c_dp, c_dp, c_dp]),
        "hil_twin_joint_props": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, c_dp]),
        "hil_twin_link_tx": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]),
        "hil_twin_link_rx": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]),
        "hil_twin_send_command": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]),
        "hil_twin_get_scalar": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, c_dp]),
        "hil_twin_set_scalar": (ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]),
        "hil_twin_trace_hash": (ctypes.c_uint64, [ctypes.c_void_p]),
    }
    for name, (res, args) in sig.items():
        fn = getattr(lib, name, None)
        if fn is None:
            raise TwinError(f"{name} is not exported by the twin library (stale build?)")
        fn.restype = res
        fn.argtypes = args
    return lib


def load_library(dll_path, private_copy=True):
    """
    Load the shared library. With private_copy (the default) the file is first copied to a fresh temp folder:
    a loaded DLL is locked on Windows, so a rebuild while a host (Isaac, a notebook) still runs would fail, and two
    copies are two independent modules.
    """
    if not dll_path or not os.path.isfile(dll_path):
        raise TwinError(f"twin library not found: {dll_path!r}")
    path = os.path.abspath(dll_path)
    if private_copy:
        d = tempfile.mkdtemp(prefix="hil_twin_")
        dst = os.path.join(d, os.path.basename(path))
        shutil.copy2(path, dst)
        path = dst
    lib = _bind(ctypes.CDLL(path))
    v = lib.hil_twin_abi_version()
    if v != ABI_VERSION:
        raise TwinError(f"ABI mismatch: library {v}, binding {ABI_VERSION}")
    return lib


class Twin:
    """One twin instance. Not thread-safe; use one instance per thread."""

    def __init__(self, dll_path, params_path, seed=1, trace_path=None, lib=None):
        self.lib = lib if lib is not None else load_library(dll_path)
        if not params_path or not os.path.isfile(params_path):
            raise TwinError(f"params file not found: {params_path!r}")
        tp = trace_path.encode("utf-8") if trace_path else None
        self.h = self.lib.hil_twin_create(os.path.abspath(params_path).encode("utf-8"), int(seed), tp)
        if not self.h:
            raise TwinError(self.last_error())

    # ---- lifetime
    def close(self):
        """Destroy the handle (safe to call twice)."""
        if getattr(self, "h", None):
            self.lib.hil_twin_destroy(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001 - interpreter shutdown may have torn ctypes down already
            pass

    # ---- helpers
    def last_error(self):
        """The library's message for the last failing call on this thread."""
        m = self.lib.hil_twin_last_error()
        return m.decode("utf-8", "replace") if m else ""

    def _check(self, rc, what):
        if rc < 0:
            raise TwinError(f"{what}: {self.last_error()}")
        return rc

    # ---- stepping
    @property
    def dt(self):
        """Fixed step, s."""
        return self.lib.hil_twin_dt(self.h)

    @property
    def time(self):
        """Simulated time, s."""
        return self.lib.hil_twin_time(self.h)

    def step(self, n=1):
        """Native mode: n steps, the twin integrates the mechanics."""
        self._check(self.lib.hil_twin_step(self.h, int(n)), "step")

    def step_external(self, q, qd):
        """Host-physics mode: hand over the host's joint angles / rates, get the actuator torques back."""
        n = len(q)
        if len(qd) != n:
            raise TwinError("q and qd must have the same length")
        aq = (ctypes.c_double * n)(*q)
        aqd = (ctypes.c_double * n)(*qd)
        tau = (ctypes.c_double * n)()
        self._check(self.lib.hil_twin_step_external(self.h, aq, aqd, tau, n), "step_external")
        return list(tau)

    # ---- joints
    def joint(self, i=0):
        """(q, qd, tau) of joint i."""
        q, qd, tau = ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
        self._check(self.lib.hil_twin_get_joint(self.h, i, ctypes.byref(q), ctypes.byref(qd), ctypes.byref(tau)), "joint")
        return q.value, qd.value, tau.value

    def joint_props(self, i=0):
        """dict with m_link, l_com, j_link, j_reflected, q_min, q_max, gravity."""
        out = (ctypes.c_double * 7)()
        self._check(self.lib.hil_twin_joint_props(self.h, i, out), "joint_props")
        keys = ("m_link", "l_com", "j_link", "j_reflected", "q_min", "q_max", "gravity")
        return dict(zip(keys, list(out)))

    # ---- link and named values
    def command(self, name, value=0.0):
        """Send a firmware command through the link codec: arm / set_pos / stop."""
        self._check(self.lib.hil_twin_send_command(self.h, name.encode("ascii"), float(value)), f"command {name}")

    def link_tx(self, data):
        """Raw bytes to the firmware."""
        return self._check(self.lib.hil_twin_link_tx(self.h, bytes(data), len(data)), "link_tx")

    def link_rx(self, cap=4096):
        """Raw bytes from the firmware."""
        buf = ctypes.create_string_buffer(cap)
        n = self._check(self.lib.hil_twin_link_rx(self.h, buf, cap), "link_rx")
        return buf.raw[:n]

    def get(self, name):
        """A named value (see hil_twin_get_scalar)."""
        v = ctypes.c_double()
        self._check(self.lib.hil_twin_get_scalar(self.h, name.encode("ascii"), ctypes.byref(v)), f"get {name}")
        return v.value

    def set(self, name, value):
        """Environment value or fault injection (see hil_twin_set_scalar)."""
        self._check(self.lib.hil_twin_set_scalar(self.h, name.encode("ascii"), float(value)), f"set {name}")

    @property
    def trace_hash(self):
        """FNV-1a 64 of the trace so far."""
        return int(self.lib.hil_twin_trace_hash(self.h))


# ---------------------------------------------------------------------------------------------------------------------
#  Scenario timeline (.scn), played with the native runner's rule (sim/twin/src/twin_main.c): before step k, at
#  t = k dt, every event with t_event <= t + 1e-12 that has not fired yet fires, in file order. "accept" lines are the
#  runner's verdict and are skipped here.
# ---------------------------------------------------------------------------------------------------------------------
class Scenario:
    """Parsed name, duration and timed events of a .scn file."""

    def __init__(self, path):
        self.name, self.duration, self.events, self.accepts = "", 0.0, [], []
        if not path or not os.path.isfile(path):
            raise TwinError(f"scenario not found: {path!r}")
        with open(path, "r", encoding="utf-8") as fh:
            for ln, raw in enumerate(fh, 1):
                f = raw.split("#", 1)[0].split()
                if not f:
                    continue
                if f[0] == "name" and len(f) >= 3 and f[1] == "=":
                    self.name = f[2]
                elif f[0] == "duration_s" and len(f) >= 3 and f[1] == "=":
                    self.duration = float(f[2])
                elif f[0] == "at" and len(f) >= 3:
                    if f[2] == "set":
                        if len(f) < 5:
                            raise TwinError(f"{path}:{ln}: 'set' needs a name and a value")
                        self.events.append([float(f[1]), True, f[3], float(f[4]), False])
                    else:
                        self.events.append([float(f[1]), False, f[2], float(f[3]) if len(f) >= 4 else 0.0, False])
                elif f[0] == "accept" and len(f) == 4:
                    if f[2] not in ("<", ">", "<=", ">=", "=="):
                        raise TwinError(f"{path}:{ln}: unknown operator {f[2]!r}")
                    self.accepts.append((f[1], f[2], float(f[3])))
                else:
                    raise TwinError(f"{path}:{ln}: cannot read {f[0]!r}")
        if not self.name or not self.duration > 0.0:
            raise TwinError(f"{path}: needs name and duration_s > 0")

    def apply_due(self, twin, t):
        """Fire every due event into the twin before the step that starts at t."""
        for e in self.events:
            if e[4] or t + 1e-12 < e[0]:
                continue
            e[4] = True
            if e[1]:
                twin.set(e[2], e[3])
            else:
                twin.command(e[2], e[3])


# ---------------------------------------------------------------------------------------------------------------------
#  Telemetry: the firmware's link frames (firmware/core/include/hil_core/link.h), decoded the way a station would
# ---------------------------------------------------------------------------------------------------------------------
def _crc8(data):
    """CRC-8, polynomial 0x07, init 0 (hil_link_crc8)."""
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class TelemetryDecoder:
    """Streaming decoder of 0xA5 | type | len | payload | crc8 frames; keeps the last telemetry record."""

    TELEM = 0x80
    FIELDS = ("state", "fault", "q_rad", "qd_rad_s", "i_a", "vbat_v", "t_motor_c", "t_winding_est_c", "derate")

    def __init__(self):
        self.buf = bytearray()
        self.last = None
        self.frames = 0
        self.bad = 0

    def feed(self, data):
        """Feed received bytes; returns the telemetry records completed by them."""
        out = []
        for byte in data:
            if not self.buf and byte != 0xA5:
                continue
            self.buf.append(byte)
            if len(self.buf) == 3 and self.buf[2] > 32:
                self.buf.clear()
                continue
            if len(self.buf) < 4 or len(self.buf) < self.buf[2] + 4:
                continue
            n = self.buf[2]
            frame = bytes(self.buf)
            self.buf.clear()
            if _crc8(frame[1:3 + n]) != frame[3 + n]:
                self.bad += 1
                continue
            self.frames += 1
            if frame[1] == self.TELEM and n == 30:
                rec = dict(zip(self.FIELDS, struct.unpack("<BBfffffff", frame[3:3 + n])))
                self.last = rec
                out.append(rec)
        return out


# ---------------------------------------------------------------------------------------------------------------------
#  Metrics and verdict: the subset of the native runner's metrics a host can compute (same names, same meaning)
# ---------------------------------------------------------------------------------------------------------------------
class Judge:
    """Accumulates the runner's metrics from telemetry and truth; evaluates a scenario's accept lines."""

    def __init__(self):
        self.m = {"homed": 0.0, "fault": 0.0, "state": -1.0, "min_derate": 1.0, "max_t_winding_c": -1e9,
                  "max_t_case_c": -1e9, "max_t_battery_c": -1e9, "min_vbat_v": 1e9, "max_abs_i_a": 0.0,
                  "link_bad": 0.0, "telem_frames": 0.0}
        self.dec = TelemetryDecoder()
        self.target = None

    def after_step(self, twin):
        """Call after every step: drains the link, updates the extremes."""
        for rec in self.dec.feed(twin.link_rx()):
            self.m["state"], self.m["fault"] = float(rec["state"]), float(rec["fault"])
            self.m["homed"] = 1.0 if rec["state"] == 2 else self.m["homed"]
            self.m["min_derate"] = min(self.m["min_derate"], rec["derate"])
        self.m["link_bad"], self.m["telem_frames"] = float(self.dec.bad), float(self.dec.frames)
        i = abs(twin.get("i_motor_a"))
        self.m["max_abs_i_a"] = max(self.m["max_abs_i_a"], i)
        self.m["max_t_winding_c"] = max(self.m["max_t_winding_c"], twin.get("t_winding_c"))
        self.m["max_t_case_c"] = max(self.m["max_t_case_c"], twin.get("t_case_c"))
        self.m["max_t_battery_c"] = max(self.m["max_t_battery_c"], twin.get("t_battery_c"))
        self.m["min_vbat_v"] = min(self.m["min_vbat_v"], twin.get("vbat_v"))

    def finish(self, twin, scenario):
        """Final metrics and the verdict: (passed, [(metric, op, limit, value, ok), ...])."""
        target = None
        for e in scenario.events:
            if not e[1] and e[2] == "set_pos" and e[4]:
                target = e[3]
        q = twin.get("q_rad")
        self.m["final_err_rad"] = abs(q - (target if target is not None else q))
        self.m["final_abs_i_a"] = abs(twin.get("i_motor_a"))
        rows, ok_all = [], True
        ops = {"<": lambda v, l: v < l, ">": lambda v, l: v > l, "<=": lambda v, l: v <= l,
               ">=": lambda v, l: v >= l, "==": lambda v, l: abs(v - l) < 1e-9}
        for name, op, lim in scenario.accepts:
            v = self.m.get(name, float("nan"))
            ok = name in self.m and math.isfinite(v) and ops[op](v, lim)
            ok_all &= ok
            rows.append((name, op, lim, v, ok))
        return ok_all and bool(rows), rows
