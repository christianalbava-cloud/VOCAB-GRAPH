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

## All Project Requests — Written in Human Language

These are the 22 changes made to this project, from most recent to oldest.
Each one shows how you could have written that request — natural, clear, enough detail for the AI to act without guessing.

---

### 22. Fix — Tooltip shows stale node name

> In the roadmap path view, when I hover over a node the tooltip still shows the name of the node I clicked before — not the one I'm hovering now. Fix it so the tooltip always updates to whatever node I'm pointing at.

---

### 21. Fix — Node labels hidden behind glow lines

> In the roadmap path view, the name text on each node is hidden behind the colored lines. I can barely read it. Add some kind of dark outline behind the letters so the text shows on top of everything.

---

### 20. Remove — "Learning Path" right panel

> In the roadmap, remove the right panel that shows "Learning Path". It doesn't give me any useful information, just takes up space.

---

### 19. Multi-part change — 5 things for the roadmap

> I have several changes I want for the roadmap, let me list them:
>
> 1. When I click a node, it highlights every single node in the graph — that's a bug. I want it to only highlight the path that leads to that node from the beginning, like tracing back to the start.
> 2. The left panel where the node names show is fixed and I can't make it wider. The names are getting cut off with "...". I want to be able to drag it to resize.
> 3. The graph only fills about half the screen. I want it to take the full height and width of the page.
> 4. The column labels at the top are plain text and hard to read. Redesign them as something better, like small badges or pills. If the name is long, break it into multiple lines instead of cutting it.

---

### 18. Confirm — Column headers appear automatically for new columns

> When I expand a node and a new column appears in the roadmap path view, does the header for that column also appear automatically? Or do I have to do something manually? I want to confirm it works without any extra step.

---

### 17. Feature — Button to clear the selected path

> In the roadmap path view, after I click a node and the path gets highlighted, I want a button I can click to clear that selection and go back to the normal view. Put it somewhere visible on the graph, only show it when a node is selected, and hide it when I clear.

---

### 16. Fix — Column headers still cutting off with "..."

> The roadmap column headers are still showing "..." at the end of long names. I said no ellipsis — show the full name. If it doesn't fit in one line, break it into up to 4 lines.

---

### 15. Fix — Error in console when I click a node

> I'm getting this error in the console when I click a node in the roadmap:
> `TypeError: Cannot read properties of null (reading 'style') at rmSelectNode`
> Find out what is causing it and fix it.

---

### 14. Feature — Docker setup with Ollama (don't run it)

> I want to containerize this project with Docker and include Ollama so the AI model runs inside the container too. Prepare all the files needed for that — Dockerfile, docker-compose, and whatever else is needed. But don't run it yet, I don't have enough disk space right now.

---

### 13. Fix — Expanded nodes appear in the same column as the parent

> When I click Expand on a node in the roadmap, the new child nodes appear in the same column as the parent instead of creating a new column to the right. They should always go to a new column that comes after the parent.

---

### 12. Fix — 500 error when I expand a node

> Expanding a node gives a 500 error. The console shows:
> `AttributeError: 'sqlite3.Row' object has no attribute 'get'`
> Fix it.

---

### 11. Feature — Graph should always grow left to right

> In the roadmap graph view, when I expand nodes the new ones sometimes appear to the left or behind other nodes. I want the graph to always flow left to right — earlier concepts on the left, more advanced ones on the right. Add that as a restriction so it never goes backwards.

---

### 10. Reference — Show me all the prompts sent to Ollama

> I want to see all the prompts this app sends to Ollama in one place so I can understand how it communicates with the AI. Create a page or document that lists all of them, organized by what they do.

---

### 9. Remove — The "~75 min" time estimate

> The nodes show something like "★★★☆☆ ~75 min" — I don't know where that 75 minutes comes from and I don't trust it. Remove that time estimate everywhere it appears. Keep the stars rating but get rid of the minutes.

---

### 8. Fix — Changing a node's status resets the zoom

> In the roadmap path view, when I click "Got it" or any of the status buttons on a node, the whole graph resets and I lose my zoom position. I want it to stay exactly where I was — same zoom, same position — after I change the status.

---

### 7. Feature — Queue for expanding multiple nodes

> Right now if I want to expand several nodes I have to wait for each one to finish before clicking the next. I want to be able to click Expand on many nodes one after another without waiting, and the app processes them one by one in the background. Show me somewhere on screen how many are pending.

---

### 6. Feature — Add and delete topics, handle more than 5

> The roadmap currently only has Ruby on Rails and C# hardcoded. I want to:
> - Add new topics from the UI, like typing "TypeScript" and it creates one
> - Delete topics I don't want anymore
> - If I have more than 5 topics, don't show them all as tabs — show 5 and a "+N" button that opens a list of the rest
> - Each topic tab should have a small delete button on hover

---

### 5. Feature — Search for a node in the roadmap

> Add a search box somewhere in the roadmap header so I can type a node name and jump to it. When I select a result it should open that node's detail panel and move the graph to show that node in the center.

---

### 4. Move — Queue badge to bottom right

> The badge that shows "AI Card 'Polymorphism' · 3 left" is appearing at the top right. Move it to the bottom right corner.

---

### 3. Feature — Queue for AI Card, also allow regenerating

> The AI Card button in the roadmap only lets me load it once. I want two things:
> - Be able to regenerate the card for a node I already loaded, to get fresh information
> - Same queue behavior as Expand — I can click AI Card on several nodes and they process one by one in the background. If I'm looking at the node that's currently being generated, I see it streaming live. If it's a different node, it just updates quietly in the background.

---

### 2. Feature — Right-click on a word to search its definition

> In the main vocabulary graph, when I right-click on a word node I want a small menu to appear with an option like "Search Definition". Clicking that opens a popup that asks the AI for the definition, examples, how to use it, and ideas to remember it — all in one popup. Similar to what the detail panel shows but without having to select the node.

---

### 1. Fix — Selecting a node only highlights nodes behind it, not in front

> In the roadmap path view, when I select a node like "OOP Basics" it only highlights the nodes that come before it — the ones that lead to it. But it doesn't highlight what comes after, the nodes that OOP Basics leads to. I need to see both directions: where it came from and where it goes.

---

## Checklist Before Sending a Request

- [ ] Did I name the **file or feature** where the change should happen?
- [ ] Did I describe **what currently happens** (the bug or current state)?
- [ ] Did I describe **what should happen** (the expected result)?
- [ ] If it is a bug, did I include the **error message or console log**?
- [ ] If I have multiple requests, did I **number them separately**?
- [ ] Did I mention any **constraint** (e.g. "do not run", "one at a time", "no ellipsis")?
- [ ] Is there a **trigger** I should describe (on click, on hover, on load)?
