#!/usr/bin/env python3
"""Rewind plugin end-to-end probe (the rebuilt expanded view + the card button).

Written for the rewind plugin (docs/rewind-plugin-plan.md, P1-P7). Runs a
throwaway `rushi-web` against a purpose-built fixture session
(`/tmp/rw-e2e/sessions/rewindprobe`) and drives a real Chromium over CDP.

Fixture (12 lines; line number = the kernel's `seq`):

    1  user_message "round one ..."      -> A  (retracted by the marker at 11)
    2  assistant_message
    3  tool_call
    4  user_message "round two ..."      -> B
    5  assistant_message
    6  user_message "round three ..."    -> C  (abandoned by the rewind at 8)
    7  assistant_message
    8  rewind target 4 mode on           -> fork: the cursor returns to B
    9  user_message "round three bis"    -> D  (active, current)
    10 assistant_message
    11 user_message_retract target m1
    12 compaction_summary first_kept 1

Checks:
  P2/P3  the expand chevron opens the full-window History view: #sidebar and
         #main are display:none, #history-view is laid out, the rail carries
         the session and stays in-view.
  P4     the tree: 4 nodes, exactly one abandoned (C), one current (D), the
         round/event/time tooltips, the legend, the boundary footnote.
  P5     a node click opens "Rewind to this point?" (Cancel / Rewind, the C3
         copy, no "irreversible" claim); Cancel writes nothing; Rewind appends
         `{"type":"rewind","target_seq":<node>,"mode":"on"}` and the live tree
         moves the current marker (fork), and re-entering the abandoned branch
         (e2e case C) works the same way.
  P6     the card quick button exists on every user card, is inert on the
         current tail, and opens the same dialog.
  C1     `#plugin-area` lists the rewind plugin and its panel summarizes the
         tree + opens the History view.
  MUT    with a live `loop.pid` (the server's loop truth) the tree is
         read-only: every node carries `.locked`, no click opens the dialog,
         the footer says why, and the card buttons are disabled.

Local-only diagnostic (e2e/ is gitignored). Usage:
    python3 e2e/rewind_probe.py [port]
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from model_panel_probe import Cdp, ws_connect  # noqa: E402

HOST = "127.0.0.1"
ROOT = "/tmp/rw-e2e/sessions"
SESSION = "rewindprobe"
CHROME = os.path.expanduser(
    "~/Library/Caches/ms-playwright/chromium-1148/chrome-mac/Chromium.app/Contents/MacOS/Chromium")
PROF = "/tmp/rushi-rewind-probe-profile"
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The probe lives in rushi-webui/e2e/ upstream and in the packaged
# rushi-rewind/e2e/ mirror; resolve the server binary and the kernel config
# from either location (RUSHI_WEB_BIN overrides).
_ROOT = os.path.dirname(HERE)
_CANDIDATES = [os.environ.get("RUSHI_WEB_BIN"), 
               os.path.join(HERE, "target", "debug", "rushi-web"),
               os.path.join(_ROOT, "rushi-webui", "target", "debug", "rushi-web")]
WEB_BIN = next((c for c in _CANDIDATES if c and os.path.exists(c)), _CANDIDATES[1])
CONFIG = os.path.join(_ROOT, "rushi", "config.toml")

FIXTURE = [
    {"v": 1, "type": "user_message", "ts": "2026-10-02T01:00:00Z", "id": "m1",
     "content": "round one \u2014 where should the parser live?"},
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T01:00:01Z",
     "content": "in the kernel crate"},
    {"v": 1, "type": "tool_call", "ts": "2026-10-02T01:00:02Z", "id": "c1",
     "name": "bash", "arguments": {}},
    {"v": 1, "type": "user_message", "ts": "2026-10-02T01:00:03Z", "id": "m2",
     "content": "round two \u2014 add the fixture tests"},
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T01:00:04Z",
     "content": "tests added"},
    {"v": 1, "type": "user_message", "ts": "2026-10-02T01:00:05Z", "id": "m3",
     "content": "round three \u2014 now the docs"},
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T01:00:06Z",
     "content": "docs v1"},
    {"v": 1, "type": "rewind", "ts": "2026-10-02T01:00:07Z", "target_seq": 4,
     "mode": "on", "reason": "webui_pick"},
    {"v": 1, "type": "user_message", "ts": "2026-10-02T01:00:08Z", "id": "m4",
     "content": "round three bis \u2014 the docs can wait"},
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T01:00:09Z", "content": "ok"},
    {"v": 1, "type": "user_message_retract", "ts": "2026-10-02T01:00:10Z", "target": "m1"},
    {"v": 1, "type": "compaction_summary", "ts": "2026-10-02T01:00:11Z",
     "first_kept_seq": 1, "summary": "early rounds folded"},
]

# v0.5.56: the History rail renders the dispatch-view session cards grouped by
# the session's **working path** (its `cwd` marker). Two fixtures get a (real)
# project directory, the third gets none — the "(no project)" bucket. The dirs
# live OUTSIDE the sessions root (the server lists every subdirectory of it as
# a session).
PROJ_BASE = os.path.join(os.path.dirname(ROOT), "projects")
PROJ_A = os.path.join(PROJ_BASE, "alpha-project")
PROJ_B = os.path.join(PROJ_BASE, "beta-project")

# A second session: the rail must switch sessions in-view (C2) and refetch.
FIXTURE2 = [
    {"v": 1, "type": "user_message", "ts": "2026-10-02T02:00:00Z", "id": "n1",
     "content": "second session, first round"},
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T02:00:01Z", "content": "ok"},
    {"v": 1, "type": "user_message", "ts": "2026-10-02T02:00:02Z", "id": "n2",
     "content": "second session, second round"},
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T02:00:03Z", "content": "ok"},
]
SESSION2 = "rewindprobe2"
# A session with no user message at all: the "no rounds yet" empty state.
FIXTURE3 = [
    {"v": 1, "type": "assistant_message", "ts": "2026-10-02T03:00:00Z",
     "content": "an orphan assistant line, no user message"},
]
SESSION3 = "rewindprobe3"

C3_NOTE = ("The active conversation resumes from here. Everything after it moves to "
           "an abandoned branch")
LOCKED_HINT = "rewind is disabled while the loop is running"
IDLE_HINT = "click a node to rewind"


def log(*a):
    print(*a, flush=True)


class Res:
    """Tiny assertion bookkeeper: one line per check, a PASS/FAIL verdict."""

    def __init__(self):
        self.fails = []
        self.n = 0

    def check(self, name, cond, detail=""):
        self.n += 1
        print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"  <- {detail}"),
              flush=True)
        if not cond:
            self.fails.append(name)
        return cond


def write_fixture():
    for name, evs in ((SESSION, FIXTURE), (SESSION2, FIXTURE2), (SESSION3, FIXTURE3)):
        d = os.path.join(ROOT, name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            for ev in evs:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        for junk in ("loop.pid", "loop.last"):
            p = os.path.join(d, junk)
            if os.path.exists(p):
                os.remove(p)
    # the working-path markers: SESSION -> alpha, SESSION2 -> beta,
    # SESSION3 -> none (the "(no project)" group).
    for name, cwd in ((SESSION, PROJ_A), (SESSION2, PROJ_B), (SESSION3, None)):
        p = os.path.join(ROOT, name, ".cwd")
        if cwd:
            os.makedirs(cwd, exist_ok=True)
            with open(p, "w") as f:
                f.write(cwd + "\n")
        elif os.path.exists(p):
            os.remove(p)


def log_lines():
    with open(os.path.join(ROOT, SESSION, "events.jsonl")) as f:
        return [l for l in f.read().splitlines() if l.strip()]


def start_server(port):
    proc = subprocess.Popen(
        [WEB_BIN, "--host", HOST, "--port", str(port), "--sessions-root", ROOT,
         "--config", CONFIG],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            with urllib.request.urlopen(f"http://{HOST}:{port}/api/sessions", timeout=1):
                return proc
        except Exception:
            time.sleep(0.25)
    proc.terminate()
    raise SystemExit("FAIL: rushi-web did not come up")


def get_json(port, path):
    with urllib.request.urlopen(f"http://{HOST}:{port}{path}", timeout=5) as r:
        return json.load(r)


# ── JS snippets ────────────────────────────────────────────────────
TREE = """(function(){
  const q = s => document.querySelector(s);
  const app = q('#app'), sb = q('#sidebar'), main = q('#main'), hv = q('#history-view');
  const nodes = [...document.querySelectorAll('#hist-tree .rw-node')];
  const cur = q('#hist-tree .rw-node.current');
  const r = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return {w: Math.round(b.width), h: Math.round(b.height)}; };
  const nm = c => ((c.querySelector('.dc-name')||{}).innerText||'').trim();
  return JSON.stringify({
    appClass: app ? app.className : null,
    sidebarDisplay: sb ? getComputedStyle(sb).display : null,
    mainDisplay: main ? getComputedStyle(main).display : null,
    view: !!hv, viewBox: r(hv),
    rail: [...document.querySelectorAll('#hist-rail .dispatch-card')].map(c=>nm(c)),
    railOn: [...document.querySelectorAll('#hist-rail .dispatch-card.active')].map(c=>nm(c)),
    // v0.5.56: the rail IS the M7 dispatch view — one .dispatch-group per
    // working path, .dispatch-card per session, each with its ▶/■ loop toggle.
    groups: [...document.querySelectorAll('#hist-rail .dispatch-group')].map(g=>({
      // v0.5.57: the visible label is `.dispatch-group-name` (basename or the
      // user's alias) and its `title` is the FULL working path.
      label: (((g.querySelector('.dispatch-group-name')||{}).textContent||'').trim()
              || ((g.querySelector('.dispatch-group-input')||{}).value||'')),
      labelShown: (((g.querySelector('.dispatch-group-name')||{}).innerText||'').trim()),
      count: Number(((g.querySelector('.dispatch-group-count')||{}).innerText||'0')),
      title: (g.querySelector('.dispatch-group-head')||{}).title||'',
      nameTitle: (g.querySelector('.dispatch-group-name')||{}).title||'',
      pencil: !!g.querySelector('.dispatch-group-edit'),
      input: !!g.querySelector('.dispatch-group-input'),
      inputVal: (g.querySelector('.dispatch-group-input')||{}).value||'',
      cards: [...g.querySelectorAll('.dispatch-card')].map(c=>nm(c)),
      tog: [...g.querySelectorAll('.dispatch-card .qa')].map(b=>
        ({txt:b.innerText.trim(), cls:b.className, title:b.title}))})),
    cards: [...document.querySelectorAll('#hist-rail .dispatch-card')].map(c=>({
      name: nm(c), cls: c.className,
      time: ((c.querySelector('.dc-time')||{}).innerText||''),
      menu: !!c.querySelector('.sess-more'),
      go: !!c.querySelector('.qa.qa-go'), stop: !!c.querySelector('.qa.qa-stop')})),
    nodes: nodes.map(n=>({seq:n.dataset.seq, cls:n.className,
      round:(n.querySelector('.rw-round')||{}).innerText||'',
      sum:(n.querySelector('.rw-sum')||{}).innerText||'',
      meta:(n.querySelector('.rw-meta')||{}).innerText||'',
      tip:(n.querySelector('.rw-head')||{}).title||'',
      retract: !!n.querySelector('.rw-badge-retract'),
      here: !!n.querySelector(':scope > .rw-head .rw-here')})),
    abandoned: document.querySelectorAll('#hist-tree .rw-node.abandoned').length,
    locked: document.querySelectorAll('#hist-tree .rw-node.locked').length,
    current: cur ? {seq:cur.dataset.seq, sum:(cur.querySelector('.rw-sum')||{}).innerText||''} : null,
    legend: (q('.rw-legend')||{}).innerText||'',
    foot: (q('#hist-foot')||{}).innerText||'',
    boundaries: [...document.querySelectorAll('.rw-boundary')].map(x=>x.innerText),
    lamp: !!q('#hist-top .rw-lamp.running'),
    treeScroll: hv ? getComputedStyle(q('#hist-tree')).overflowY : null,
  });
})()"""

DIALOG = """(function(){
  const d = document.querySelector('#rw-dialog');
  if (!d) return JSON.stringify({open:false});
  const bs = [...d.querySelectorAll('.rw-actions .ns-btn')];
  return JSON.stringify({open:true,
    title: (d.querySelector('.rw-title')||{}).innerText||'',
    target: (d.querySelector('.rw-target')||{}).innerText||'',
    note: (d.querySelector('.rw-note')||{}).innerText||'',
    buttons: bs.map(b=>b.innerText.trim()),
    enabled: bs.map(b=>!b.disabled),
    chinese: /[\\u4e00-\\u9fa5]/.test(d.innerText)});
})()"""

QUICK = """(function(){
  const bs = [...document.querySelectorAll('#transcript .ev-user .ev-quickbtn')];
  return JSON.stringify({count: bs.length, disabled: bs.map(b=>b.disabled),
    titles: bs.map(b=>b.title),
    labels: [...document.querySelectorAll('#transcript .ev-user .ev-content')].map(x=>x.innerText.trim().slice(0,40))});
})()"""

THEME = """(function(){
  const hv = document.querySelector('#history-view'), tree = document.querySelector('#hist-tree');
  const sum = document.querySelector('.rw-node .rw-sum'), ic = document.querySelector('#hist-theme svg');
  const rgb = s => (s.match(/\\d+/g)||[]).map(Number);
  const lum = c => { const [r,g,b] = c.map(v => { v /= 255;
    return v <= 0.03928 ? v/12.92 : Math.pow((v+0.055)/1.055, 2.4); });
    return 0.2126*r + 0.7152*g + 0.0722*b; };
  const cs = getComputedStyle(hv), ss = getComputedStyle(sum);
  return JSON.stringify({
    theme: document.documentElement.dataset.theme,
    bg: cs.backgroundColor, fg: ss.color,
    contrast: Math.abs(lum(rgb(cs.backgroundColor)) - lum(rgb(ss.color))),
    treeW: Math.round(tree.getBoundingClientRect().width),
    icon: ic ? Math.round(ic.getBoundingClientRect().width) : 0,
  });
})()"""

PLUGIN = """(function(){
  const body = document.querySelector('#rewind-body');
  return JSON.stringify({
    bar: (document.querySelector('#plugin-list')||{}).innerText||'',
    body: !!body,
    meta: (document.querySelector('.rewind-meta')||{}).innerText||'',
    rows: [...document.querySelectorAll('.rewind-row')].map(x=>x.innerText.trim()),
    rowsHere: document.querySelectorAll('.rewind-row.current').length,
    openBtn: !!document.querySelector('#rewind-open-hist'),
    cap: getComputedStyle(document.querySelector('#plugin-area')).maxHeight,
  });
})()"""


def click_node(seq):
    return ("(function(){const n=document.querySelector('#hist-tree .rw-node[data-seq=\"" + str(seq)
            + "\"] .rw-head'); if(!n) return 'nf'; n.click(); return 'ok';})()")


def click_dialog(label):
    return ("(function(){const d=document.querySelector('#rw-dialog'); if(!d) return 'nf';"
            "const b=[...d.querySelectorAll('.rw-actions .ns-btn')].find(x=>x.innerText.trim()=="
            f"'{label}'); if(!b) return 'nf'; b.click(); return 'ok';}})()")


OPEN_HISTORY = ("(function(){if(document.querySelector('#history-view')) return 'already';"
                "const b=document.querySelector('#sidebar-expand'); if(!b) return 'nf';"
                "b.click(); return 'ok';})()")

OPEN_PLUGIN = ("(function(){const b=document.querySelector('#plugin-list'); if(!b) return 'nf';"
               "b.click(); return 'ok';})()")

PICK_REWIND_PLUGIN = ("(function(){const items=[...document.querySelectorAll('#plugin-menu .plugin-item')];"
                      "const b=items.find(x=>x.innerText.trim().toLowerCase()==='rewind');"
                      "if(!b) return 'nf'; b.click(); return 'ok';})()")

def click_rail(name):
    """Click one session's card in the History rail. The rail carries the M7
    dispatch cards (v0.5.56), so the match is on the card's `.dc-name` — an
    exact name, never a substring (`rewindprobe` is a prefix of
    `rewindprobe2`)."""
    return ("(function(){const b=[...document.querySelectorAll('#hist-rail .dispatch-card')]"
            ".find(x=>((x.querySelector('.dc-name')||{}).innerText||'').trim()==="
            f"'{name}'); if(!b) return 'nf'; b.click(); return 'ok';}})()")


SELECT_SESSION = ("(function(){const items=[...document.querySelectorAll('#session-list .session-item')];"
                  "const it=items.find(x=>x.innerText.includes('" + SESSION + "'));"
                  "if(!it) return 'nf'; it.click(); return 'ok';})()")


def wait_for(c, expr, want, timeout=8.0, poll=0.25):
    """Poll `expr` (a JS boolean/JSON) until `want(value)` is true."""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = c.ev(expr)
        try:
            v = json.loads(last)
        except Exception:
            v = last
        if want(v):
            return v
        time.sleep(poll)
    return last


def open_browser(port):
    sock = socket.socket()
    sock.bind((HOST, 0))
    cdp_port = sock.getsockname()[1]
    sock.close()
    if os.path.isdir(PROF):
        shutil.rmtree(PROF, ignore_errors=True)
    proc = subprocess.Popen([CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
                             f"--remote-debugging-port={cdp_port}", f"--user-data-dir={PROF}",
                             "--window-size=1400,1000", f"http://{HOST}:{port}/"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ws_url = None
    deadline = time.time() + 30
    while time.time() < deadline and not ws_url:
        try:
            with urllib.request.urlopen(f"http://{HOST}:{cdp_port}/json", timeout=2) as r:
                for t in json.load(r):
                    if t.get("type") == "page" and f"127.0.0.1:{port}" in t.get("url", ""):
                        ws_url = t.get("webSocketDebuggerUrl")
                        break
        except Exception:
            pass
        time.sleep(0.5)
    if not ws_url:
        proc.terminate()
        raise SystemExit("FAIL: no CDP target")
    s, buf = ws_connect(ws_url)
    return proc, Cdp(s, buf)


def reload_page(c):
    c.ev("location.reload(); 'ok'")
    time.sleep(2.5)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8491
    if not os.path.exists(WEB_BIN):
        print(f"FAIL: {WEB_BIN} missing (cargo build -p rushi-web)"); return 1
    write_fixture()
    server = start_server(port)
    res = Res()
    tree0 = get_json(port, f"/api/sessions/{SESSION}/rewind")
    res.check("server projection: 4 rounds, current 9, C abandoned",
              tree0["total_rounds"] == 4 and tree0["current_seq"] == 9
              and tree0["rewinds"][0]["target_seq"] == 4, json.dumps(tree0)[:120])

    chrome, c = open_browser(port)
    sleep_pid = None
    try:
        # ── boot: the transcript is up, the session is selectable ─────
        ok = wait_for(c, "!!document.querySelector('#session-list .session-item')", lambda v: v)
        res.check("shell booted (session list rendered)", ok)
        c.ev(SELECT_SESSION)
        ok = wait_for(c, "document.querySelectorAll('#transcript .ev-user .ev-quickbtn').length",
                      lambda v: isinstance(v, int) and v == 4)
        res.check("P6: quick button on every user card (4)", ok, f"got {ok}")

        quick = json.loads(c.ev(QUICK))
        res.check("P6: the newest card (current tail, seq 9) is inert",
                  quick["disabled"] == [False, False, False, True], json.dumps(quick["disabled"]))
        res.check("P6: enabled cards carry the rewind tooltip",
                  all("rewind to this message" == t for t in quick["titles"][:3]), str(quick["titles"]))
        res.check("P6: the tail card says why it is inert",
                  quick["titles"][3] == "you are already here", str(quick["titles"][3]))

        # The card button opens the same dialog (Cancel writes nothing).
        before = len(log_lines())
        c.ev("document.querySelectorAll('#transcript .ev-user .ev-quickbtn')[0].click(); 'ok'")
        d = wait_for(c, DIALOG, lambda v: isinstance(v, dict) and v.get("open"), timeout=4)
        d = json.loads(d) if isinstance(d, str) else d
        res.check("P6: the card button opens the confirm dialog", d.get("open"), str(d))
        res.check("dialog title is 'Rewind to this point?'", d.get("title") == "Rewind to this point?", str(d.get("title")))
        res.check("dialog body is the C3 copy", C3_NOTE in d.get("note", ""), str(d.get("note"))[:80])
        res.check("dialog claims nothing irreversible",
                  "irrevers" not in d.get("note", "").lower() and "cannot" not in d.get("note", "").lower())
        res.check("dialog buttons are Cancel / Rewind", d.get("buttons") == ["Cancel", "Rewind"], str(d.get("buttons")))
        res.check("dialog text is English", d.get("chinese") is False)
        res.check("dialog target names the card's round",
                  "round one" in d.get("target", ""), str(d.get("target")))
        c.ev(click_dialog("Cancel"))
        time.sleep(0.6)
        res.check("Cancel closes the dialog", not json.loads(c.ev(DIALOG))["open"])
        res.check("Cancel writes nothing", len(log_lines()) == before,
                  f"{before} -> {len(log_lines())}")

        # ── C1: the plugin area lists + summarizes the plugin ─────────
        c.ev(OPEN_PLUGIN)
        time.sleep(0.4)
        pick = c.ev(PICK_REWIND_PLUGIN)
        time.sleep(0.8)
        p = json.loads(c.ev(PLUGIN))
        res.check("C1: `rewind` is offered in the plugin menu", pick == "ok", str(pick))
        res.check("C1: the panel renders the tree summary",
                  p["body"] and "4 rounds" in p["meta"].lower()
                  and "1 abandoned" in p["meta"].lower(), p["meta"])
        rows = [r.lower() for r in p["rows"]]
        res.check("C1: the panel lists the active path (3 rounds, active only)",
                  len(rows) == 3 and "r1" in rows[0] and "round one" in rows[0]
                  and "r2" in rows[1] and "round two" in rows[1]
                  and "r4" in rows[2] and "round three bis" in rows[2], str(rows))
        res.check("C1: the panel marks the current round", p["rowsHere"] == 1, str(p["rowsHere"]))
        res.check("C1: the plugin panel keeps the 30% cap", p["cap"] == "30%", p["cap"])

        # ── P3: the expand chevron opens the full-window History view ─
        res.check("P3: expand chevron", c.ev(OPEN_HISTORY) == "ok")
        t = wait_for(c, TREE, lambda v: isinstance(v, dict) and len(v.get("nodes", [])) == 4)
        t = json.loads(t) if isinstance(t, str) else t
        res.check("P3: #history-view is mounted and laid out",
                  t["view"] and t["viewBox"] and t["viewBox"]["w"] > 600 and t["viewBox"]["h"] > 400,
                  str(t.get("viewBox")))
        res.check("P3: layout-full hides the sidebar + transcript",
                  "layout-full" in (t["appClass"] or "") and t["sidebarDisplay"] == "none"
                  and t["mainDisplay"] == "none", f"{t['appClass']} {t['sidebarDisplay']}/{t['mainDisplay']}")
        res.check("P3: the rail keeps the session (C2) and stays in-view",
                  t["railOn"] == [SESSION] and len(t["groups"]) >= 1, str(t["railOn"]))
        res.check("P3: the tree is the scroller", t["treeScroll"] == "auto", str(t["treeScroll"]))

        # ── R1 (v0.5.56): the rail = the dispatch cards, grouped by
        #    working path (`cwd` marker), each with its loop start/stop ──
        gl = {g["label"].strip(): g for g in t["groups"]}
        res.check("R1: one group per working path (+ the no-project bucket)",
                  sorted(gl) == ["(no project)", "alpha-project", "beta-project"],
                  str(sorted(gl)))
        res.check("R1: the group head labels the path and counts its cards",
                  all(g["count"] == len(g["cards"]) == 1 for g in t["groups"]),
                  str([(g["label"], g["count"], g["cards"]) for g in t["groups"]]))
        res.check("R1: the basename's tooltip is the full working path",
                  gl["alpha-project"]["nameTitle"] == PROJ_A
                  and gl["beta-project"]["nameTitle"] == PROJ_B,
                  str({k: v["nameTitle"] for k, v in gl.items()}))
        res.check("R1: the cards land in their own project's group",
                  gl["alpha-project"]["cards"] == [SESSION]
                  and gl["beta-project"]["cards"] == [SESSION2]
                  and gl["(no project)"]["cards"] == [SESSION3],
                  str({k: v["cards"] for k, v in gl.items()}))
        res.check("R1: the rail card is the dispatch card (name/time/menu)",
                  sorted(cd["name"] for cd in t["cards"]) == sorted([SESSION, SESSION2, SESSION3])
                  and all(cd["time"] and cd["menu"] for cd in t["cards"]),
                  str(t["cards"]))
        res.check("R1: the selected card carries the dispatch 'active' accent",
                  any("active" in cd["cls"] for cd in t["cards"] if cd["name"] == SESSION),
                  str([cd["cls"] for cd in t["cards"]]))
        res.check("R2: every rail card has a loop toggle (▶ start while idle)",
                  len(t["cards"]) == 3 and all(cd["go"] and not cd["stop"] for cd in t["cards"]),
                  str(t["cards"]))
        # ── L1/L2 (v0.5.57): the group label — tooltip + display-only rename ──
        res.check("L1: every head's label carries the full path as its tooltip",
                  all(g["nameTitle"] == g["title"] if g["title"] else g["nameTitle"]
                      for g in t["groups"])
                  and gl["(no project)"]["nameTitle"] == "(no project)",
                  str([(g["label"], g["nameTitle"]) for g in t["groups"]]))
        res.check("L1: the M7 uppercase style is kept for a path's basename",
                  all(g["labelShown"] == g["label"].upper()
                      for g in t["groups"] if g["label"] != "(no project)"),
                  str([(g["label"], g["labelShown"]) for g in t["groups"]]))
        res.check("L2: every head offers the rename pencil",
                  all(g["pencil"] and not g["input"] for g in t["groups"]),
                  str([(g["label"], g["pencil"], g["input"]) for g in t["groups"]]))
        cwd_before = {x["name"]: x.get("cwd") for x in get_json(port, "/api/sessions")}
        lines_before = len(log_lines())
        open_edit = ("(function(){const g=[...document.querySelectorAll('#hist-rail .dispatch-group')]"
                     ".find(x=>{const n=x.querySelector('.dispatch-group-name');"
                     f"return n && n.title==='{PROJ_A}';}});"
                     "if(!g) return 'nf'; const b=g.querySelector('.dispatch-group-edit');"
                     "if(!b) return 'nobtn'; b.click(); return 'ok';})()")
        res.check("L2: the pencil opens the editor", c.ev(open_edit) == "ok")
        time.sleep(0.4)
        e = json.loads(c.ev(TREE))
        eg = [g for g in e["groups"] if g["input"]]
        res.check("L2: the label is swapped for an input, prefilled with the label",
                  len(eg) == 1 and eg[0]["inputVal"] == "alpha-project",
                  str([(g["label"], g["input"], g["inputVal"]) for g in e["groups"]]))
        res.check("L2: the input's placeholder is the real basename",
                  c.ev("(document.querySelector('#hist-rail .dispatch-group-input')||{}).placeholder")
                  == "alpha-project")
        res.check("L2: Escape cancels without touching the label",
                  c.ev("(function(){const i=document.querySelector('#hist-rail .dispatch-group-input');"
                       "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));"
                       "return 'ok';})()") == "ok")
        time.sleep(0.4)
        el = json.loads(c.ev(TREE))
        res.check("L2: after Escape the basename is back",
                  sorted(g["label"] for g in el["groups"])
                  == ["(no project)", "alpha-project", "beta-project"],
                  str([g["label"] for g in el["groups"]]))
        # now rename for real: pencil -> type -> Enter
        c.ev(open_edit)
        time.sleep(0.4)
        res.check("L2: commit with Enter",
                  c.ev("(function(){const i=document.querySelector('#hist-rail .dispatch-group-input');"
                       "if(!i) return 'nf'; i.value='core engine';"
                       "i.dispatchEvent(new Event('input',{bubbles:true}));"
                       "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
                       "return 'ok';})()") == "ok")
        time.sleep(0.5)
        t2 = json.loads(c.ev(TREE))
        l2 = {g["label"]: g for g in t2["groups"]}
        res.check("L2: the head now shows the custom label",
                  "core engine" in l2 and l2["core engine"]["count"] == 1
                  and l2["core engine"]["cards"] == [SESSION],
                  str([(g["label"], g["cards"]) for g in t2["groups"]]))
        res.check("L2: the custom label is shown as typed (uppercase dropped)",
                  l2.get("core engine", {}).get("labelShown") == "core engine",
                  str(l2.get("core engine", {}).get("labelShown")))
        res.check("L2: the tooltip still shows the REAL working path",
                  l2.get("core engine", {}).get("nameTitle") == PROJ_A,
                  str(l2.get("core engine", {}).get("nameTitle")))
        res.check("L2: the editor closed (back to a label)",
                  not l2.get("core engine", {}).get("input")
                  and len([g for g in t2["groups"] if g["input"]]) == 0)
        res.check("L2: the working path is untouched (server cwd unchanged)",
                  {x["name"]: x.get("cwd") for x in get_json(port, "/api/sessions")} == cwd_before,
                  str({x["name"]: x.get("cwd") for x in get_json(port, "/api/sessions")}))
        res.check("L2: renaming wrote no event and no marker",
                  len(log_lines()) == lines_before
                  and not os.path.exists(os.path.join(ROOT, SESSION, "loop.last")),
                  f"{lines_before} -> {len(log_lines())}")
        res.check("L2: the alias is persisted (localStorage)",
                  "core engine" in c.ev("localStorage.getItem('rushi-project-labels') || ''")
                  and PROJ_A in c.ev("localStorage.getItem('rushi-project-labels') || ''"))
        # it must survive a reload (the whole point of persisting it)
        reload_page(c)
        wait_for(c, "document.querySelectorAll('#hist-rail .dispatch-group').length",
                 lambda v: isinstance(v, int) and v >= 3)
        # a fresh page has no active session: re-enter it from the rail, so the
        # tree the later checks look at is back (the layout itself is persisted)
        c.ev(click_rail(SESSION))
        wait_for(c, "document.querySelectorAll('#hist-tree .rw-node').length", lambda v: v == 4)
        t3 = json.loads(c.ev(TREE))
        res.check("L2: the alias survives a page reload",
                  "core engine" in {g["label"] for g in t3["groups"]}
                  and [g["nameTitle"] for g in t3["groups"] if g["label"] == "core engine"] == [PROJ_A],
                  str([(g["label"], g["nameTitle"]) for g in t3["groups"]]))
        # and clearing it (empty value) restores the basename
        c.ev(open_edit)
        time.sleep(0.4)
        c.ev("(function(){const i=document.querySelector('#hist-rail .dispatch-group-input');"
             "if(!i) return 'nf'; i.value='';"
             "i.dispatchEvent(new Event('input',{bubbles:true}));"
             "i.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));"
             "return 'ok';})()")
        time.sleep(0.5)
        t4 = json.loads(c.ev(TREE))
        res.check("L2: clearing the label restores the basename",
                  sorted(g["label"] for g in t4["groups"])
                  == ["(no project)", "alpha-project", "beta-project"]
                  and [g["nameTitle"] for g in t4["groups"] if g["label"] == "alpha-project"]
                  == [PROJ_A],
                  str([(g["label"], g["nameTitle"]) for g in t4["groups"]]))
        res.check("L2: the cleared alias is dropped from localStorage",
                  "core engine" not in c.ev("localStorage.getItem('rushi-project-labels') || ''"))

        res.check("R2: the toggle is the dispatch 'start' button",
                  all(tg["txt"] == "\u25B6 start" and "qa-go" in tg["cls"]
                      and tg["title"] == "start / stop this session's loop"
                      for g in t["groups"] for tg in g["tog"]),
                  str([tg for g in t["groups"] for tg in g["tog"]]))

        # ── P4: the tree ─────────────────────────────────────────────
        res.check("P4: one node per round (4)", len(t["nodes"]) == 4, str(len(t["nodes"])))
        res.check("P4: exactly one abandoned branch (C)",
                  t["abandoned"] == 1 and any(n["seq"] == "6" and "abandoned" in n["cls"] for n in t["nodes"]),
                  str(t["abandoned"]))
        res.check("P4: the current node is D (seq 9)",
                  t["current"] and t["current"]["seq"] == "9" and "round three bis" in t["current"]["sum"],
                  str(t["current"]))
        res.check("P4: 'here' marker only on the current node",
                  [n["here"] for n in t["nodes"]] == [False, False, False, True],
                  str([n["here"] for n in t["nodes"]]))
        res.check("P4: the retracted round carries the badge",
                  [n["retract"] for n in t["nodes"]] == [True, False, False, False],
                  str([n["retract"] for n in t["nodes"]]))
        res.check("P4: node meta shows the folded event count",
                  t["nodes"][0]["meta"] == "3 events" and t["nodes"][3]["meta"] == "4 events",
                  str([n["meta"] for n in t["nodes"]]))
        tips_ok = all("round " in n["tip"] and "event" in n["tip"]
                      and ("active" in n["tip"] or "abandoned" in n["tip"]) for n in t["nodes"])
        res.check("P4: tooltips carry round/time/events/state", tips_ok,
                  str([n["tip"] for n in t["nodes"]]))
        res.check("P4: legend counts the branches + rewinds",
                  "abandoned (1)" in t["legend"].lower() and "1 rewind" in t["legend"].lower(),
                  t["legend"])
        res.check("P4: the compaction boundary is footnoted",
                  len(t["boundaries"]) == 1 and "line 12" in t["boundaries"][0], str(t["boundaries"]))
        # ── P3 accept: both themes stay clean + readable ─────────────
        th1 = json.loads(c.ev(THEME))
        res.check("P3: the top-bar theme icon is sized", th1["icon"] == 16, str(th1["icon"]))
        c.ev("document.querySelector('#hist-theme').click(); 'ok'")
        time.sleep(0.8)
        th2 = json.loads(c.ev(THEME))
        res.check("P3: the theme button flips the palette",
                  th2["theme"] != th1["theme"] and th2["bg"] != th1["bg"],
                  f"{th1['theme']}->{th2['theme']} {th1['bg']}->{th2['bg']}")
        res.check("P3: node text stays readable in both themes",
                  th1["contrast"] > 0.25 and th2["contrast"] > 0.25,
                  f"{round(th1['contrast'],3)}/{round(th2['contrast'],3)}")
        res.check("P3: the tree survives the theme switch", th2["treeW"] > 400, str(th2["treeW"]))
        c.ev("document.querySelector('#hist-theme').click(); 'ok'")  # back to the first mode
        time.sleep(0.4)

        res.check("P3: idle footer hint", IDLE_HINT in t["foot"], t["foot"])

        # ── C2: the rail switches sessions in-view (rail + tree reload) ─
        c.ev(click_rail(SESSION2))
        sw = wait_for(c, TREE, lambda v: isinstance(v, dict) and v.get("railOn") == [SESSION2]
                      and len(v.get("nodes", [])) == 2, timeout=8)
        sw = json.loads(sw) if isinstance(sw, str) else sw
        res.check("C2: the rail moves the selection", sw["railOn"] == [SESSION2], str(sw["railOn"]))
        res.check("C2: the tree reloads for the new session (2 rounds)",
                  len(sw["nodes"]) == 2 and all(n["seq"] in ("1", "3") for n in sw["nodes"]),
                  str([n["seq"] for n in sw["nodes"]]))
        title = c.ev("(document.querySelector('#hist-title .hist-sess')||{}).innerText")
        res.check("C2: the title follows the session", title == SESSION2, str(title))
        res.check("C2: the view stays full-window",
                  "layout-full" in (c.ev("document.querySelector('#app').className") or ""))
        c.ev(click_rail(SESSION))
        back = wait_for(c, TREE, lambda v: isinstance(v, dict) and len(v.get("nodes", [])) == 4
                        and v.get("railOn") == [SESSION], timeout=8)
        back = json.loads(back) if isinstance(back, str) else back
        res.check("C2: switching back restores the 4-round tree",
                  len(back["nodes"]) == 4 and back["current"]["seq"] == "9", str(back.get("current")))

        # ── empty states (§3): a session with no round ───────────────
        c.ev(click_rail(SESSION3))
        empty = wait_for(c, "JSON.stringify({t:(document.querySelector('#hist-tree .rw-empty')||{})"
                            ".innerText||'', locked:document.querySelectorAll('#hist-tree .rw-node').length})",
                         lambda v: isinstance(v, dict) and v.get("t", "").startswith("no rounds yet"), timeout=8)
        empty = json.loads(empty) if isinstance(empty, str) else empty
        res.check("empty state: 'no rounds yet' for a session without a round",
                  empty.get("t", "").startswith("no rounds yet") and empty.get("locked") == 0,
                  str(empty))
        c.ev(click_rail(SESSION))
        wait_for(c, TREE, lambda v: isinstance(v, dict) and len(v.get("nodes", [])) == 4
                 and v.get("railOn") == [SESSION], timeout=8)

        # ── P5: node click -> dialog -> rewind writes mode "on" ───────
        res.check("P5: node click", c.ev(click_node(6)) == "ok")
        time.sleep(0.6)
        d = json.loads(c.ev(DIALOG))
        res.check("P5: node click opens the dialog", d.get("open"), str(d))
        res.check("P5: the dialog names the clicked round",
                  "round three \u2014 now the docs" in d.get("target", ""), str(d.get("target")))
        res.check("P5: Rewind is enabled while idle", d.get("enabled") == [True, True], str(d.get("enabled")))
        # Cancel leaves the log alone, then confirm for real.
        c.ev(click_dialog("Cancel"))
        time.sleep(0.5)
        res.check("P5: cancel = no write", len(log_lines()) == before)

        c.ev(click_node(6))
        time.sleep(0.5)
        res.check("P5: confirm", c.ev(click_dialog("Rewind")) == "ok")
        time.sleep(1.2)
        lines = log_lines()
        last = json.loads(lines[-1])
        res.check("P5: the appended line targets seq 6 with mode 'on'",
                  last.get("type") == "rewind" and last.get("target_seq") == 6 and last.get("mode") == "on",
                  json.dumps(last))
        res.check("P5: the dialog closed after the write", not json.loads(c.ev(DIALOG))["open"])
        # Live refetch (ws.rs bumps rewind_gen on the rewind frame).
        t2 = wait_for(c, TREE, lambda v: isinstance(v, dict)
                      and v.get("current", {}) and v["current"].get("seq") == "6", timeout=6)
        t2 = json.loads(t2) if isinstance(t2, str) else t2
        res.check("P5: the live tree moves 'current' to C", t2["current"]["seq"] == "6", str(t2.get("current")))
        res.check("P5: the old branch (D) is now abandoned",
                  any(n["seq"] == "9" and "abandoned" in n["cls"] for n in t2["nodes"]),
                  str([n["cls"] for n in t2["nodes"]]))
        api2 = get_json(port, f"/api/sessions/{SESSION}/rewind")
        res.check("P5: the server agrees (current_seq 6, settled)",
                  api2["current_seq"] == 6 and api2["settled"] is True, str(api2["current_seq"]))

        # ── e2e case C: re-entering the abandoned branch ─────────────
        res.check("case C: re-enter the abandoned branch", c.ev(click_node(9)) == "ok")
        time.sleep(0.5)
        c.ev(click_dialog("Rewind"))
        time.sleep(1.2)
        lines = log_lines()
        last = json.loads(lines[-1])
        res.check("case C: the second marker targets seq 9 with mode 'on'",
                  last.get("target_seq") == 9 and last.get("mode") == "on", json.dumps(last))
        t3 = wait_for(c, TREE, lambda v: isinstance(v, dict)
                      and v.get("current", {}) and v["current"].get("seq") == "9", timeout=6)
        t3 = json.loads(t3) if isinstance(t3, str) else t3
        res.check("case C: 'current' returns to D", t3["current"]["seq"] == "9", str(t3.get("current")))
        res.check("case C: C is abandoned again (all history kept)",
                  any(n["seq"] == "6" and "abandoned" in n["cls"] for n in t3["nodes"])
                  and len(t3["nodes"]) == 4, str([n["cls"] for n in t3["nodes"]]))
        res.check("case C: the log is append-only (14 lines)",
                  len(lines) == 14, f"{len(lines)} lines")

        # ── the loop-running guard (decision 5) ──────────────────────
        write_fixture()  # back to the canonical 12 lines
        # start_new_session: the fake loop is its own process-group leader, so
        # the server's `kill(-pid, SIGKILL)` finds it — which is what lets the
        # rail's ■ stop button be tested for real further down.
        sleeper = subprocess.Popen(["sleep", "600"], start_new_session=True)
        sleep_pid = sleeper.pid
        with open(os.path.join(ROOT, SESSION, "loop.pid"), "w") as f:
            f.write(str(sleep_pid))
        time.sleep(1.0)
        res.check("server sees a live loop for the fixture",
                  SESSION in get_json(port, "/api/loops")["running"],
                  str(get_json(port, "/api/loops")))
        reload_page(c)
        wait_for(c, "document.querySelectorAll('#hist-rail .dispatch-card').length", lambda v: isinstance(v, int) and v >= 1)
        rail_click = c.ev(click_rail(SESSION))
        res.check("C2: the rail switches the session in-view", rail_click == "ok", str(rail_click))
        tl = wait_for(c, TREE, lambda v: isinstance(v, dict) and len(v.get("nodes", [])) == 4)
        tl = json.loads(tl) if isinstance(tl, str) else tl
        res.check("guard: the lamp is lit", tl["lamp"], str(tl["lamp"]))
        res.check("guard: every node is locked", tl["locked"] == 4, str(tl["locked"]))
        res.check("guard: the footer says why", tl["foot"] == LOCKED_HINT, tl["foot"])
        res.check("guard: node tooltips say why",
                  all("disabled while the loop is running" in n["tip"] for n in tl["nodes"]),
                  str([n["tip"] for n in tl["nodes"]]))
        c.ev(click_node(6))
        time.sleep(0.8)
        res.check("guard: a locked node opens nothing", not json.loads(c.ev(DIALOG))["open"])
        res.check("guard: the log is untouched", len(log_lines()) == 12, str(len(log_lines())))
        c.ev("(function(){const b=document.querySelector('#hist-back'); if(b) b.click(); return 'ok';})()")
        time.sleep(1.0)
        q = json.loads(c.ev(QUICK))
        res.check("guard: the card buttons are disabled", q["count"] == 4 and all(q["disabled"]),
                  str(q["disabled"]))
        res.check("guard: the disabled cards say why",
                  all(t == LOCKED_HINT for t in q["titles"]), str(q["titles"]))

        # ── R2 (v0.5.56): the rail's loop toggle, driven for real ────
        # The fixture's `loop.pid` holds a live setsid sleeper, so the server
        # reports the session as running and the rail card must offer ■ stop.
        # (The guard checks above left the History view to drive the
        # transcript's own buttons, so step back in first.)
        res.check("R2: back into the History view", c.ev(OPEN_HISTORY) == "ok")
        tl = wait_for(c, TREE, lambda v: isinstance(v, dict) and len(v.get("nodes", [])) == 4
                      and v.get("locked") == 4)
        tl = json.loads(tl) if isinstance(tl, str) else tl
        res.check("R2: the running session's card offers ■ stop",
                  any(cd["name"] == SESSION and cd["stop"] and not cd["go"] for cd in tl["cards"]),
                  str(tl["cards"]))
        res.check("R2: the ■ stop button is the dispatch 'stop' button",
                  any(tg["txt"] == "\u25A0 stop" and "qa-stop" in tg["cls"]
                      and tg["title"] == "start / stop this session's loop"
                      for g in tl["groups"] for tg in g["tog"]),
                  str([tg for g in tl["groups"] for tg in g["tog"]]))
        click = c.ev("(function(){const b=[...document.querySelectorAll('#hist-rail .dispatch-card')]"
                     f".find(x=>((x.querySelector('.dc-name')||{{}}).innerText||'').trim()==='{SESSION}');"
                     "if(!b) return 'nf'; const q=b.querySelector('.qa'); if(!q) return 'noqa';"
                     "q.click(); return 'ok';})()")
        res.check("R2: click ■ stop", click == "ok", str(click))
        # v0.5.58 (b): the click POSTs /api/sessions/{id}/stop, the server
        # SIGKILLs the group, and the session must leave `running` **while the
        # fake loop is still an unreaped zombie** — that is the state a loop
        # started elsewhere (the TUI, a previous server instance) sits in, and
        # `kill(pid, 0)` alone calls it alive. We deliberately do NOT reap yet.
        gone = False
        for _ in range(32):
            if SESSION not in get_json(port, "/api/loops")["running"]:
                gone = True
                break
            time.sleep(0.25)
        res.check("R2: a zombie is not reported as a running loop (v0.5.58)",
                  gone and get_json(port, "/api/loops")["running"] == [],
                  str(get_json(port, "/api/loops")))
        # Proof that the server reached its verdict about a ZOMBIE: the child
        # is dead but nobody has reaped it, so its pid slot is still held —
        # exactly what `kill(pid, 0)` alone reports as "alive". (Checked with
        # signal 0, never with `poll()`: poll reaps.)
        pid_held = True
        try:
            os.kill(sleeper.pid, 0)
        except OSError:
            pid_held = False
        res.check("R2: that verdict was reached on an unreaped pid (signal 0 still succeeds)",
                  pid_held, "the child was already reaped")
        res.check("R2: /loop agrees (▶ start is available again)",
                  not get_json(port, f"/api/sessions/{SESSION}/loop").get("running"),
                  str(get_json(port, f"/api/sessions/{SESSION}/loop")))
        # now reap, and confirm nothing about the server state changes
        try:
            sleeper.wait(timeout=8)
        except Exception:
            pass
        res.check("R2: the fake loop process got the signal", sleeper.poll() is not None,
                  f"alive={sleeper.poll() is None}")
        res.check("R2: and the state is the same after reaping",
                  get_json(port, "/api/loops")["running"] == [],
                  str(get_json(port, "/api/loops")))
        # and the client comes back to life: ▶ start + clickable nodes again.
        reload_page(c)
        wait_for(c, "document.querySelectorAll('#hist-tree .rw-node').length", lambda v: v == 4)
        c.ev(click_rail(SESSION))
        tz = wait_for(c, TREE, lambda v: isinstance(v, dict) and len(v.get("nodes", [])) == 4
                      and v.get("locked") == 0)
        tz = json.loads(tz) if isinstance(tz, str) else tz
        res.check("R2: with the loop stopped the card is back to ▶ start",
                  any(cd["name"] == SESSION and cd["go"] and not cd["stop"] for cd in tz["cards"]),
                  str(tz["cards"]))
        res.check("R2: and the tree is clickable again",
                  tz["locked"] == 0 and IDLE_HINT in tz["foot"]
                  and all("disabled while the loop is running" not in n["tip"] for n in tz["nodes"]),
                  f"{tz['locked']} / {tz['foot']}")
        res.check("R2: stopping wrote no event (the guard is client-side only)",
                  len(log_lines()) == 12, str(len(log_lines())))

        log("")
        if res.fails:
            log(f"FAIL ({len(res.fails)}/{res.n} checks failed): " + ", ".join(res.fails))
            return 1
        log(f"PASS ({res.n} checks)")
        return 0
    finally:
        if sleep_pid:
            try:
                os.kill(sleep_pid, signal.SIGKILL)
            except Exception:
                pass
        p = os.path.join(ROOT, SESSION, "loop.pid")
        if os.path.exists(p):
            os.remove(p)
        chrome.terminate()
        server.terminate()


if __name__ == "__main__":
    sys.exit(main())
