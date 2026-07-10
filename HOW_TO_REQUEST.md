# How to Send Requests to AI — VocabGraph Guide

This file has two purposes:
1. A complete log of every change made to this project
2. A personal guide showing you **how to write better requests** so the AI understands faster and produces correct results on the first try

---

## The Request Template

Use this structure every time:

```
CONTEXT:   In [feature/file], currently [what exists or what is broken].
ACTION:    [Add / Fix / Remove / Change / Move] [specific thing].
BEHAVIOR:  When [trigger], it should [expected result].
CONSTRAINT: Make sure [restriction or edge case to respect].
```

You do not need to use these exact words — but always include these four pieces of information. The more you give, the less the AI has to guess.

---

## The 5 Rules for Good Requests

### Rule 1 — Name the feature and the file
Bad:  `"the graph is not working"`
Good: `"In the roadmap path view (frontend/index.html), when I select a node the graph resets zoom"`

### Rule 2 — Describe current behavior AND expected behavior
Bad:  `"fix the highlight"`
Good: `"Currently selecting a node only highlights ancestors. I also want descendants (nodes that come after) to be highlighted"`

### Rule 3 — One topic per request (or number them clearly)
Bad:  `"fix the bug and also add a button and the panel is too small"`
Good:
```
I have 3 separate requests:
1. Fix: [specific bug]
2. Add: [specific feature]
3. Change: [specific layout]
```

### Rule 4 — Say what you do NOT want
Bad:  `"make it highlight the path"`
Good: `"Highlight the path from root to the selected node — do NOT highlight all connected nodes, only the direct ancestor chain"`

### Rule 5 — Include the trigger, not just the goal
Bad:  `"add a queue"`
Good: `"When I click Expand on multiple nodes, instead of waiting for each one, add them to a queue and process them one at a time"`

---

## All Project Requests — Rewritten in Good Format

These are the 22 changes made to this project, ordered from most recent to oldest, each rewritten as an example of a well-structured request.

---

### 22. Fix — Tooltip shows stale node name

**How to write it:**
> In the roadmap path view (`frontend/index.html`), in the `mouseenter` event on nodes, the tooltip still shows the name of the previously selected node when I hover a different node.
> Fix it so the tooltip always updates to the hovered node's name regardless of whether another node is selected.

---

### 21. Fix — Node labels hidden behind glow lines

**How to write it:**
> In the roadmap path view SVG, the text labels on nodes are rendered behind the bezier curve lines, making them unreadable.
> Add a dark stroke outline behind each letter using SVG `paint-order:stroke` so the text is visible on top of the glow lines.

---

### 20. Remove — "Learning Path" right panel

**How to write it:**
> In the roadmap overlay (`frontend/index.html`), remove the right-side "Learning Path" panel completely. It adds no value and takes up screen space.

---

### 19. Multi-part change — Spanish request (5 items)

**How to write it:**
> I have 5 separate changes for the roadmap:
>
> 1. **Fix BFS direction**: When I select a node, only show the ancestor chain (nodes that lead TO it from the start). Currently it highlights all directions — fix it to go backward only.
> 2. **Fix all-paths bug**: Related to above — clicking one node should not highlight the entire graph.
> 3. **Make left panel resizable**: The node names are cut off with `...`. Add a drag handle so I can resize the panel width.
> 4. **Graph full page**: The graph area only fills half the screen. Make it fill 100% of height and width.
> 5. **Redesign column headers**: The plain text headers at the top are hard to read. Replace them with styled pill badges that support word wrap up to 4 lines.

---

### 18. Feature — Auto-generate column headers for new sections

**How to write it:**
> In the roadmap path view, when a new section is added (by expanding a node or generating), confirm that the column header pill badge is automatically created for it.
> The headers should appear dynamically without any manual update.

---

### 17. Feature — Add "✕ Clear path" button

**How to write it:**
> In the roadmap path view (`frontend/index.html`), add a floating button that deselects the currently highlighted path.
> It should appear in the top-left of the graph area only when a node is selected, and disappear when the path is cleared.

---

### 16. Fix — Column headers truncating with ellipsis

**How to write it:**
> In the roadmap path view, the pill badge column headers are still truncating long names with `...`.
> Change them to use full text with up to 4 line breaks using SVG `<tspan>` elements. No ellipsis allowed.

---

### 15. Fix — `TypeError: rmClearPathBtn is null`

**How to write it:**
> In `frontend/index.html`, clicking a node throws:
> `TypeError: Cannot read properties of null (reading 'style') at rmSelectNode`
>
> The bug is that `_rmRenderPath()` calls `area.innerHTML = ''` which destroys `#rmClearPathBtn`, then tries to reference it.
> Fix: save both `#rmPcZoomBtns` and `#rmClearPathBtn` before clearing innerHTML, then re-append them after.

---

### 14. Feature — Docker + Ollama setup (prepare only, do not run)

**How to write it:**
> Create a Docker setup for this project with Ollama included.
> Files needed: `Dockerfile`, `docker-compose.yml`, `docker/ollama-init.sh`, `.dockerignore`.
> Requirements:
> - Ollama should not expose port 11434 to the host (use internal Docker network only)
> - The app depends on Ollama being healthy (model fully downloaded) before starting
> - DB should persist via a volume mount at `./data/vocabgraph.db`
> - Do NOT run it — just prepare the files.

---

### 13. Fix — Expanded children appear in same column as parent

**How to write it:**
> In `main.py`, the expand endpoint (`POST /api/roadmap/nodes/{id}/expand`) places children in the same column as the parent.
> Fix: assign expanded children to a new section named after the parent node, and insert that section right after the parent's section in `rm_technologies.sections`.

---

### 12. Fix — 500 error on expand endpoint

**How to write it:**
> `POST /api/roadmap/nodes/{id}/expand` returns 500 with:
> `AttributeError: 'sqlite3.Row' object has no attribute 'get'`
>
> In `main.py`, `sqlite3.Row` objects support `row["key"]` but NOT `row.get("key")`.
> Find all occurrences of `.get("sections")` on sqlite3.Row objects and replace with `["sections"]`.

---

### 11. Feature — Left-to-right expansion constraint

**How to write it:**
> In the roadmap force-directed graph (`_rmRenderGraph` in `frontend/index.html`), nodes can appear in any x position regardless of their section order.
> Add a `d3.forceX` force based on section index from `rmSections` so nodes are always pushed left-to-right by section.
> Earlier sections = left side, later sections = right side. Strength ~0.35 so it guides without locking.

---

### 10. Reference — Ollama prompts artifact

**How to write it:**
> Create a visual reference page (artifact) showing all 12 prompts sent to Ollama in this project.
> Group them by category (Vocabulary, Roadmap).
> For each prompt show: HTTP method, endpoint, one-line purpose, full prompt text with `{variables}` highlighted, and response type (streaming / JSON / plain text).

---

### 9. Remove — Estimated minutes field

**How to write it:**
> Remove `estimated_minutes` completely from the project because it is unreliable.
> Locations to clean up:
> - Tooltip in path view (`mouseenter` event)
> - Detail panel meta row (`rmMeta` innerHTML)
> - AI prompt schema in `main.py` (all 3 prompts that include it)
> - DB INSERT in `_save_roadmap_batch`
> - CSS class `.rm-time`

---

### 8. Fix — Zoom resets after changing node status

**How to write it:**
> In the roadmap path view, clicking "Got it" or any status button calls `_rmRefreshNodeColors()` → `_rmRenderPath()` which completely re-renders the SVG and resets pan/zoom to the initial fitted transform.
> Fix: before clearing `area.innerHTML`, save the current `d3.zoomTransform` from the SVG element. After creating the new zoom instance, apply the saved transform instead of `initT`.

---

### 7. Feature — Expand queue for multiple nodes

**How to write it:**
> In the roadmap (`frontend/index.html`), currently clicking Expand sends one request and blocks the UI.
> Add a queue system so I can click Expand on multiple nodes quickly:
> - Each click adds the node to a queue and shows "✓ Queued" on the button
> - Process one at a time (sequential, not parallel — Ollama can't handle parallel)
> - Show a floating green badge at bottom-right: "Expanding 'X' · 3 left"
> - Reload the graph after each successful expand
> - Skip duplicate entries (same node already in queue)

---

### 6. Feature — Dynamic topic management (add / delete / overflow)

**How to write it:**
> The roadmap technologies are currently hardcoded in `RM_TECHS` in `frontend/index.html`.
> Replace with dynamic loading from `GET /api/roadmap/technologies` and add:
>
> **Backend** (`main.py`):
> - `POST /api/roadmap/technologies` — create new tech (auto-generate slug ID from name)
> - `DELETE /api/roadmap/technologies/{id}` — cascade delete all nodes and links
>
> **Frontend**:
> - Auto-assign emoji by tech name (map of ~30 common techs, fallback to 📚)
> - Show max 5 tabs; if more, show a "+N" chip that opens a dropdown with the overflow items
> - Each tab has a "×" delete button visible on hover, with confirmation dialog
> - Inline "＋" button that expands to a text input for adding a new topic
> - After add/delete, reload the list from API and switch to the first tech

---

### 5. Feature — Node search in roadmap header

**How to write it:**
> In the roadmap header (`frontend/index.html`), add a search input that:
> - Appears next to the other header controls when a roadmap is loaded
> - Filters nodes by name as you type, showing up to 8 results in a dropdown
> - Highlights the matching text in amber in each result
> - On select: opens the detail panel for that node, switches to graph view, and pans/animates the graph to center on the node
> - Arrow keys + Enter navigate the dropdown; Escape closes it

---

### 4. Feature — Move queue badge to bottom-right

**How to write it:**
> In `frontend/index.html`, the floating queue badge (`#rmQueueBadge`) is positioned at `top:14px;right:14px`.
> Move it to `bottom:14px;right:14px`.

---

### 3. Feature — AI Card queue (regenerate + queue)

**How to write it:**
> In the roadmap detail panel (`frontend/index.html`), the "✦ AI Card" button currently sends one request and blocks.
> Add the same queue system as Expand:
> - Clicking the button adds the node to `_rmCardQueue` and shows "✓ Queued"
> - Process one at a time
> - If the node being generated is currently open in the panel, stream the output live
> - If it is a background node, silently update `node.ai_cache` — when you later open that node, the fresh card shows
> - When you navigate to a node that is in the queue, show "⟳ In queue" on the button (disabled)
> - Share the same badge as the expand queue: "Expanding (2) · AI Card 'X' · 1 left"

---

### 2. Feature — Right-click definition popup on vocab graph nodes

**How to write it:**
> In the main vocab graph (`frontend/index.html`), add a right-click context menu on D3 nodes.
> Menu options:
> - "✦ Search Definition" — opens a centered popup modal
> - "→ Open in panel" — same as normal left-click
>
> The definition popup should:
> - Show the word name and category in the header
> - Check in-memory cache (`aiCache`) and server cache first; only call AI if not cached
> - Stream from `GET /api/ai/stream/{word}` and display live while streaming
> - On complete, format into sections: Definition, Examples (with word highlighted), How to use, Ideas to remember
> - Include a "↺ Regenerate" button that clears cache and re-streams
> - Close on backdrop click or Escape key
> - Reuse the existing `parseCard(raw)` function for parsing

---

### 1. Fix — Selection does not highlight descendant nodes

**How to write it:**
> In `_rmGetFullPath` (`frontend/index.html`), the BFS only traverses backward (ancestor chain: nodes that lead TO the selected node).
> This means when I select "OOP Basics", nodes that come AFTER it are not highlighted — only its prerequisites are.
> Fix: traverse BOTH directions — backward (ancestors/prerequisites) AND forward (descendants/what this unlocks).
> Use two maps: `bwd[target] → [sources]` and `fwd[source] → [targets]`, and BFS both in the same queue.

---

## Checklist Before Sending a Request

- [ ] Did I name the **file or feature** where the change should happen?
- [ ] Did I describe **what currently happens** (the bug or current state)?
- [ ] Did I describe **what should happen** (the expected result)?
- [ ] If it is a bug, did I include the **error message or console log**?
- [ ] If I have multiple requests, did I **number them separately**?
- [ ] Did I mention any **constraint** (e.g. "do not run", "one at a time", "no ellipsis")?
- [ ] Is there a **trigger** I should describe (on click, on hover, on load)?
