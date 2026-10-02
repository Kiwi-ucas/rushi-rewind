#!/usr/bin/env bash
# sync-from-webui.sh — re-extract the WebUI-side sources of the rewind plugin
# from a rushi-webui checkout.
#
# The rushi-webui tree is the plugin's DEVELOPMENT home (it is compiled into
# the server binary and the WASM bundle there). This repository PACKAGES the
# plugin: the droppable files, the install map, the docs and the browser
# probe — so a plugin can be carried to another checkout, reviewed on its
# own, and the probe survives (`rushi-webui/e2e/` is gitignored upstream).
#
# Usage:
#   scripts/sync-from-webui.sh [path-to-rushi-webui]
#   (default: ../rushi-webui next to this repo)
#
# It is idempotent: it overwrites the mirrors from upstream and regenerates
# client/rewind.css from the upstream stylesheet block plus the additive
# rules kept in client/rewind-additive.css. UPSTREAM records the exact
# upstream revision the mirrors were taken from.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WEBUI="${1:-$HERE/../rushi-webui}"
WEBUI="$(cd "$WEBUI" && pwd)"

say() { printf '%s\n' "$*"; }
need() { [ -f "$1" ] || { echo "sync: missing $1" >&2; exit 1; }; }

# ── the upstream files ──────────────────────────────────────────────
SERVER="bin/rushi-web/src/rewind.rs"
CLIENT="web-leptos/src/rewind.rs"
STYLE="web-leptos/style.css"
PROBE="e2e/rewind_probe.py"
CDP="e2e/model_panel_probe.py"
DOC1="docs/rewind-plugin.md"
DOC2="docs/rewind-plugin-plan.md"
for f in "$SERVER" "$CLIENT" "$STYLE" "$PROBE" "$CDP" "$DOC1" "$DOC2"; do
  need "$WEBUI/$f"
done

cp "$WEBUI/$SERVER" "$HERE/server/rewind.rs"
cp "$WEBUI/$CLIENT" "$HERE/client/rewind.rs"
cp "$WEBUI/$PROBE"  "$HERE/e2e/rewind_probe.py"
# The CDP harness the probe imports (`from model_panel_probe import Cdp,
# ws_connect`) — copied so the probe runs from this repo standalone.
# NOTE: the two probe copies below stay LOCAL — e2e/ is gitignored and e2e
# python test files are never uploaded from this account. They are convenience
# copies of rushi-webui/e2e/, not part of what this repository ships.
cp "$WEBUI/$CDP"    "$HERE/e2e/model_panel_probe.py"
cp "$WEBUI/$DOC1"   "$HERE/docs/rewind-plugin.md"
cp "$WEBUI/$DOC2"   "$HERE/docs/rewind-plugin-plan.md"

# ── the stylesheet: the appended block + the additive rules ─────────
# The block is everything from the plugin's section banner to the NEXT
# section banner (or EOF): the sections are APPENDED to the upstream
# stylesheet, never inserted, so a section is a contiguous suffix-run and
# its end is the next top-level banner. (Taking it to EOF instead silently
# swallows every later section — v0.5.59's time-inject section was appended
# after rewind's and 22 of its lines landed in the rewind mirror.) A missing
# banner means the upstream layout changed.
BANNER='/* ── v0.5.56 Rewind plugin'
grep -qF "$BANNER" "$WEBUI/$STYLE" || {
  echo "sync: the rewind section banner is gone from $STYLE" >&2; exit 1; }
awk -v banner="$BANNER" '
  index($0, banner) == 1 { on = 1; print; next }
  on && /^\/\* ── / { exit }          # the banner that follows ends this one
  # blank lines are held back: the ones inside the section are flushed by the
  # next real line, the trailing run (the separator before the next banner, or
  # the file end) is dropped, so the block is the section exactly
  on && /^$/ { pend = pend "\n"; next }
  on { if (pend != "") { printf "%s", pend; pend = "" } print }
' "$WEBUI/$STYLE" \
  > "$HERE/client/.rewind-block.css.tmp"

{
  cat "$HERE/client/header.css"
  cat "$HERE/client/.rewind-block.css.tmp"
  cat "$HERE/client/rewind-additive.css"
} > "$HERE/client/rewind.css"
rm -f "$HERE/client/.rewind-block.css.tmp"

# ── verify the WebUI touch points still exist ───────────────────────
# The plugin's own two modules are mirrored above; these are the edits
# that wire them into the WebUI (install/TOUCHPOINTS.md lists them all).
# A missing marker means the upstream integration changed and this
# package needs a look — fail loudly instead of shipping a stale mirror.
check() { # file, marker, label
  grep -qF -- "$2" "$WEBUI/$1" || {
    echo "sync: touch point lost — $3" >&2
    echo "      $1 no longer contains: $2" >&2
    exit 1; }
}
check bin/rushi-web/src/main.rs        'mod rewind;'                                   "main.rs: mod rewind"
check bin/rushi-web/src/main.rs        'rewind::build(&id, &events)'                   "main.rs: the projection handler"
check bin/rushi-web/src/main.rs        'get(get_rewind_tree).post(post_rewind)'        "main.rs: the route"
check web-leptos/src/model.rs          'pub struct RewindTree {'                       "model.rs: the types"
check web-leptos/src/model.rs          'pub rewind_gen: RwSignal<u64>,'                 "model.rs: the state slice"
check web-leptos/src/api.rs            'pub async fn load_rewind_tree('                "api.rs: the read call"
check web-leptos/src/api.rs            'pub async fn post_rewind('                     "api.rs: the write call"
check web-leptos/src/lib.rs            'crate::rewind::register_tree_effect(state);'   "lib.rs: the fetch effect"
check web-leptos/src/lib.rs            'crate::rewind::rewind_confirm_dialog(state)'   "lib.rs: the dialog mount"
check web-leptos/src/ws.rs             'st.rewind_gen.update(|g| *g += 1);'            "ws.rs: the structure bump"
check web-leptos/src/transcript.rs     'crate::rewind::quick_rewind_button('           "transcript.rs: the card button"
check web-leptos/src/plugins.rs        'PluginDef { id: "rewind", label: "rewind" }'   "plugins.rs: the registry entry"
check web-leptos/src/ui.rs             '"rewind" => crate::rewind::rewind_plugin_view'  "ui.rs: the dispatcher arm"
check web-leptos/src/ui.rs             'pub(crate) fn session_card('                   "ui.rs: the shared session card"
check web-leptos/src/ui.rs             'pub(crate) fn session_group_head('             "ui.rs: the shared group head"
check web-leptos/src/ui.rs             'rushi-project-labels'                          "ui.rs: the project-label store"
check web-leptos/style.css             '#history-view { flex: 1 1 auto'                "style.css: the section body"

# ── record the upstream revision ────────────────────────────────────
REV="$(git -C "$WEBUI" rev-parse HEAD 2>/dev/null || echo unknown)"
SUBJ="$(git -C "$WEBUI" log -1 --pretty=%s 2>/dev/null || echo unknown)"
DIRTY="clean"
[ -n "$(git -C "$WEBUI" status --porcelain 2>/dev/null)" ] && DIRTY="dirty"
{
  echo "# The rushi-webui revision these mirrors were taken from."
  echo "# Written by scripts/sync-from-webui.sh — do not edit."
  echo "repo     = $WEBUI"
  echo "rev      = $REV"
  echo "subject  = $SUBJ"
  echo "worktree = $DIRTY"
  echo "synced   = $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
} > "$HERE/UPSTREAM"

say "synced from $WEBUI @ $REV ($DIRTY)"
git -C "$HERE" status --short || true
