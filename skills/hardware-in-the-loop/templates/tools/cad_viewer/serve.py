#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=======================================================================================================================
 serve.py - local host for the web CAD viewer, with a review-notes store
=======================================================================================================================
 Why this exists
   index.html runs on any static host, but a static host cannot keep review notes anywhere an agent can read them.
   This server serves the same page from this machine and adds a small notes API, so a note pinned on a part in the
   browser lands in an append-only JSONL file in the repo that the next CAD session reads.

 What it serves
   GET  /  or /index.html          the viewer page from THIS folder (edits to index.html are live)
   GET  /mesh.json, /mesh_<k>.json the packed build from --mesh (pack_mesh.py output)
   GET  /<overlay>.json            board / harness overlays: ONLY the names mesh.json lists ("pcb", "harness") plus any
                                   --overlay name, and only if the file sits directly in the --mesh folder
   GET  /api/notes                 every live note (adds minus deletes), oldest first
   POST /api/notes                 add one note: {"text", "part"?, "at_mm"?, "build"?, "view"?, "client_t"?}
   POST /api/notes/delete          tombstone one note: {"id"}
   Everything else is 404: no directory listings, no path traversal.

 The notes file is an append-only log, one JSON object per line:
     {"op": "add", "id": "...", "t": "<server ISO time>", "text": ..., "part": ..., "at_mm": [x, y, z], "build": ..., "view": {...}}
     {"op": "delete", "id": "...", "t": "..."}
   Nothing is rewritten in place, so a crash mid-write can at worst lose the last line.

 Interpreter: python 3.9+ (stdlib only)
 Run: python tools/cad_viewer/serve.py --mesh <pack_mesh out dir> [--notes <file.jsonl>] [--port 8765] [--host 127.0.0.1]
 Paths: command line (--mesh, --notes); the page and the default notes file resolve from __file__
        (default notes: <repo>/docs/cad_feedback/notes.jsonl, two folders above this file)
=======================================================================================================================
"""
import argparse
import datetime
import json
import os
import re
import sys
import threading
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# ---------------------------------------------------------------------------------------------------------------------
#  Windows console safety: part names and notes may carry any Unicode; never let a print kill the server
# ---------------------------------------------------------------------------------------------------------------------
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

HERE = os.path.dirname(os.path.abspath(__file__))                       # <repo>/tools/cad_viewer
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))                  # the repo the viewer sits in
DEFAULT_NOTES = os.path.join(REPO, "docs", "cad_feedback", "notes.jsonl")

# ---------------------------------------------------------------------------------------------------------------------
#  Limits: a note is a sentence or a paragraph, not an upload. Anything bigger is refused, never truncated silently.
# ---------------------------------------------------------------------------------------------------------------------
MAX_BODY_B = 64 * 1024
MAX_TEXT = 4000
MAX_NAME = 200
MESH_RE = re.compile(r"^/mesh(_\d{1,3})?\.json$")
OVERLAY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,120}\.json$")   # same rule as pack_mesh.py


def _now_iso():
    """Local time with offset, seconds resolution."""
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _finite3(v):
    """A list of three finite numbers, else None (garbled input from a page never reaches the file)."""
    if not isinstance(v, (list, tuple)) or len(v) != 3:
        return None
    out = []
    for x in v:
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            return None
        if x != x or x in (float("inf"), float("-inf")):
            return None
        out.append(round(float(x), 3))
    return out


def _clean_view(v):
    """Keep only the view fields the page writes (camera, target, section planes, hidden parts, toggles), type-checked."""
    if not isinstance(v, dict):
        return None
    out = {}
    cam, tgt = _finite3(v.get("cam")), _finite3(v.get("target"))
    if cam and tgt:
        out["cam"], out["target"] = cam, tgt
    clips = v.get("clips")
    if isinstance(clips, list) and len(clips) == 3:
        cl = []
        for c in clips:
            if not isinstance(c, dict):
                cl = None
                break
            pos = c.get("pos")
            if isinstance(pos, bool) or not isinstance(pos, (int, float)) or pos != pos:
                cl = None
                break
            cl.append({"on": bool(c.get("on")), "pos": round(float(pos), 2), "flip": bool(c.get("flip"))})
        if cl:
            out["clips"] = cl
    hidden = v.get("hidden")
    if isinstance(hidden, list):
        out["hidden"] = [str(h)[:MAX_NAME] for h in hidden[:2000] if isinstance(h, str)]
    for k in ("xray", "pcbx", "bright", "cg", "pcb", "harness"):
        if k in v:
            out[k] = bool(v.get(k))
    return out or None


def overlay_names(mesh_dir, extra):
    """The overlay file names this server may serve: mesh.json's "pcb" list + "harness" + --overlay, regex-checked."""
    names = set()
    try:
        with open(os.path.join(mesh_dir, "mesh.json"), encoding="utf-8") as fh:
            head = json.load(fh)
    except (OSError, ValueError):
        head = {}
    if isinstance(head, dict):
        pcb = head.get("pcb")
        if isinstance(pcb, list):
            names.update(n for n in pcb if isinstance(n, str))
        elif isinstance(pcb, str):
            names.add(pcb)
        if isinstance(head.get("harness"), str):
            names.add(head["harness"])
    names.update(extra or ())
    return {n for n in names if OVERLAY_RE.match(n) and not re.match(r"^mesh(_\d+)?\.json$", n)}


class NoteStore:
    """Append-only JSONL note log with a process lock (the server is threaded; one writer at a time)."""

    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.lock = threading.Lock()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)

    def _read_log(self):
        """Every well-formed line of the log, in order; a torn last line is skipped, not fatal."""
        rows = []
        if not os.path.isfile(self.path):
            return rows
        with open(self.path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                if isinstance(j, dict):
                    rows.append(j)
        return rows

    def live(self):
        """Adds minus deletes, oldest first."""
        with self.lock:
            rows = self._read_log()
        dead = {r.get("id") for r in rows if r.get("op") == "delete"}
        return [r for r in rows if r.get("op") == "add" and r.get("id") not in dead]

    def _append(self, row):
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.lock:
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())          # a saved note is on disk when the page says so

    def add(self, body):
        """Validate one note from the page; returns (row, None) or (None, error text)."""
        if not isinstance(body, dict):
            return None, "body must be a JSON object"
        text = body.get("text")
        if not isinstance(text, str) or not text.strip():
            return None, "text is empty"
        if len(text) > MAX_TEXT:
            return None, f"text longer than {MAX_TEXT} characters"
        part = body.get("part")
        part = str(part)[:MAX_NAME] if isinstance(part, str) and part else None
        build = body.get("build")
        build = str(build)[:MAX_NAME] if isinstance(build, str) and build else None
        client_t = body.get("client_t")
        row = {
            "op": "add",
            "id": uuid.uuid4().hex[:10],
            "t": _now_iso(),
            "text": text.strip(),
            "part": part,
            "at_mm": _finite3(body.get("at_mm")),
            "build": build,
            "view": _clean_view(body.get("view")),
            "client_t": str(client_t)[:40] if isinstance(client_t, str) else None,
        }
        self._append(row)
        return row, None

    def delete(self, note_id):
        """Tombstone a live note; returns None or an error text."""
        if not isinstance(note_id, str) or not re.fullmatch(r"[0-9a-f]{6,32}", note_id):
            return "bad id"
        if not any(r.get("id") == note_id for r in self.live()):
            return "no such note"
        self._append({"op": "delete", "id": note_id, "t": _now_iso()})
        return None


def make_handler(mesh_dir, store, extra_overlays):
    """Bind the mesh folder, the note store and the overlay allow-list into a request handler class."""

    class Handler(SimpleHTTPRequestHandler):
        # the static root is the mesh folder; index.html is special-cased to come from this script's folder
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=mesh_dir, **kw)

        def log_message(self, fmt, *args):
            sys.stderr.write("[viewer] " + (fmt % args) + "\n")

        def _json(self, code, obj):
            data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self):
            """The request body parsed as JSON, or raises ValueError with a reason."""
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise ValueError("bad Content-Length")
            if n <= 0:
                raise ValueError("empty body")
            if n > MAX_BODY_B:
                raise ValueError(f"body larger than {MAX_BODY_B} bytes")
            raw = self.rfile.read(n)
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ValueError("body is not UTF-8 JSON")

        # -------------------------------------------------------------------------------------------------------------
        #  GET: the page, the mesh pieces, the listed overlays, the notes. Everything else is 404.
        # -------------------------------------------------------------------------------------------------------------
        def do_GET(self):
            path = self.path.split("?", 1)[0].split("#", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    with open(os.path.join(HERE, "index.html"), "rb") as fh:
                        data = fh.read()
                except OSError as e:
                    return self._json(500, {"error": f"index.html unreadable: {e}"})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/notes":
                return self._json(200, store.live())
            if MESH_RE.match(path):
                return super().do_GET()
            # overlays: the allow-list is re-read per request so a re-pack is picked up without a restart
            name = path[1:]
            if "/" not in name and "\\" not in name and name in overlay_names(mesh_dir, extra_overlays):
                if os.path.isfile(os.path.join(mesh_dir, name)):
                    return super().do_GET()
            self.send_error(404)

        # -------------------------------------------------------------------------------------------------------------
        #  POST: add / delete a note
        # -------------------------------------------------------------------------------------------------------------
        def do_POST(self):
            path = self.path.split("?", 1)[0]
            try:
                body = self._body()
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            if path == "/api/notes":
                row, err = store.add(body)
                if err:
                    return self._json(400, {"error": err})
                print(f"[viewer] note {row['id']} on {row['part'] or '(view)'}: {row['text'][:120]}")
                return self._json(200, row)
            if path == "/api/notes/delete":
                err = store.delete(body.get("id") if isinstance(body, dict) else None)
                if err:
                    return self._json(400, {"error": err})
                return self._json(200, {"ok": True})
            self._json(404, {"error": "unknown endpoint"})

    return Handler


def main():
    ap = argparse.ArgumentParser(description="Serve the web CAD viewer locally with a review-notes store.")
    ap.add_argument("--mesh", required=True, help="folder with mesh.json + mesh_<k>.json (pack_mesh.py output)")
    ap.add_argument("--notes", default=DEFAULT_NOTES, help="append-only JSONL notes log")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1", help="bind address (default loopback only)")
    ap.add_argument("--overlay", action="append", default=[], help="extra overlay JSON name in --mesh to serve")
    a = ap.parse_args()

    mesh_dir = os.path.abspath(a.mesh)
    if not os.path.isfile(os.path.join(mesh_dir, "mesh.json")):
        sys.exit(f"serve.py: no mesh.json in {mesh_dir} (run pack_mesh.py first)")
    if not (0 < a.port < 65536):
        sys.exit(f"serve.py: bad port {a.port}")
    bad = [n for n in a.overlay if not OVERLAY_RE.match(n)]
    if bad:
        sys.exit(f"serve.py: overlay names must be plain [A-Za-z0-9_.-]+.json: {bad}")

    store = NoteStore(a.notes)
    srv = ThreadingHTTPServer((a.host, a.port), make_handler(mesh_dir, store, a.overlay))
    print(f"[viewer] http://{a.host}:{a.port}/  mesh {mesh_dir}  notes {store.path} ({len(store.live())} live)  "
          f"overlays {sorted(overlay_names(mesh_dir, a.overlay))}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
