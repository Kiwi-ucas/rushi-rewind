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
  `rushi-webui` checkout, verifies all 17 integration touch points still
  exist, and records the upstream revision in `UPSTREAM`.

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
├─ e2e/model_panel_probe.py     the shared CDP harness the probe imports — LOCAL, untracked
└─ scripts/sync-from-webui.sh   re-extract + verify + record the upstream rev
```

The two `e2e/` python files are local verification tools: they are ignored by
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
cd rushi-webui && cargo test -p rushi-web        # 54 passed (17 rewind)

# frontend
cd rushi-webui/web-leptos && trunk build          # clean

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
generated `client/rewind.css`, checks the 17 touch points, and rewrites
`UPSTREAM`. Hand-maintained files (this README, `install/`, the two
`client/*.css` inputs) are never touched. If a touch point is gone, the script
fails with the file and the missing marker rather than shipping a stale
mirror — that is the signal to update `install/TOUCHPOINTS.md`.

## Status & limits

* Implemented and verified (P1–P7 and, for the rewind × compaction work,
  section 11 of `docs/rewind-plugin-plan.md`; upstream `rushi-webui` v0.5.61).
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
