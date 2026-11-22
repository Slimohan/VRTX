#!/usr/bin/env python3
# Small web server for the VRTX web page. Standard library only.
#
#   python3 vrtx_server.py [--port 8099] [--moonraker http://127.0.0.1:7125]
#
# Serves index.html and forwards a short list of Moonraker requests, so the
# page needs no CORS setup in Moonraker. Anyone who can open the page can move
# the printer, so run it only on a trusted network.
#
# Slicer filament sync: every few seconds it copies the filament of every tool
# (VRTX_SET_FILAMENT, saved as t<N>_filament) into Moonraker's database,
# namespace lane_data, one entry T<N> per tool. OrcaSlicer's "Synchronize
# filaments" for Moonraker printers reads that namespace and fills the
# project's filament list with the colour and material of every tool. Only the
# entries T0 ... T9 are written; other lane_data entries are left alone.
# Switch it off with variable_slicer_sync: False in _VRTX_TOOLS_SETTINGS,
# or with --no-slicer-sync.
#
# Spoolman: a tool whose filament has a spool id (SPOOL=, or the Spoolman panel
# of Mainsail) takes colour, material and name from that spool, on the page
# (GET /spool?ids=1,2) and in the slicer sync; GET /spools lists the spools
# that are not archived, for choosing one on the page. Read through Moonraker's
# [spoolman] component (/server/spoolman/proxy, only GET /v1/spool and
# GET /v1/spool/<id>), kept 30 s. Without Spoolman nothing changes.
# The same loop also copies material, name, colour and temperature of a tool's
# spool into its saved filament (VRTX_SET_FILAMENT, only when they differ and
# not while printing), so the console (VRTX_TOOLS_STATUS) and the macros have
# them too. variable_spoolman: False in _VRTX_TOOLS_SETTINGS switches it off.
#
# Part of VRTX (github.com/Slimohan/VRTX), GPL-3.0.

import argparse
import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

# Moonraker paths the page may use. Everything else is refused.
ALLOWED = (
    "/printer/gcode/script",
    "/printer/objects/query",
    "/printer/info",
    "/server/webcams/list",
    "/server/files/metadata",
    "/server/files/list",
)


# ---------------------------------------------------------------- Spoolman
SPOOL_TTL = 30.0
_spools = {}            # spool id -> (time read, info or None)
_spool_lock = threading.Lock()


def _hex6(v):
    v = str(v or "").split(",")[0].strip().lstrip("#").upper()
    return v[:6] if len(v) >= 6 and all(c in "0123456789ABCDEF" for c in v[:6]) else ""


def _spool_fields(r):
    """The few values of a Spoolman spool object that VRTX uses."""
    fil = r.get("filament") or {}
    temp = fil.get("settings_extruder_temp")
    return {
        "id": int(r.get("id")),
        "color": _hex6(fil.get("color_hex") or fil.get("multi_color_hexes")),
        "material": str(fil.get("material") or ""),
        "name": str(fil.get("name") or ""),
        "vendor": str((fil.get("vendor") or {}).get("name") or ""),
        "remaining_weight": r.get("remaining_weight"),
        "temp": float(temp) if isinstance(temp, (int, float)) else 0.0,
        "location": str(r.get("location") or ""),
        "initial_weight": r.get("initial_weight") or fil.get("weight"),
        "last_used": r.get("last_used"),
    }


def _proxy_get(moonraker, path):
    """GET a Spoolman path through Moonraker's [spoolman] proxy (v1 and v2 answers)."""
    r = _call(moonraker, "POST", "/server/spoolman/proxy",
              {"request_method": "GET", "path": path}, timeout=5).get("result")
    if isinstance(r, dict) and "response" in r and ("error" in r or len(r) <= 2):
        r = r.get("response")
    return r


_spool_list = [0.0, None]


def spool_list(moonraker):
    """All spools that are not archived, newest list kept 30 s. None without Spoolman."""
    now = time.time()
    with _spool_lock:
        if _spool_list[1] is not None and now - _spool_list[0] < SPOOL_TTL:
            return _spool_list[1]
    out = None
    try:
        r = _proxy_get(moonraker, "/v1/spool?allow_archived=false")
        if isinstance(r, list):
            out = [_spool_fields(s) for s in r if isinstance(s, dict) and s.get("id") is not None]
    except Exception:
        out = None
    with _spool_lock:
        _spool_list[0], _spool_list[1] = now, out
        for s in out or []:
            _spools[s["id"]] = (now, s)
    return out


def spool_info(moonraker, sid):
    """Colour, material, name ... of Spoolman spool `sid`, or None (no Spoolman,
    no such spool). Cached SPOOL_TTL seconds, a failure too."""
    sid = int(sid)
    now = time.time()
    with _spool_lock:
        c = _spools.get(sid)
        if c and now - c[0] < SPOOL_TTL:
            return c[1]
    info = None
    try:
        r = _proxy_get(moonraker, "/v1/spool/%d" % sid) or {}
        if r.get("id") is not None:
            info = _spool_fields(r)
    except Exception:
        info = None
    with _spool_lock:
        _spools[sid] = (now, info)
    return info


def make_handler(moonraker):
    class Handler(http.server.BaseHTTPRequestHandler):
        server_version = "vrtx-ui"

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _proxy(self, method):
            parts = urllib.parse.urlsplit(self.path)
            path = parts.path[len("/moonraker"):]
            if path not in ALLOWED:
                return self._send(403, b'{"error":"path not allowed"}')
            data = None
            if method == "POST":
                length = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(length)
            url = moonraker + path + ("?" + parts.query if parts.query else "")
            req = urllib.request.Request(url, data=data, method=method)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return self._send(r.status, r.read())
            except urllib.error.HTTPError as e:
                return self._send(e.code, e.read())
            except Exception as e:  # moonraker not reachable
                msg = str(e).replace('"', "'")
                return self._send(502, ('{"error":"%s"}' % msg).encode())

        def _spools(self):
            q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            ids = []
            for part in ",".join(q.get("ids", [])).split(","):
                part = part.strip()
                if part.isdigit() and 0 < int(part) <= 999999 and int(part) not in ids:
                    ids.append(int(part))
            out = {str(i): spool_info(moonraker, i) for i in ids[:10]}
            return self._send(200, json.dumps(out).encode())

        def do_GET(self):
            if self.path.startswith("/moonraker/"):
                return self._proxy("GET")
            if self.path == "/spool" or self.path.startswith("/spool?"):
                return self._spools()
            if self.path == "/spools":
                # an empty list without Spoolman (no [spoolman] in Moonraker, or not reachable):
                # the page then uses the plain spool id field
                lst = spool_list(moonraker)
                return self._send(200, json.dumps(lst or []).encode())
            if self.path in ("/", "/index.html"):
                with open(os.path.join(HERE, "index.html"), "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            return self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if self.path.startswith("/moonraker/"):
                return self._proxy("POST")
            return self._send(404, b'{"error":"not found"}')

    return Handler


# ---------------------------------------------------------------- slicer sync
LANE_NS = "lane_data"
OWN_KEYS = ["T%d" % i for i in range(10)]


def _call(moonraker, method, path, body=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(moonraker + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"{}")


def with_spool(f, spool):
    """The filament of a tool with the values of its Spoolman spool on top."""
    if not isinstance(f, dict):
        return f
    try:
        sid = int(f.get("spool_id") or 0)
    except (TypeError, ValueError):
        sid = 0
    s = spool(sid) if sid > 0 else None
    if not s:
        return f
    g = dict(f)
    for k in ("color", "material"):
        if s.get(k):
            g[k] = s[k]
    name = " ".join(x for x in (s.get("vendor"), s.get("name")) if x)
    if name:
        g["name"] = name
    if not float(g.get("temp") or 0) and s.get("temp"):
        g["temp"] = s["temp"]
    return g


def lane_entries(status, spool=lambda sid: None):
    """The lane_data entries VRTX wants, from a printer status answer.
    None means: do not touch lane_data (sync off, or no tool changer yet).
    spool(id) gives the Spoolman values of a spool (None without Spoolman).
    Every tool gets an entry: a tool without a filament gets an empty material,
    which OrcaSlicer shows as an empty slot. Orca counts the slots up to the
    highest lane it finds, so a missing lane would shorten its filament list
    (and break the flush matrix of a project with more filaments)."""
    tc = status.get("toolchanger")
    settings = status.get("gcode_macro _VRTX_TOOLS_SETTINGS") or {}
    if not tc or str(settings.get("slicer_sync", True)).lower() in ("false", "0", "no"):
        return None
    variables = (status.get("save_variables") or {}).get("variables") or {}
    want = {}
    for n in tc.get("tool_numbers") or []:
        if not 0 <= int(n) <= 9:
            continue
        f = with_spool(variables.get("t%d_filament" % n), spool)
        e = {"lane": str(n), "material": ""}
        if not isinstance(f, dict) or not (f.get("material") or f.get("color")):
            want["T%d" % n] = e   # no filament: an empty slot
            continue
        e["material"] = str(f.get("material") or "")
        col = str(f.get("color") or "").strip().lstrip("#").upper()
        if len(col) == 6:
            e["color"] = "#" + col
        if f.get("name"):
            e["name"] = str(f["name"])
        try:
            if float(f.get("temp") or 0) > 0:
                e["nozzle_temp"] = int(round(float(f["temp"])))
        except (TypeError, ValueError):
            pass
        want["T%d" % n] = e
    return want


def _clean(v):
    """A value that can go into a G-code parameter (no quotes, no comment characters)."""
    return " ".join(str(v or "").replace('"', "").replace("'", "").replace("\\", "")
                    .replace("#", "").replace(";", "").split())


def fill_filaments(moonraker, status):
    """Copy the values of each tool's Spoolman spool into its saved filament
    (t<n>_filament) when they differ. Not while printing."""
    settings = status.get("gcode_macro _VRTX_TOOLS_SETTINGS") or {}
    if str(settings.get("spoolman", True)).lower() in ("false", "0", "no"):
        return
    if (status.get("print_stats") or {}).get("state") in ("printing", "paused"):
        return
    tc = status.get("toolchanger") or {}
    variables = (status.get("save_variables") or {}).get("variables") or {}
    for n in tc.get("tool_numbers") or []:
        f = variables.get("t%d_filament" % n)
        if not isinstance(f, dict):
            continue
        try:
            sid = int(f.get("spool_id") or 0)
        except (TypeError, ValueError):
            sid = 0
        s = spool_info(moonraker, sid) if sid > 0 else None
        if not s:
            continue
        mat = _clean(s.get("material")).replace(" ", "-").upper()[:20]
        name = _clean(" ".join(x for x in (s.get("vendor"), s.get("name")) if x))[:40]
        color = (s.get("color") or "").upper()
        try:
            temp = float(s.get("temp") or 0) or float(f.get("temp") or 0)
        except (TypeError, ValueError):
            temp = 0.0
        same = (str(f.get("material") or "").upper() == mat and str(f.get("name") or "") == name
                and (not color or str(f.get("color") or "").upper() == color)
                and abs(float(f.get("temp") or 0) - temp) < 0.5)
        if same:
            continue
        script = 'VRTX_SET_FILAMENT T=%d MATERIAL=%s NAME="%s"%s TEMP=%g SPOOL=%d' % (
            n, mat or '""', name, (" COLOR=" + color) if color else "", temp, sid)
        _call(moonraker, "POST", "/printer/gcode/script?script=" + urllib.parse.quote(script), {})


def sync_once(moonraker, last):
    """One pass: write changed entries, remove VRTX's entries that are gone.
    `last` is what this process wrote before; returns the new state."""
    q = "/printer/objects/query?toolchanger&save_variables&print_stats=state&gcode_macro%20_VRTX_TOOLS_SETTINGS"
    status = _call(moonraker, "GET", q)["result"]["status"]
    fill_filaments(moonraker, status)
    want = lane_entries(status, lambda sid: spool_info(moonraker, sid))
    if want is None:
        return last
    try:
        have = _call(moonraker, "GET", "/server/database/item?namespace=" + LANE_NS)["result"]["value"] or {}
    except urllib.error.HTTPError as e:
        if e.code != 404:   # 404: the namespace does not exist yet
            raise
        have = {}
    for key, entry in want.items():
        if have.get(key) != entry:
            _call(moonraker, "POST", "/server/database/item", {"namespace": LANE_NS, "key": key, "value": entry})
    for key in OWN_KEYS:
        if key in have and key not in want:
            _call(moonraker, "DELETE", "/server/database/item?namespace=%s&key=%s" % (LANE_NS, key))
    return want


def sync_loop(moonraker, interval):
    last, failing = None, False
    while True:
        try:
            last = sync_once(moonraker, last)
            if failing:
                print("slicer sync: working again")
            failing = False
        except Exception as e:  # Klipper not ready, Moonraker restarting ...
            if not failing:
                print("slicer sync: paused (%s)" % e)
            failing = True
        time.sleep(interval)


def restart_on_update(interval=10.0):
    """Restart this server when an update replaces its code. The update panel
    restarts only Klipper, so a page service of its own (vrtx-<NAME>) would
    otherwise keep running the old code until the computer restarts."""
    me = os.path.abspath(__file__)
    try:
        start = os.stat(me).st_mtime
    except OSError:
        return
    while True:
        time.sleep(interval)
        try:
            if os.stat(me).st_mtime != start:
                time.sleep(2)  # let the update finish writing
                print("VRTX page: code updated, restarting")
                sys.stdout.flush()
                os.execv(sys.executable, [sys.executable, me] + sys.argv[1:])
        except OSError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--moonraker", default="http://127.0.0.1:7125")
    ap.add_argument("--no-slicer-sync", action="store_true",
                    help="do not copy the tool filaments into Moonraker's lane_data")
    ap.add_argument("--sync-interval", type=float, default=5.0)
    a = ap.parse_args()
    if not a.no_slicer_sync:
        threading.Thread(target=sync_loop, args=(a.moonraker.rstrip("/"), a.sync_interval),
                         daemon=True).start()
    threading.Thread(target=restart_on_update, daemon=True).start()
    srv = http.server.ThreadingHTTPServer((a.bind, a.port),
                                          make_handler(a.moonraker.rstrip("/")))
    print("VRTX page on port %d, Moonraker at %s" % (a.port, a.moonraker))
    srv.serve_forever()


if __name__ == "__main__":
    main()
