# Install map — every change the rewind plugin makes to a rushi-webui checkout

Two files drop in, thirteen spots wire them up. All snippets below are the
current upstream code (`rushi-webui` v0.5.56); `scripts/sync-from-webui.sh`
verifies each anchor still exists.

Paths are relative to the `rushi-webui` root. Line anchors are indicative —
the snippet text is the anchor.

---

## 1. Drop-in files

```sh
cp server/rewind.rs  <webui>/bin/rushi-web/src/rewind.rs        # the projection
cp client/rewind.rs  <webui>/web-leptos/src/rewind.rs           # the client module
```

`bin/rushi-web/src/rewind.rs` is a pure function over the event list
(`build(session, &[Value]) -> RewindTree`), with its unit tests at the bottom
(the kernel's own `active_ranges` fixtures, ported as a reference). It has no
dependency on the rest of the server module and never writes.

`web-leptos/src/rewind.rs` holds the History view, the recursive node view,
the confirm dialog, the card quick button and the sidebar panel, plus the one
shared fetch effect (`register_tree_effect`).

---

## 2. Server wiring

### 2.1 `bin/rushi-web/src/main.rs`

```rust
mod rewind;                                    // with the other `mod` lines (alphabetical)
```

```rust
/// Read-only history-tree projection (the rewind plugin's data source):
/// rounds = one user message each, forks at every rewind marker, the active
/// path + the "you are here" round. Never writes.
async fn get_rewind_tree(
    State(st): State<AppState>,
    Path(id): Path<String>,
) -> impl IntoResponse {
    match st.sessions.events(&id).await {
        Ok(events) => {
            // v0.5.69: while this session's loop is alive, a tool call whose
            // result is not written yet is pending, not stranded — otherwise
            // the projection drops every rewind marker for as long as the
            // agent works (see server/rewind.rs `build_live`).
            let live = st.loops.is_running(&id).await;
            (StatusCode::OK, Json(rewind::build_live(&id, &events, live))).into_response()
        }
        Err(e) => (StatusCode::NOT_FOUND, e.to_string()).into_response(),
    }
}
```

```rust
/// Write a `rewind` marker. The R1 pre-check
/// (`docs/rewind-plugin-plan.md` 11.1) gates it: the kernel never refuses
/// a marker, so the server answers what its projection would do with it.
/// A pick that would be dropped is refused (409 + the verdict) and the log
/// gains no marker that does nothing.
async fn post_rewind(
    State(st): State<AppState>,
    Path(id): Path<String>,
    Json(body): Json<PostRewind>,
) -> impl IntoResponse {
    let verdict = match st.sessions.events(&id).await {
        Ok(events) => {
            let live = st.loops.is_running(&id).await;
            rewind::rewind_verdict_live(&events, body.target_seq, &body.mode, live)
        }
        Err(e) => return (StatusCode::NOT_FOUND, e.to_string()).into_response(),
    };
    if !matches!(verdict, rewind::RewindVerdict::Ok) {
        return (
            StatusCode::CONFLICT,
            Json(serde_json::json!({ "ok": false, "verdict": verdict })),
        )
            .into_response();
    }
    match st.sessions.append_rewind(&id, body.target_seq, &body.mode).await {
        Ok(()) => (
            StatusCode::OK,
            Json(serde_json::json!({ "ok": true, "verdict": verdict })),
        )
            .into_response(),
        Err(e) => (StatusCode::INTERNAL_SERVER_ERROR, e.to_string()).into_response(),
    }
}
```

```rust
/// Style B (v0.5.64): ONE round in full, by round seq — the flow view's
/// detail panel. Verbatim text, never a summary; 404 for a seq that is not
/// a round. Kept as a route (rather than fattening the tree) so the payload
/// stays bounded per interaction — see the plan's D8 and 10.8.
async fn get_rewind_node(
    State(st): State<AppState>,
    Path((id, seq)): Path<(String, u64)>,
) -> impl IntoResponse {
    match st.sessions.events(&id).await {
        Ok(events) => match rewind::node_detail(&events, seq) {
            Some(d) => (StatusCode::OK, Json(d)).into_response(),
            None => (StatusCode::NOT_FOUND, "not a round".to_string()).into_response(),
        },
        Err(e) => (StatusCode::NOT_FOUND, e.to_string()).into_response(),
    }
}
```

The routes:

```rust
.route("/api/sessions/{id}/rewind", get(get_rewind_tree).post(post_rewind))
.route("/api/sessions/{id}/rewind/node/{seq}", get(get_rewind_node))
```

---

## 3. Client wiring

### 3.1 `web-leptos/src/model.rs` — the data model

Paste the rewind type block (`RewindNode`, `RewindMarker`, `RewindBoundary`,
`RewindTree` + its helpers, `RewindTarget`, and the v0.5.61 additions
`Restore`, `RewindVerdict`, `IgnoredMarker` with their `label()` /
`blocked()` / `notice()` impls) anywhere at module level — the upstream
tree keeps it just above the `EssenceEntry` impl. Then add three
`AppState` fields (and their `RwSignal` initializers in `AppState::new`):

```rust
    /// Rewind plugin: the active session's projected history tree
    /// (`GET /api/sessions/{id}/rewind`). None until the first fetch.
    pub rewind_tree: RwSignal<Option<RewindTree>>,
    /// Rewind plugin: the confirm dialog's pending target (None = closed).
    pub rewind_pending: RwSignal<Option<RewindTarget>>,
    /// Rewind plugin: bumped when a `user_message` / `rewind` event arrives
    /// (the only structure-changing event types) so the tree refetches.
    pub rewind_gen: RwSignal<u64>,
```

```rust
            rewind_tree: RwSignal::new(None),
            rewind_pending: RwSignal::new(None),
            rewind_gen: RwSignal::new(0),
```

### 3.2 `web-leptos/src/api.rs` — the two calls

```rust
/// Rewind plugin: the session's projected history tree (read-only). The
/// server degrades a missing log to an empty tree.
pub async fn load_rewind_tree(id: &str) -> Result<crate::model::RewindTree, String> {
    let res = Request::get(&format!("/api/sessions/{id}/rewind"))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let text = res.text().await.map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

/// Rewind plugin: write a `rewind` marker. `mode:"on"` keeps the target user
/// message as the active tail and abandons everything after it; the fork
/// stays in the log and can be re-entered later. v0.5.61: the R1 verdict
/// rides back (200 = written, 409 = the kernel would ignore it, nothing
/// written) so the dialog can show the notice instead of a bare error.
pub async fn post_rewind(
    id: &str,
    target_seq: u64,
    mode: &str,
) -> Result<crate::model::RewindVerdict, String> {
    let payload = json!({ "target_seq": target_seq, "mode": mode });
    let res = Request::post(&format!("/api/sessions/{id}/rewind"))
        .json(&payload)
        .map_err(|e| e.to_string())?
        .send()
        .await
        .map_err(|e| e.to_string())?;
    let status = res.status();
    let text = res.text().await.map_err(|e| e.to_string())?;
    let verdict = serde_json::from_str::<serde_json::Value>(&text)
        .ok()
        .and_then(|v| v.get("verdict").cloned())
        .and_then(|v| serde_json::from_value(v).ok());
    match verdict {
        Some(v) => Ok(v),
        None if status >= 400 => Err(format!("rewind failed: HTTP {status}")),
        None => Ok(crate::model::RewindVerdict::Ok),
    }
}
```

### 3.3 `web-leptos/src/lib.rs` — mounting

```rust
mod rewind;                                    // with the other `mod` lines
```

```rust
    // Rewind plugin: one tree fetch for the whole app (the History view and
    // the sidebar panel both read `state.rewind_tree`); keyed on the active
    // session + the structure-generation counter.
    crate::rewind::register_tree_effect(state);
```

The rebuilt expanded view — mounted as a sibling of `#main`, shown only in
`layout-full`:

```rust
            <Show
                when=move || state.layout_mode.get() == "full"
                fallback=|| ()
            >
                { crate::rewind::history_view(state) }
            </Show>
```

The dialog, next to the other app-level dialogs:

```rust
            { crate::rewind::rewind_confirm_dialog(state) }
```

### 3.4 `web-leptos/src/ws.rs` — the refetch trigger

Inside the incoming-`event` frame handler, right after the frame is parsed and
`normalize_event`ed:

```rust
                        let t = ev.get("type").and_then(|v| v.as_str()).unwrap_or("");
                        // Rewind plugin: only `user_message` / `rewind`
                        // change the history tree's shape (a round or a
                        // fork); the plugin refetches on these two.
                        if t == "user_message" || t == "rewind" {
                            st.rewind_gen.update(|g| *g += 1);
                        }
```

### 3.5 `web-leptos/src/transcript.rs` — the card quick button

In `event_card_view` (it owns the card index), before `ev_body` is called:

```rust
    // The card's own 1-based log line: the server ships the window's first
    // line (`oldest_line`) and index-keyed cards, so the line is
    // `oldest_line + key`. That is the SAME non-empty-line numbering the
    // kernel's `seq`/`target_seq` use, so the rewind plugin can post a
    // `target_seq` straight from a rendered card. Only meaningful for
    // `user_message` (the only event type a rewind may target).
    let card_seq = state.hist_oldest_line.get_untracked() + key as u64;
    let body = ev_body(&ev, &t, state, events, card_seq);
```

`ev_body` gains the parameter:

```rust
fn ev_body(
    ev: &Value,
    t: &str,
    state: AppState,
    events: RwSignal<Vec<Value>>,
    card_seq: u64,
) -> AnyView {
```

and the `"user_message"` arm renders the button after the content:

```rust
            let excerpt: String = content.chars().take(60).collect();
            let v = view! {
                { badge }
                <div class="ev-content">{ md_blocks_view(content) }</div>
                { crate::rewind::quick_rewind_button(state, card_seq, excerpt) }
            };
```

### 3.6 `web-leptos/src/plugins.rs` — the `#plugin-area` entry

```rust
pub const PLUGINS: [PluginDef; 3] = [
    PluginDef { id: "goal", label: "goal" },
    PluginDef { id: "essence", label: "essence" },
    PluginDef { id: "rewind", label: "rewind" },
];
```

(bump the array length: `2` → `3`.)

### 3.7 `web-leptos/src/ui.rs` — the dispatcher + visibility

```rust
        "rewind" => crate::rewind::rewind_plugin_view(state).into_any(),
```

The plugin module reuses three `ui.rs` helpers, so they must be
`pub(crate)` (they are module-private upstream — `fn` → `pub(crate) fn`):

* `set_layout_mode` (the History view's back button / the panel's
  *open History view* button persist the layout),
* `set_theme_mode_stored` and `theme_icon` (the History top bar's theme
  button is the sidebar's, rendered in the new view).

### 3.8 `web-leptos/src/ui.rs` — the session rail's shared card

The History rail renders the **M7 dispatch view's session card** (v0.5.56), so
two pieces of the dispatch view are extracted and shared instead of copied:

```rust
/// M7 (v0.5.56): the project-group header line — the working directory
/// (basename as the label, the full path in the title) plus the card count.
pub(crate) fn session_group_head(group_key: &str, count: usize) -> AnyView { ... }

/// M7 (v0.5.56): the session card itself, shared by the sidebar's dispatch
/// view and the rewind plugin's History rail.
pub(crate) fn session_card(state: AppState, s: SessionInfo, stay: bool) -> AnyView { ... }
```

`dispatch_card` becomes a one-line wrapper (`session_card(state, s, false)`),
and the card's click handler honours `stay`: `false` returns to the `split`
layout (the dispatch view), `true` keeps the full-window view (the rail
switches sessions in place). The `…` rename/delete menu is shared as-is —
`#sess-menu` is `position: fixed` and mounted *outside* `#sidebar`, so it is
never hidden by `layout-full`.

The rail then needs only the upstream M7 rules (`.dispatch-group`,
`.dispatch-card`, `.dc-*`, `.qa`, `.sess-more`) — they are already in every
`style.css`, so `client/rewind.css` adds nothing but the rail's own column
compaction (`#hist-rail .dispatch-card` etc.).

**v0.5.57 — the head's label.** The same shared helper now carries the
label policy, so this is part of the general layer as well:

```rust
// ui.rs: the store (localStorage `rushi-project-labels`, a
// `{ "<working path>": "<label>" }` map — the path is never rewritten)
pub fn read_project_labels() -> HashMap<String, String>;
pub fn persist_project_labels(labels: &HashMap<String, String>);
pub fn group_label(state: AppState, group_key: &str) -> String;   // alias ?? basename
pub fn set_group_label(state: AppState, group_key: &str, label: &str);
```

plus two `AppState` fields (`project_labels`, `group_edit`). The head renders
`<span class="dispatch-group-name" title="<full path>">label</span>` and, on
hover, a `✎` that swaps the label for an `<input>` (Enter/blur commit, Escape
cancel, empty = basename, `.custom` drops M7's uppercase). Its four CSS rules
(`.dispatch-group-name`, `.custom`, `.dispatch-group-edit`, `.dispatch-group-input`)
are restated in `client/rewind-additive.css`, so the drop-in stays complete —
upstream they live next to the M7 `.dispatch-group-*` rules.

---

### 3.9 Style B — the flow view's client wiring (v0.5.65)

The flow style is the **same tree read a second way**: no second fetch, no
second cache, no second invalidation path. Its wiring is:

**`web-leptos/src/model.rs`** — the flat projection's types (mirroring the
server's `Flow`), plus three `AppState` fields:

```rust
pub struct RewindFlow { pub nodes: Vec<FlowNode>, pub edges: Vec<FlowEdge>,
                        pub branches: Vec<FlowBranch>, pub cols: usize,
                        pub lanes: i32, pub main_len: usize }
pub struct FlowNode   { /* seq, x, lane, main, state, round, ts, events, summary */ }
pub struct FlowEdge   { /* from, to, main, lane */ }
pub struct FlowBranch { /* root, lane, from_x, to_x */ }
pub struct RewindDetail { /* seq, round, ts, text, events, state, current, retracted */ }

// AppState:
pub rw_view: RwSignal<String>,          // "tree" | "flow" (persisted)
pub rw_selected: RwSignal<Option<u64>>, // the round the panel shows
pub rw_detail: RwSignal<Option<RewindDetail>>,
```

**`web-leptos/src/api.rs`** — `load_rewind_detail(id, seq)`, and both rewind
reads parse through `parse_deep`:

```rust
/// serde_json refuses past 128 nesting levels, and the tree nests one
/// `children` array per round — a chain of ~64 rounds failed the whole
/// response (v0.5.65). `unbounded_depth` + this parse is the fix.
fn parse_deep<T: DeserializeOwned>(body: &str) -> Result<T, String> { ... }
```

**`web-leptos/src/lib.rs`** — one extra effect registration beside
`register_tree_effect`:

```rust
crate::rewind::register_flow_effects(state);   // preselect (D5) + scene metrics
```

**`web-leptos/src/rewind.rs`** — the plugin's own additions:
`read_view_mode` / `set_view_mode` (localStorage `rushi-rw-view`),
`select_node`, `register_flow_effects`, `sync_scene_metrics` (writes
`--halfpw` / `--denom` / `--bx` at layout time), `mark_scroll` (the one value
a scroll frame writes), `hist_style_switch`, `flow_view`, `flow_detail`,
`flow_scene`, `flow_node_button`.

**`web-leptos/src/ws.rs`** — unchanged: the same `rewind_gen` bump
invalidates both styles.

---

## 4. Stylesheet

`web-leptos/style.css` — append `client/rewind.css` (the plugin's section
plus the additive rules), or load it as a second stylesheet after
`style.css`. The generated file covers:

* `#history-view`, `#hist-*`, `.rw-*`, `#rw-backdrop` / `#rw-dialog`,
  `#rewind-body` / `.rewind-*`, `.ev-quickbtn`;
* `#app.layout-full #sidebar` / `#main` → `display: none` (the old
  `layout-full` rules are replaced by the History view);
* the `#hist-theme` icon sizing (the `#theme-toggle svg/.ln/.fl` groups);
* the three new scrollers (`#hist-tree`, `#hist-rail`, `.rewind-list`) in the
  five capsule-scrollbar rules (plugin rule 4 of
  `docs/plugin-authoring-rules.md`).

Upstream edits those three shared groups **in place**; `rewind-additive.css`
restates them additively so an untouched stylesheet + this file behaves
identically (see the file header).

Style B adds a second, later sub-section to the same block
(`/* ── v0.5.65 Rewind Style B — the "flow" view */` and the `/* B7: ... */`
part): `#rw-split`, `#rw-detail` / `.fd-*`, `#rw-flow-scroll` /
`#rw-flow-track` / `.rw3-*`, and `.fd-text` in the five capsule-scrollbar
rules. Both live **inside** the plugin's section, i.e. before the
time-inject banner.

`client/rewind.css` is generated: `sync-from-webui.sh` takes this plugin's
first banner up to the banner of the plugin that follows it (time-inject),
prepends the additive rules, and then checks a set of **canaries**
(`#rw-split`, `.rw3-node`, `.rw-orbit`, `.rw-branch`, `.rw-br-line`,
`.rw3-sheen`, `.fd-text`) inside the extracted block — plus a *gone* set
(`.rw-fin`, `.rw3-elbow`, `.rw3-hinge`, `.rw-orbit.solo`) that must **not**
appear as rules. Two failure modes this catches — both real:

* taking the block to the next `/* ── ` banner at all (v0.5.59): the
  time-inject section was appended after rewind's and 22 of its lines landed
  in the mirror;
* or (v0.5.65) stopping at the plugin's **own** Style B sub-banner, which
  silently dropped every rule after it. A sub-banner that names a step rather
  than a version (`/* B7: ...`) avoids the ambiguity for any tool that scans
  for `/* ── `, and the canaries fail the sync loudly if the block is short.

The canary list tracks what the section actually ends with. v0.5.68 replaced
the `.rw3-ribbon` / `.rw3-hinge` pair with the orbital `.rw-orbit` /
`.rw-fin`; **v0.5.70 (the cone)** deletes `.rw-fin` and `.rw3-elbow` too —
a branch is one straight `.rw-br-line` out of its parent's bead, so there is
neither a ribbon nor an elbow left — and those names move to the *gone* set,
which fails the sync if a stale copy reintroduces them (a comment naming them
is fine; a rule is not). **v0.5.71 (the full circle)** adds `.rw-orbit.solo`:
the bounded arc, its park and the one-branch swing are gone (D-cone-8/11), so a
resurrected `.rw-orbit.solo` rule fails the sync too.

**v0.5.72 (the even fan)** changed no selector: the step became `360/k` of each
*fan*, and since the stylesheet cannot know which fan a container belongs to,
the *render* writes each branch's angles in degrees (`--th`, and `--rdeg` for
the top ancestor) instead of the stylesheet deriving them from `--slot` and one
global `--step`. `--step`/`--align`/`--sroot` therefore vanished from the
section while `--slot` stayed (as the rank inside the fan), so the canary and
gone lists are unchanged.

---

## 5. Build & verify

```sh
cargo build -p rushi-web                     # server binary (new route)
cd web-leptos && trunk build                 # WASM bundle
cargo test -p rushi-web                      # 71 passed (flow layout + the cone + node detail)
python3 e2e/rewind_probe.py 8491             # 140 checks, PASS (Style A)
python3 e2e/flow_check.py <port>             # the flow projection + the ring's invariants,
                                             #   over every live session
python3 e2e/flow_style_b_probe.py <port>     # 68 checks, PASS (Style B, both themes,
                                             #   + H: the orbit, + K: the list style on
                                             #   a 117-round chain)
python3 e2e/orbit_probe.py <port>            # 48 checks, PASS (the cone's own fixtures:
                                             #   the geometry law, the even fan (360/k,
                                             #   never edge-on), the full circle (a whole
                                             #   turn spent on the wheel), the depth dim,
                                             #   the flat projection, reduced motion, and
                                             #   the zero-/one-fork cases)
python3 e2e/layout_probe.py  <port>          # shell regression (scrollbar rules)
```

(`e2e/` is untracked by design — the probes are local tools, not part of
what this repository ships; `rushi-webui/e2e/` is gitignored too, and
`scripts/sync-from-webui.sh` refreshes the local copies.)

The list style's 117-round case is checked by `flow_style_b_probe.py`'s
section K (one DOM level per **fork** since v0.5.67; a chain is flat, no wasm
trap). `e2e/rewind_probe.py` pins `rushi-rw-view=tree` for itself, because the
History view's default style is `flow` since v0.5.67.

A restart of the running `rushi-web` is required for the new route; the WASM
bundle is re-read from `dist/` on the next page load in debug builds.

**Upstream dependency (not part of this package).** The probe's `■ stop`
check kills a fake loop and requires the session to leave `/api/loops`
*while the process is still an unreaped zombie*. That needs upstream
**v0.5.58's zombie-aware liveness probe** in `bin/rushi-web/src/process.rs`
(`is_pid_alive = kill(pid, 0) && !is_zombie(pid)`, with a per-platform
`is_zombie`); on an older server the check fails and, in real use, a loop
started by the TUI / a previous server instance looks permanently running
after a stop (no `▶ start`, the rewind guard stuck on). No rewind-plugin file
depends on it — it is a general server fix — but the probe asserts it.
