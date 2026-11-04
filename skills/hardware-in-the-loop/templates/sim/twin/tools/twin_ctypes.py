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
import os
import shutil
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
