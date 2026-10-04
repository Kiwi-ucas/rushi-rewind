# rushi-rewind

The **rewind plugin** for `rushi-webui` — the conversation history tree.

Rewinding to an earlier user message in `rushi-webui` throws nothing away:
every branch stays in the same append-only `events.jsonl`, and the abandoned
ones remain visible — and re-enterable — in a history tree. The kernel already
had `rewind` as a first-class event (`rushi/crates/rushi/src/rewind.rs`,
`bin/claim`) and `POST /api/sessions/{id}/rewind`; this plugin adds the
read-only projection, the UI, and the guard that forbids rewinding while the
session's loop runs.

## Why a repository

`rushi-webui` is the plugin's **development home** — the server module and the
Leptos module are compiled into the server binary and the WASM bundle there.
This repository **packages** the plugin:

* the two source modules as droppable files,
* the complete install map (`install/TOUCHPOINTS.md`: every file and anchor
  the plugin touches, with the exact snippets),
* the stylesheet (the upstream section + the additive rules, as one
  drop-in file),
* the design doc and the plan/record,
* the browser probe — `rushi-webui/e2e/` is **gitignored** upstream, so this
  repo is where the probe survives; it runs from either home (it resolves the
  server binary itself, `RUSHI_WEB_BIN` overrides). It assumes a server at
  **v0.5.58 or later**: the `■ stop` check kills a fake loop and requires the
  session to leave `/api/loops` while the process is still an unreaped zombie,
  which needs the zombie-aware liveness probe (`bin/rushi-web/src/process.rs`
  in the upstream tree, see its `docs/loop-lamp-reconnect.md` §v0.5.58),
* `scripts/sync-from-webui.sh`, which re-extracts all of the above from a
  `rushi-webui` checkout, verifies the integration touch points still exist,
  checks that the extracted stylesheet block still contains the plugin's
  newest rules (canaries, after a sub-banner once silently truncated it), and
  records the upstream revision in `UPSTREAM`.

```
rushi-rewind/
├─ README.md                     this file
├─ UPSTREAM                      the rushi-webui rev the mirrors came from
├─ server/rewind.rs              ← bin/rushi-web/src/rewind.rs
├─ client/rewind.rs              ← web-leptos/src/rewind.rs
├─ client/rewind.css             generated: the upstream section + the additive rules
├─ client/header.css             hand-maintained provenance header
├─ client/rewind-additive.css    hand-maintained additive rules (layout-full, icons, scrollbars)
├─ install/TOUCHPOINTS.md        the install map (file, anchor, snippet)
├─ docs/rewind-plugin.md         design: the projection ↔ kernel semantics mapping
├─ docs/rewind-plugin-plan.md    the P1–P7 plan + the implementation record
├─ e2e/rewind_probe.py          the CDP probe (140 assertions) — LOCAL, untracked
├─ e2e/flow_check.py            Style B's geometry checker, live server — LOCAL, untracked
├─ e2e/flow_style_b_probe.py    Style B's browser probe (60 checks) — LOCAL, untracked
├─ e2e/model_panel_probe.py     the shared CDP harness the probe imports — LOCAL, untracked
└─ scripts/sync-from-webui.sh   re-extract + verify + record the upstream rev
```

The `e2e/` python files are local verification tools: they are ignored by
git (an account-wide rule — no e2e python test files are uploaded from these
repositories) and are present only in a working tree that got them from
`rushi-webui/e2e/` via `scripts/sync-from-webui.sh`. Nothing else here depends
on them.

## Install into a rushi-webui checkout

Follow `install/TOUCHPOINTS.md`. In short:

1. `cp server/rewind.rs <webui>/bin/rushi-web/src/rewind.rs`
2. `cp client/rewind.rs <webui>/web-leptos/src/rewind.rs`
3. apply the 12 wiring edits (module decls, the route, the state slice, the
   two API calls, the mount points, the plugin registry entry, the dispatcher
   arm, the card button).
4. append `client/rewind.css` to `<webui>/web-leptos/style.css` (or load it
   as a second stylesheet after it).
5. `cargo build -p rushi-web` and `trunk build` in `web-leptos/`.

## Verify

```sh
# server unit tests (the projection against the kernel's own fixtures)
cd rushi-webui && cargo test -p rushi-web        # 71 passed (flow layout + the ring + node
                                                 # detail + the two v0.5.69 live rules)

# frontend
cd rushi-webui/web-leptos && trunk build          # clean

# Style B's flow geometry, against a live server and every session it sees
python3 e2e/flow_check.py 8480                          # 8 sessions, 0 problems
# ... and its browser surfaces (the switch, the 1:2 split, the scene, the
# panel, the dialog, the 117-round session, the orbital ring, the paint in
# both themes)
python3 e2e/flow_style_b_probe.py 8480                  # 68 checks, 0 failed
#   (A-I plus K: the list style on Webui's 117-round chain, no traps)

# the cone itself (v0.5.70, its full circle in v0.5.71): fixtures + server +
# the ray's geometry law
python3 e2e/orbit_probe.py 8480                         # 48 checks, 0 failed
#   (L1-L12: the law recomputed in Python against the painted rects to 3px,
#    the auto-fit to the panel's smaller half, nothing parked (no z push), a
#    whole 360 degrees spent on a real wheel returning every bead, the depth
#    dim `1 - 0.35 sin a`, the even fan (every fan is `360/k` apart, none
#    edge-on, the front's nudge rule), the trunk unmoved, click-to-align, hit-testing a
#    turned bead, the flat cos projection, reduced motion static, the zero-
#    and one-fork cases, a nested fork, no traps)
#   NOTE: v0.5.73's focus carousel (plan §15) supersedes this probe's angle
#   assertions — it pins the even-fan law the carousel replaces — so it is
#   kept as the v0.5.72 record and is not re-run. Use the two below.

# the focus carousel (v0.5.73, plan §15.6's S1-S6), on a live branchy
# session: one focus, phase 0, every unfocused root branch below the axis,
# the walk wrapping, the dots still 13px — and the residual bead pairs, as
# data
python3 e2e/carousel_probe.py 8480 rewind               # 0 failed checks
# the spine still ends on its branch's last bead after the nested shift
# that fixes K1 (plan §14.1)
python3 e2e/spine_probe.py 8480 rewind                  # 0.1px off, 99.7% along

# end-to-end (Chromium over CDP; builds its own fixtures and server)
cd rushi-webui && python3 e2e/rewind_probe.py [port]   # PASS (140 checks)
# ... or from this package (it finds ../rushi-webui/target/debug/rushi-web,
# or whatever RUSHI_WEB_BIN points at):
python3 e2e/rewind_probe.py [port]                     # PASS (140 checks)
```

The probe drives a real browser against three fixture sessions it writes to
`/tmp/rw-e2e/sessions/` and a throwaway `rushi-web`:

* `rewindprobe` — 12 lines, one fork at line 8 (C abandoned, D current),
  `.cwd` = `alpha-project`;
* `rewindprobe2` — a second session, `.cwd` = `beta-project` (the rail must be
  grouped by that path and switch sessions in-view);
* `rewindprobe3` — no round at all (the empty state), no `cwd` marker (the
  "(no project)" group);
* `rewindprobe4` — the rewind × compaction fixture (v0.5.61): a compaction
  boundary *inside* the span a later rewind abandons, a pick that would
  strand a tool pair, and a marker the kernel's projection ignores.

It asserts the rebuilt expanded view (layout, the rail **as the dispatch
cards** — grouped by working path, each with its ▶ start / ■ stop loop toggle,
scroller, both themes; and the group heads: the basename's tooltip is the full
working path, and the display-only rename survives a reload),
the recursive tree (abandoned / current / retracted / boundary / tooltips /
legend / the v0.5.61 `restore` annotation), the dialog copy, the appended
`{"type":"rewind","target_seq":N,"mode":"on"}` line, the live marker move,
**re-entering an abandoned branch** (the log stays append-only), the card `⟲`
button, the `#plugin-area` entry, the v0.5.61 guards (**the `409` refusal of a
pick the kernel would ignore — nothing written; the ignored-marker list and the
tail notice in the plugin area**), and the loop-running guard (with a real `loop.pid`: every node locked, no dialog, the
footer explains, the card buttons disabled) — ending with a **real ■ stop
click** in the rail: the fake loop is a setsid group leader, so the server's
`kill(-pid)` lands, and the check then sees `/api/loops` drop the session and
the card come back as ▶ start.

## Sync / provenance

```sh
scripts/sync-from-webui.sh [path-to-rushi-webui]     # default: ../rushi-webui
```

The script overwrites `server/`, `client/rewind.rs`, `docs/`, `e2e/` and the
generated `client/rewind.css`, checks the 18 touch points, and rewrites
`UPSTREAM`. Hand-maintained files (this README, `install/`, the two
`client/*.css` inputs) are never touched. If a touch point is gone, the script
fails with the file and the missing marker rather than shipping a stale
mirror — that is the signal to update `install/TOUCHPOINTS.md`.

**The cone (v0.5.70, and its full circle in v0.5.71).** A branch is no longer a
ribbon parallel to the trunk: it is a **straight ray** leaving its **parent's
bead**. Bead *i* of a branch sits at radius *i·q* (one *q* per round,
`q = r / longest branch`) while the x step stays one column per round, so a
branch leans out of the history line instead of shadowing it. Each branch is one
zero-size container on its parent's bead holding **one straight bar**
(`hypot()`/`atan2()` over the branch's own span and slope) and the beads that
branch owns; the per-branch ribbon (`.rw-fin`) and the per-edge elbow bars
(`.rw3-elbow`) are **deleted** — the user's rule was "every connector must be
a straight line with no bends" and "no background ribbon per branch". A fork
*off a branch* renders its container **inside** its parent's, so its line
starts on the parent's bead and fans relative to its parent (D-cone-6/7).

**v0.5.71 (D-cone-8..12)** turned that ray's angle into a **full circle**: it is
`mod(theta + 180, 360) - 180` instead of a ±60° clamp, so 360° of turning brings
every branch back to the pixel and it can be turned for ever — the **bounded
arc, its queue and its park** (a clamped branch pushed back in `z` and faded to
0.08, which left its parent's bead by up to 87px and shrank its dots to 2.2px)
are gone, and so is the one-branch `solo` swing. The auto-fit now fits the
*smaller* half of the panel (the axis sits in the middle, because a branch
reaches as far below the trunk as above it), the depth cue is a dim on the half
pointing away from the viewer (`1 − 0.35·sin a`), and the scene measures itself
**when it mounts** (a per-mount `ResizeObserver` on the scroller) — mounting the
History is not a reactive event, so the scene used to paint with no geometry at
all, which looked exactly like the old horizontal layout until something
re-measured it. The phase, the alignment and `.rw3-sheen` are unchanged; the
trunk is bit-identical. Three engine facts are load-bearing, all measured:
`hypot()`/`atan2()` are unusable with container units inside them (the cell and
the slope cross into CSS as px), `opacity < 1` flattens 3D children — so the dim
is a *value* on the leaves, and a nested bead counter-rotates and counter-scales
by its plane's **absolute** angle and its chain's **product** (written by the
render, because CSS cannot add an ancestor's variable to its own without a
cycle) — and CSS `mod()` works (negative arguments and custom-property chains
included), which is what keeps the whole wrap in `calc()`. Full record, the
three engine traps, the three defects live use found and the probe table:
**§13.6–§13.8 of `docs/rewind-plugin-plan.md`**, **§3b.6 of
`docs/rewind-plugin.md`**, `e2e/orbit_probe.py` (48 checks).

**v0.5.72 (D-fan-1..6, the user's own ask)** spreads each *fan* over the full
circle: the branches of one parent are `360/k` apart (three of them 120° each)
instead of the fixed 30° step that wedged them into 30/60/90 — measured on the
live session, branch beads landing on trunk beads went **7 → 1** and overlapping
bead pairs **8 → 2**. An angle now belongs to a **fan**: the server's `fin`
numbers every branch of the session (the live root fan holds `{0, 2, 3}`), so
each container carries its rank inside its own fan and the *render* works the
angle out and writes it in degrees (`--th`, plus `--rdeg` for the top ancestor,
which is what the beads' counter-rotation needs). `--step`/`--align`/`--sroot`
left the stylesheet; the selectors did not change, so this mirror's canaries
still hold. Two geometry facts came with it: `a = ±90°` is edge-on (the screen
offset is `−q·cos a`, so a branch there loses all of its projected length and
its beads land on the trunk row — measured 1.7px from a trunk bead), which a
0-based fan hits exactly when `k % 4 == 0`, so those fans are nudged half a
step (the aligned branch then sits at ±45°, not straight up); and a nested fan
is centred on 180° — opposite its parent's own ray, never along it — with its
spread capped at 120° and a quarter-step nudge in the few sizes where 180 ± 90
would reappear. A lone child sits straight below its parent, a lone branch
still points straight up, and the auto-fit is untouched (a branch's screen
offset never exceeds `q`, so the panel's smaller half still holds the whole
circle). The server was not touched: `orbit.step_deg` and `orbit.arc_deg` are
both unread now.

**v0.5.73 (the focus carousel, plan §15, D-snap-1..10)** stops the cone being a
*free-turning* fan and gives it **detents**. One wheel notch brings the **next
branch up** — rank order, wrapping `k → 0`, so a gesture is never a no-op
(v0.5.69's rule) — and the focused branch hangs at `0` with the rest spread over
the **lower arc**, inset `δ = 30°` so none is ever edge-on (the K2 class is
unreachable by construction). The fit turns **asymmetric** so the focused
branch has room: the axis drops to `0.68·h`, `q = (axis − margin − dot/2) /
longest` (**+42%**, 25.7 → 36.5px measured), and an unfocused branch rides
`--ql = --q · --kof` (`--kof = 0.82` live). Nothing scales an element — only
the step — so **every dot is still 13px**: exactly 13.00 on the focused branch
(`a = 0`), 11.7…13.2 on the others (the perspective). The walk is an
accumulator (`--turn`), a ~300 ms eased `Interval` per detent, instant under
`prefers-reduced-motion`, and it moves the **view only** — a click still
selects (D-snap-7). The wheel keeps a **pan fallback** on a fan of 0/1 branch
(every live session but `rewind`): the scroller is `overflow-x` only, so a
vertical delta has to go through the same proxy it always did.

Two findings came with it, both measured. **K1 (plan §14.1) is fixed by
construction**: a nested fan now starts **two steps out**, because K1 was the
exact, phase-independent coincidence `po == out` — both `(po − out)·q` and
`−(po − out)·q·sin a` cancel, so the child's bead sat on the trunk row at
*every* phase (0.0px at 24/24 sampled phases) and covered the trunk's round.
`out = po + 2` cannot cancel for any phase (44/29/29px clearance at the live
detents), and the spine's reach (`data-n`/`--n`) grows with it so it still ends
on its last bead (0.1px off, measured). And the **up-right tilt is not
shipped**: D-snap-9's `rotateZ(ψ)` was measured *wrong* — it rolls a branch's
own **column axis** too, so a 7-column branch's bead line gained `±7·cell·sin
ψ` (41px at 10°) of height and its far beads rose above the trunk row.
`CONE_ROLL_DEG` is 0 with the CSS machinery wired and inert; the right form is
a **shear of the radial direction only** (each step leans, the columns stay
horizontal), which stays open as D-slant.

Still open, on the record in **§15.9 of `docs/rewind-plugin-plan.md`**: the
**mirror pair** (8.3px between the live session's two one-bead lower branches —
they are mirror images on one column, and the *symmetric* arc is exactly what
keeps the walk jump-free, so it needs the staged relaxation's lateral DOF, the
D-snap-1b x-stagger, or acceptance); the tilt's shear form; and `k ≥ 4`, which
no live session here can exercise (every session but `rewind` has 0 or 1 root
branch). Verified live: the carousel probe's S1–S6 **0 failed** (one focus,
phase exactly 0, every unfocused root branch below the axis, the walk wrapping
`fin0 → fin2 → fin3 → fin0`), the spine probe 0.1px, `flow_style_b_probe 68/0`,
`flow_check 8/0`, `rewind_probe 140 PASS`, `cargo test -p rushi-web 71`. The old
`orbit_probe.py` (48 checks) is **superseded**: it pins the even-fan law this
replaces.

**Before that: the orbital ring (v0.5.68).** The scene was a **ring**: the
trunk stayed the horizontal axis and never turned, and every forking branch
was a **fin** — a plane hinged on that axis where it left, held out at an
auto-fitted radius, turned around the line by a ring angle the scene's own
scroll drove. Three fins fit the visible ±60° arc (30° apart) and the rest
queue at its ends, pushed back in depth and faded, so a session with many
forks keeps them all visible without giving up a screen row per branch. The
current branch sits at the front at rest; clicking a fin's bead brings its
branch round. (A lone branch swung ±25° instead of ringing until v0.5.71 made
it orbit like the rest.)
`prefers-reduced-motion` unfolds the whole ring onto rows *and* holds it
still. The implementation record (including five refinements the plan
missed), the measurements and the laws the probe recomputes are **§12.9 of
`docs/rewind-plugin-plan.md`**, **§3b.6 of `docs/rewind-plugin.md`**, and
`e2e/orbit_probe.py`.

**Two live defects fixed (v0.5.69).** The first report from real use found
both at once. (1) The history tree went *flat on a running session*: the
server's pop guard is `bin/assemble`'s own, but the kernel only ever runs it
*between* turns while the plugin projects on every request, so a tool call
whose result was not written yet read as a stranded pair and every rewind
marker of the log was dropped. A session with four markers that the user knew
were there projected `0 fins` while the agent worked and `4 fins` a moment
later. Fix: `build_live`/`rewind_verdict_live` treat an id unpaired *anywhere
in the log* as **pending** while `is_running(session)` — a narrow rule (a pair
the mask splits still strands, a dead loop keeps the kernel's exact rule).
(2) The ring *never turned on the sessions that have branches*: `--phase`
reads the scene's pan, and `#rw-flow-track` auto-fits, so a track of 17..45
columns fits its panel **exactly** — `scrollLeft` pinned at 0, wheel and drag
both dead (measured `0/1200/1200`, `--rw-scroll` `"0"`, four fins unmoved
after six real wheel events). Fix: `--rw-scroll` is `scrollLeft + turn`, the
turn being the part of a gesture the track could not take; a scene that can
pan is unchanged, a scene that cannot now turns the ring in place, and the
turn resets at every layout point so rest still means alignment.
**§12.10 of `docs/rewind-plugin-plan.md`, §3b.6 of `docs/rewind-plugin.md`.**
The two browser checks that would pin these (a cone that turns on a scene too
narrow to pan, and a `loop.pid` toggle for the pending rule) are still not
written — deferred, because the user was reviewing that fix by hand — but the
four probes have since been re-run green for v0.5.70 and v0.5.71.

## Status & limits

* Implemented and verified (P1–P7, the rewind × compaction work of section 11,
  **Style B — the flow view (§10.8 of `docs/rewind-plugin-plan.md`), the
  round-2 work (§10.9), the orbital ring (§12.9), the two live defects of
  §12.10, the cone of §13, its full circle (§13.7/§13.8) and the even fan
  (§13.9/§13.10) and the focus carousel (§15, v0.5.73)**; upstream `rushi-webui`
  v0.5.73).
  **Two styles, one tree:** `flow` — the horizontal `. - . - .` trunk with each
  forking branch leaning out of it as a straight ray, a 1:2 split with the
  selected round's full text above and the scene below, an auto-fit scene that
  pans by drag, a horizontal delta or the scrollbar, whose wheel walks a
  **carousel of the branches** (one notch = the next branch up, the rest in the
  lower half) — is **the default**, and the
  list (the rounds as a flat tree) is one click away in the top bar. Rewind is
  still only ever triggered from the panel's button → the one confirm dialog.
  The plugin is a pure view plus a write of an existing event type: **no
  kernel change** for the plugin side. It *depends* on the kernel fix in
  `rushi` f145572 (`docs/rewind-fork-design.md` section 11 — the compaction
  boundary is the last one **on the active path**); before it, a rewind past a
  boundary created inside the abandoned branch resumed with that branch's
  handoff and no history.
* v0.5.61 (the visibility work, D-C): the server re-evaluates the kernel's own
  decision (`rewind_verdict`) and refuses a pick that would be ignored
  (`409`, nothing written); every node is annotated with what it restores
  (`Raw` / `Framed{vN, from, to}` / `Unresumable`); and a rewind that did not
  take effect is reported in the plugin area (`ignored` / `tail_ignored`). A
  dropped marker no longer moves the tree's cursor.
* **Long sessions render in both styles now (v0.5.67).** The list style used
  to nest one DOM level per round and exhausted the wasm stack past roughly
  32–80 rounds (`Webui` 117 and `alpha` 82 drew **six** nodes each, 5 and 8
  traps, `RuntimeError: memory access out of bounds` — the app survived, the
  tree did not paint, and History looked like a stale copy). A node with a
  single child now continues as a **sibling**, so recursion depth is the
  number of *forks*, not rounds; all eight live sessions draw in full in the
  list style (117 / 82 / 33 / 30 / 27 / 16 / 11 / 6 nodes, zero traps) and a
  plain run is one flat column instead of a staircase. The flow style's
  scene draws the same trees (as a cone of rays since v0.5.70). Both rewind reads also parse past
  serde_json's 128-level limit (`api::parse_deep`); without it those long
  sessions failed the parse and History sat on "loading…" (fixed in upstream
  v0.5.65).
* A flow scene that **fits** its panel has nothing to pan, but since v0.5.69
  the gesture's overshoot becomes a *turn*, so such a scene still rotates its
  branches — including a whole revolution (measured on the live `Webui`
  session: `0/1200/1200`, and the cone still turns).
* The **client** module cannot be an out-of-tree crate: it binds to
  `rushi-webui`'s `AppState`, `api` and `timeutil`, and to the Leptos view
  tree (the tree needs `RwSignal` state shared with the transcript). Hence a
  mirror package, not a dependency. The server module (`server/rewind.rs`) is
  a pure function over `serde_json::Value` and *could* be extracted as a
  standalone crate if that ever matters.
* Runtime-loaded (out-of-tree) `rushi-webui` plugins are not supported yet —
  `rushi-webui-plan.md` Phase 3 sketches the `ext-web.toml` /
  `GET /api/exts/:name/manifest` protocol for that. Until then the plugin area
  is a compile-time registry (`web-leptos/src/plugins.rs`), which is why this
  repo ships an install map instead of a loader.
* The probe and the CSS carry no license of their own; they follow the
  upstream `rushi-webui` repository.
