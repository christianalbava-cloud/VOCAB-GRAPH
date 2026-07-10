"""
VocabGraph — FastAPI backend
Handles: SQLite persistence, LLM proxy (Ollama or Groq), semantic similarity, streaming
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, AsyncGenerator
import sqlite3, json, httpx, asyncio, re, os, random
from pathlib import Path
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama")   # "ollama" | "groq"

# Ollama settings (used when LLM_PROVIDER=ollama)
OLLAMA_URL   = os.getenv("OLLAMA_URL",   "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

# Groq settings (used when LLM_PROVIDER=groq)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL",   "llama-3.3-70b-versatile")

DB_PATH = os.getenv("DB_PATH", "vocabgraph.db")

def _active_model() -> str:
    return GROQ_MODEL if LLM_PROVIDER == "groq" else OLLAMA_MODEL

# ── LLM ADAPTERS ─────────────────────────────────────────────
async def _llm_generate(prompt: str, timeout: int = 60) -> str:
    """Non-streaming: returns the full response text."""
    if LLM_PROVIDER == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=GROQ_API_KEY)
        resp = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            timeout=timeout,
        )
        return resp.choices[0].message.content or ""
    else:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            )
            return r.json().get("response", "")

async def _llm_stream(prompt: str) -> AsyncGenerator[str, None]:
    """Streaming: async generator that yields text chunks."""
    if LLM_PROVIDER == "groq":
        from groq import AsyncGroq
        client = AsyncGroq(api_key=GROQ_API_KEY)
        stream = await client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        )
        async for chunk in stream:
            text = chunk.choices[0].delta.content or ""
            if text:
                yield text
    else:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", f"{OLLAMA_URL}/api/generate",
                json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": True},
            ) as r:
                async for line in r.aiter_lines():
                    if line:
                        try:
                            chunk = json.loads(line)
                            if chunk.get("response"):
                                yield chunk["response"]
                        except Exception:
                            pass

app = FastAPI(title="VocabGraph API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── DATABASE ──────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id          TEXT PRIMARY KEY,
                cat         TEXT NOT NULL DEFAULT 'concept',
                notes       TEXT DEFAULT '',
                ai_cache    TEXT DEFAULT '',
                image_url   TEXT DEFAULT '',
                weight      INTEGER DEFAULT 1,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS links (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                source      TEXT NOT NULL,
                target      TEXT NOT NULL,
                score       REAL DEFAULT 0.5,
                reason      TEXT DEFAULT '',
                link_type   TEXT DEFAULT 'semantic',
                created_at  TEXT DEFAULT (datetime('now')),
                UNIQUE(source, target)
            );

            CREATE TABLE IF NOT EXISTS sim_cache (
                word        TEXT PRIMARY KEY,
                results     TEXT NOT NULL,
                model       TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rm_technologies (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                description TEXT DEFAULT '',
                focus       TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rm_nodes (
                id                  TEXT PRIMARY KEY,
                tech_id             TEXT NOT NULL,
                name                TEXT NOT NULL,
                category            TEXT DEFAULT 'fundamentals',
                difficulty          INTEGER DEFAULT 1,
                estimated_minutes   INTEGER DEFAULT 30,
                status              TEXT DEFAULT 'not_started',
                ai_cache            TEXT DEFAULT '',
                personal_notes      TEXT DEFAULT '',
                priority            INTEGER DEFAULT 0,
                created_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rm_links (
                source  TEXT NOT NULL,
                target  TEXT NOT NULL,
                type    TEXT DEFAULT 'prerequisite',
                PRIMARY KEY (source, target)
            );
        """)
        # migrate: add columns added after initial release
        cols = [r[1] for r in db.execute("PRAGMA table_info(nodes)").fetchall()]
        if "image_url" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN image_url TEXT DEFAULT ''")
        if "weight" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN weight INTEGER DEFAULT 1")
        if "knowledge_level" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN knowledge_level TEXT DEFAULT 'new'")
        if "last_reviewed" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN last_reviewed TEXT DEFAULT NULL")
        if "review_count" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN review_count INTEGER DEFAULT 0")
        if "correct_count" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN correct_count INTEGER DEFAULT 0")

        # migrate: jargon → concept, temporal → phrase (both categories removed)
        db.execute("UPDATE nodes SET cat='concept' WHERE cat='jargon'")
        db.execute("UPDATE nodes SET cat='phrase'  WHERE cat='temporal'")

        # migrate: add columns to rm_technologies and rm_nodes
        rm_tech_cols = [r[1] for r in db.execute("PRAGMA table_info(rm_technologies)").fetchall()]
        if "focus" not in rm_tech_cols:
            db.execute("ALTER TABLE rm_technologies ADD COLUMN focus TEXT DEFAULT ''")
        if "sections" not in rm_tech_cols:
            db.execute("ALTER TABLE rm_technologies ADD COLUMN sections TEXT DEFAULT '[]'")
        rm_node_cols = [r[1] for r in db.execute("PRAGMA table_info(rm_nodes)").fetchall()]
        if "section" not in rm_node_cols:
            db.execute("ALTER TABLE rm_nodes ADD COLUMN section TEXT DEFAULT ''")

        # seed roadmap technologies
        db.executemany(
            "INSERT OR IGNORE INTO rm_technologies (id, name, description) VALUES (?,?,?)",
            [
                ("rubyonrails", "Ruby on Rails", "Full-stack MVC web framework for Ruby"),
                ("csharp",      "C#",            "Object-oriented language by Microsoft for .NET"),
            ]
        )

        # seed data if empty
        count = db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if count == 0:
            _seed_db(db)
    print(f"[DB] Initialized at {DB_PATH}")

def _seed_db(db):
    seed_nodes = [
        ("idempotent",           "concept"),
        ("bottleneck",           "concept"),
        ("latency",              "jargon"),
        ("throughput",           "jargon"),
        ("scalability",          "concept"),
        ("race condition",       "concept"),
        ("fault tolerant",       "phrase"),
        ("graceful degradation", "phrase"),
        ("load balancer",        "jargon"),
        ("redundancy",           "concept"),
        ("in the pipeline",      "phrase"),
        ("moving forward",       "phrase"),
        ("bandwidth",            "jargon"),
        ("deadlock",             "concept"),
        ("at the end of the day","phrase"),
    ]
    seed_links = [
        ("idempotent",           "race condition",       0.70, "operation safety"),
        ("idempotent",           "fault tolerant",       0.65, "resilient design"),
        ("latency",              "throughput",           0.80, "core perf metrics"),
        ("latency",              "bottleneck",           0.75, "latency reveals bottlenecks"),
        ("bottleneck",           "scalability",          0.72, "bottlenecks limit scale"),
        ("scalability",          "load balancer",        0.78, "LBs enable scaling"),
        ("scalability",          "redundancy",           0.68, "redundancy aids scale"),
        ("fault tolerant",       "graceful degradation", 0.85, "both resilience strategies"),
        ("fault tolerant",       "redundancy",           0.76, "redundancy enables FT"),
        ("load balancer",        "throughput",           0.70, "LBs improve throughput"),
        ("bandwidth",            "latency",              0.72, "network perf pair"),
        ("bandwidth",            "throughput",           0.80, "closely related metrics"),
        ("deadlock",             "race condition",       0.82, "both concurrency bugs"),
        ("in the pipeline",      "moving forward",       0.60, "progress expressions"),
    ]
    db.executemany("INSERT OR IGNORE INTO nodes (id,cat) VALUES (?,?)", seed_nodes)
    db.executemany(
        "INSERT OR IGNORE INTO links (source,target,score,reason) VALUES (?,?,?,?)",
        seed_links
    )

init_db()

# ── MODELS ───────────────────────────────────────────────────
class NodeIn(BaseModel):
    id:    str
    cat:   str = "concept"
    notes: str = ""

class LinkIn(BaseModel):
    source:    str
    target:    str
    score:     float = 0.5
    reason:    str   = ""
    link_type: str   = "semantic"

class AICacheIn(BaseModel):
    word:  str
    cache: str

# ── HELPERS ──────────────────────────────────────────────────
def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

def _parse_card_full(ai_cache: str) -> dict:
    """Parse all sections of a LLM card into structured lists."""
    result = {'definition': [], 'examples': [], 'speak': [], 'ideas': [], 'related': []}
    current = None
    markers = {
        '## DEFINITION':       'definition',
        '## EXAMPLES':         'examples',
        '## HOW TO SPEAK':     'speak',
        '## IDEAS TO REMEMBER':'ideas',
        '## RELATED WORDS':    'related',
    }
    for line in ai_cache.split('\n'):
        stripped = line.strip()
        upper = stripped.upper()
        switched = False
        for key, sec in markers.items():
            if key in upper:
                current = sec
                switched = True
                break
        if switched or not current or not stripped:
            continue
        clean = re.sub(r'<[^>]+>', '', stripped)
        clean = clean.lstrip('▸').lstrip('💡').lstrip('•').lstrip('-').strip()
        if clean:
            result[current].append(clean)
    return result

def _build_detective_clues(word: str, cat: str, card: dict) -> list:
    """Build contextual clues hardest → easiest, mining the cached LLM card."""
    word_parts = word.split()
    total_letters = sum(len(w) for w in word_parts)

    def blank(text: str) -> str:
        out = text.strip().strip('"').strip("'")   # remove surrounding quotes from LLM
        out = re.sub(r'<<[^>]+>>', '<strong>____</strong>', out)
        if len(word_parts) == 1:
            out = re.sub(r'\b' + re.escape(word) + r'\b', '<strong>____</strong>', out, flags=re.IGNORECASE)
        else:
            out = re.sub(re.escape(word), '<strong>____</strong>', out, flags=re.IGNORECASE)
        return out

    clues = []

    # ── Clue 1 (hardest): fill-in-the-blank from EXAMPLES ──────
    for ex in card.get('examples', []):
        cleaned = re.sub(r'^\[[^\]]+\]:\s*', '', ex)  # strip [LABEL]:
        b = blank(cleaned)
        if '<strong>____</strong>' in b and len(b) > 25:
            clues.append(f'Fill in the blank: "{b}"')
            break

    # ── Clue 2: HOW TO SPEAK phrase with word blanked ────────────
    for phrase in card.get('speak', []):
        b = blank(phrase)
        if '<strong>____</strong>' in b and len(b) > 12:
            clues.append(f'In a technical context, someone said: "{b}"')
            break
    else:
        # fallback: use a speak phrase that doesn't contain the word as-is (still contextual)
        for phrase in card.get('speak', []):
            if word.lower() not in phrase.lower() and len(phrase) > 15:
                clues.append(f'In a technical context: "{phrase}"')
                break

    # ── Clue 3: mnemonic / analogy from IDEAS ───────────────────
    for idea in card.get('ideas', []):
        b = blank(idea)
        if len(idea) > 20:
            clues.append(f'💡 A way to remember it: "{b}"')
            break

    # ── Clue 4: second example or definition fragment ────────────
    example_count = 0
    for ex in card.get('examples', []):
        cleaned = re.sub(r'^\[[^\]]+\]:\s*', '', ex)
        b = blank(cleaned)
        if '<strong>____</strong>' in b and len(b) > 25:
            example_count += 1
            if example_count == 2:          # skip first (used in clue 1)
                clues.append(f'Another context: "{b}"')
                break

    # ── Clue 5: category + word structure ───────────────────────
    cat_labels = {
        'concept':  'a technical or abstract concept',
        'phrase':   'an English phrase or expression',
        'composed': 'a composed multi-word term',
    }
    label = cat_labels.get(cat, f'a {cat}')
    if len(word_parts) == 1:
        clues.append(f'It is {label} ({total_letters} letters).')
    else:
        clues.append(f'It is {label} made of {len(word_parts)} words ({total_letters} letters total).')

    # ── Clue 6: first letter + pattern ──────────────────────────
    pattern = '  '.join('_' * len(w) for w in word_parts)
    clues.append(f'Starts with <strong>"{word[0].upper()}"</strong> — pattern: <code>{pattern}</code>')

    # ── Clue 7 (easiest): definition excerpt ────────────────────
    defn = ' '.join(card.get('definition', []))
    if defn:
        excerpt = ' '.join(defn.split()[:13])
        clues.append(f'Its definition begins: "<em>{excerpt}…</em>"')

    return [c for c in clues if c]

def _clean_def_text(text: str, strip_word: str = "", blank_word: str = "") -> str:
    """Strip markdown/HTML, remove leading 'Word:' prefix, optionally blank a word."""
    # strip HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # strip markdown bold/italic/code/blockquote/headers
    text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)
    text = re.sub(r'\*([^*]+)\*',     r'\1', text)
    text = re.sub(r'`([^`]+)`',       r'\1', text)
    text = re.sub(r'^>\s*',           '',    text, flags=re.MULTILINE)
    text = re.sub(r'^#+\s*',          '',    text, flags=re.MULTILINE)
    text = text.strip()
    # remove leading (optionally quoted) "Word:" / "Word -" / "Word " prefix patterns
    if strip_word:
        esc = re.escape(strip_word)
        # matches: Word: / "Word": / "Word" - / Word -
        text = re.sub(r'^["\']?' + esc + r'["\']?\s*[:\-]\s*', '', text, flags=re.IGNORECASE)
    # blank the word inside the text
    if blank_word:
        if ' ' in blank_word:
            text = re.sub(re.escape(blank_word), '____', text, flags=re.IGNORECASE)
        else:
            text = re.sub(r'\b' + re.escape(blank_word) + r'\b', '____', text, flags=re.IGNORECASE)
        # also remove surrounding quotes that now wrap ____ e.g. "____" → ____
        text = re.sub(r'["\']____["\']', '____', text)
    return text.strip()

def _parse_definition(ai_cache: str) -> str:
    """Extract plain-text definition from cached LLM output."""
    if not ai_cache:
        return ""
    in_def = False
    parts = []
    for line in ai_cache.split("\n"):
        if "## DEFINITION" in line.upper():
            in_def = True
            continue
        if in_def and line.startswith("##"):
            break
        if in_def:
            stripped = re.sub(r'<[^>]+>', '', line).strip()
            if stripped:
                parts.append(stripped)
    return " ".join(parts)[:400]

def _auto_knowledge_level(review_count: int, correct_count: int) -> str:
    if review_count == 0:
        return "new"
    trust = correct_count / review_count
    if trust >= 0.8 and review_count >= 5:
        return "mastered"
    if trust >= 0.6 and review_count >= 3:
        return "learned"
    return "new"

# ── HEALTH ───────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    provider_ok = False
    models = []
    if LLM_PROVIDER == "groq":
        provider_ok = bool(GROQ_API_KEY)
        models = [GROQ_MODEL]
    else:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                r = await client.get(f"{OLLAMA_URL}/api/tags")
                if r.status_code == 200:
                    provider_ok = True
                    models = [m["name"] for m in r.json().get("models", [])]
        except Exception:
            pass
    return {
        "api":      "ok",
        "ollama":   provider_ok,
        "provider": LLM_PROVIDER,
        "model":    _active_model(),
        "models":   models,
    }

# ── NODES ────────────────────────────────────────────────────
@app.get("/api/nodes")
def get_nodes():
    with get_db() as db:
        rows = db.execute("SELECT * FROM nodes ORDER BY created_at").fetchall()
    return rows_to_list(rows)

@app.post("/api/nodes")
def create_node(node: NodeIn):
    with get_db() as db:
        existing = db.execute("SELECT id FROM nodes WHERE id=?", (node.id,)).fetchone()
        if existing:
            raise HTTPException(400, f"Node '{node.id}' already exists")
        db.execute(
            "INSERT INTO nodes (id,cat,notes) VALUES (?,?,?)",
            (node.id, node.cat, node.notes)
        )
    return {"ok": True, "id": node.id}

@app.delete("/api/nodes/{node_id}")
def delete_node(node_id: str):
    with get_db() as db:
        db.execute("DELETE FROM nodes WHERE id=?", (node_id,))
        db.execute("DELETE FROM links WHERE source=? OR target=?", (node_id, node_id))
        db.execute("DELETE FROM sim_cache WHERE word=?", (node_id,))
    return {"ok": True}

@app.get("/api/nodes/{node_id}/cache")
def get_cache(node_id: str):
    with get_db() as db:
        row = db.execute("SELECT ai_cache FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Node not found")
    return {"word": node_id, "cache": row["ai_cache"]}

@app.post("/api/nodes/{node_id}/cache")
def set_cache(node_id: str, body: AICacheIn):
    with get_db() as db:
        db.execute("UPDATE nodes SET ai_cache=? WHERE id=?", (body.cache, node_id))
    return {"ok": True}

@app.get("/api/nodes/{node_id}/image")
def get_image(node_id: str):
    with get_db() as db:
        row = db.execute("SELECT image_url FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Node not found")
    return {"word": node_id, "image_url": row["image_url"]}

@app.post("/api/nodes/{node_id}/image")
def set_image(node_id: str, body: dict):
    url = body.get("url", "")
    with get_db() as db:
        db.execute("UPDATE nodes SET image_url=? WHERE id=?", (url, node_id))
    return {"ok": True}

@app.post("/api/nodes/{node_id}/weight/increment")
def increment_weight(node_id: str):
    with get_db() as db:
        db.execute("UPDATE nodes SET weight = weight + 1 WHERE id=?", (node_id,))
        row = db.execute("SELECT weight FROM nodes WHERE id=?", (node_id,)).fetchone()
    return {"ok": True, "weight": row["weight"] if row else 1}

@app.post("/api/nodes/{node_id}/weight")
def set_weight(node_id: str, body: dict):
    w = max(1, int(body.get("weight", 1)))
    with get_db() as db:
        db.execute("UPDATE nodes SET weight=? WHERE id=?", (w, node_id))
        row = db.execute("SELECT weight FROM nodes WHERE id=?", (node_id,)).fetchone()
    return {"ok": True, "weight": row["weight"] if row else w}

# ── LINKS ────────────────────────────────────────────────────
@app.get("/api/links")
def get_links():
    with get_db() as db:
        # only return links where both endpoints exist as nodes
        rows = db.execute("""
            SELECT l.* FROM links l
            WHERE EXISTS (SELECT 1 FROM nodes n WHERE n.id = l.source)
              AND EXISTS (SELECT 1 FROM nodes n WHERE n.id = l.target)
            ORDER BY l.score DESC
        """).fetchall()
    return rows_to_list(rows)

@app.post("/api/links")
def create_link(link: LinkIn):
    with get_db() as db:
        db.execute(
            """INSERT INTO links (source,target,score,reason,link_type)
               VALUES (?,?,?,?,?)
               ON CONFLICT(source,target) DO UPDATE SET score=excluded.score, reason=excluded.reason""",
            (link.source, link.target, link.score, link.reason, link.link_type)
        )
    return {"ok": True}

@app.post("/api/links/batch")
def create_links_batch(links: list[LinkIn]):
    with get_db() as db:
        for link in links:
            db.execute(
                """INSERT INTO links (source,target,score,reason,link_type)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(source,target) DO UPDATE SET score=excluded.score, reason=excluded.reason""",
                (link.source, link.target, link.score, link.reason, link.link_type)
            )
    return {"ok": True, "count": len(links)}

# ── SIM CACHE ────────────────────────────────────────────────
@app.get("/api/sim/{word}")
def get_sim(word: str):
    with get_db() as db:
        row = db.execute("SELECT results FROM sim_cache WHERE word=?", (word,)).fetchone()
    if not row:
        raise HTTPException(404, "No similarity cache for this word")
    return {"word": word, "results": json.loads(row["results"])}

@app.post("/api/sim/{word}")
def set_sim(word: str, body: dict):
    results = body.get("results", [])
    with get_db() as db:
        db.execute(
            """INSERT INTO sim_cache (word,results,model) VALUES (?,?,?)
               ON CONFLICT(word) DO UPDATE SET results=excluded.results, model=excluded.model""",
            (word, json.dumps(results), _active_model())
        )
    return {"ok": True}

# ── CATEGORY ─────────────────────────────────────────────────
VALID_CATS = {"concept", "phrase", "composed"}

@app.patch("/api/nodes/{node_id}/category")
def update_category(node_id: str, body: dict):
    cat = body.get("cat", "").strip()
    if cat not in VALID_CATS:
        raise HTTPException(400, f"Invalid category. Must be one of: {', '.join(VALID_CATS)}")
    with get_db() as db:
        db.execute("UPDATE nodes SET cat=? WHERE id=?", (cat, node_id))
    return {"ok": True, "id": node_id, "cat": cat}

@app.post("/api/ai/suggest-category")
async def suggest_category(body: dict):
    word = body.get("word", "").strip()
    if not word:
        raise HTTPException(400, "word required")
    prompt = f"""Classify the English word or phrase "{word}" into exactly one category.
Categories:
- concept: a technical or abstract idea (e.g. latency, idempotent, abstraction, bottleneck)
- phrase: a fixed expression, idiom, multi-word expression, or time-related expression (e.g. "moving forward", "touch base", "in the meantime", "prior to")

Reply with ONLY the single word: concept or phrase. No explanation."""
    try:
        text = (await _llm_generate(prompt, timeout=10)).strip().lower()
        cat = text if text in {"concept", "phrase"} else "concept"
    except Exception:
        cat = "concept"
    return {"cat": cat}

# ── EXPORT ───────────────────────────────────────────────────
@app.get("/api/export")
def export_graph():
    with get_db() as db:
        nodes = rows_to_list(db.execute(
            "SELECT id,cat,notes,created_at,ai_cache,image_url,weight FROM nodes"
        ).fetchall())
        links = rows_to_list(db.execute(
            "SELECT source,target,score,reason,link_type FROM links"
        ).fetchall())
    return {
        "exported_at": datetime.utcnow().isoformat(),
        "version":     2,
        "model":       _active_model(),
        "nodes":       nodes,
        "links":       links,
    }

# ── IMPORT ───────────────────────────────────────────────────
@app.post("/api/import")
def import_graph(body: dict):
    """
    body: { nodes: [...], links: [...], mode: "merge"|"replace" }
    merge  = skip nodes/links that already exist (default)
    replace = wipe everything first, then insert
    """
    mode  = body.get("mode", "merge")
    nodes = body.get("nodes", [])
    links = body.get("links", [])

    added_nodes = 0
    skipped_nodes = 0
    added_links = 0
    skipped_links = 0

    with get_db() as db:
        if mode == "replace":
            db.execute("DELETE FROM links")
            db.execute("DELETE FROM nodes")

        for n in nodes:
            nid = n.get("id","").strip()
            if not nid:
                continue
            existing = db.execute("SELECT id FROM nodes WHERE id=?", (nid,)).fetchone()
            if existing and mode == "merge":
                skipped_nodes += 1
                continue
            db.execute("""
                INSERT OR REPLACE INTO nodes(id,cat,notes,created_at,ai_cache,image_url,weight)
                VALUES(?,?,?,?,?,?,?)
            """, (
                nid,
                n.get("cat","concept"),
                n.get("notes",""),
                n.get("created_at", datetime.utcnow().isoformat()),
                n.get("ai_cache",""),
                n.get("image_url",""),
                n.get("weight", 1),
            ))
            added_nodes += 1

        for l in links:
            src = l.get("source","").strip()
            tgt = l.get("target","").strip()
            if not src or not tgt:
                continue
            existing = db.execute(
                "SELECT rowid FROM links WHERE source=? AND target=?", (src, tgt)
            ).fetchone()
            if existing and mode == "merge":
                skipped_links += 1
                continue
            db.execute("""
                INSERT OR REPLACE INTO links(source,target,score,reason,link_type)
                VALUES(?,?,?,?,?)
            """, (
                src, tgt,
                l.get("score", 0.5),
                l.get("reason",""),
                l.get("link_type","semantic"),
            ))
            added_links += 1

    return {
        "ok": True,
        "added_nodes":   added_nodes,
        "skipped_nodes": skipped_nodes,
        "added_links":   added_links,
        "skipped_links": skipped_links,
    }

# ── AI: STREAM KNOWLEDGE CARD ────────────────────────────────
@app.get("/api/ai/stream/{word}")
async def stream_card(word: str, cat: str = "concept", notes: str = ""):
    """Stream knowledge card from Ollama"""
    cat_desc = {
        "concept":  "an abstract or technical concept in systems engineering",
        "phrase":   "an English phrase or expression",
        "temporal": "an English phrase or expression",
        "jargon":   "technical industry jargon in systems engineering",
    }.get(cat, "a word or expression")

    prompt = f"""You are teaching English to a systems engineer who speaks Spanish as their native language.
They want to learn: "{word}" — which is {cat_desc}.
{f'Their context: "{notes}"' if notes else ''}

Provide a complete learning card. Use these exact section headers on their own line:

## DEFINITION
Write a clear 2-3 sentence definition. Avoid complex words. Explain what it does in a system if technical.

## EXAMPLES
Write 3 examples. Each on its own line. Format: [LABEL]: sentence
Labels must be: [Systems Engineering], [General English], [Conversation]
Surround the word/phrase with <<double angle brackets>> in each sentence.

## HOW TO SPEAK
Write 3 short practical phrases the engineer can say in meetings or work conversations.
Each on a new line starting with ▸

## IDEAS TO REMEMBER
Write 3 creative ideas, mnemonics, or analogies to remember this word.
Each on its own line starting with 💡

## RELATED WORDS
List 5 related words or phrases, comma-separated on one line.

Keep everything simple, clear, and useful for a systems engineer learning English."""

    async def generate():
        try:
            async for chunk in _llm_stream(prompt):
                yield chunk
        except Exception as e:
            yield f"\n\n[Error: {e}]"

    return StreamingResponse(generate(), media_type="text/plain")

# ── AI: SEMANTIC SIMILARITY ───────────────────────────────────
@app.post("/api/ai/similarity")
async def compute_similarity(body: dict):
    """Ask Qwen to find semantically related nodes"""
    word     = body.get("word", "")
    cat      = body.get("cat", "concept")
    existing = body.get("existing", [])

    if not existing:
        return {"results": []}

    # send max 40 nodes to keep prompt short
    sample = existing[:40]

    prompt = f"""You are a semantic similarity assistant for a vocabulary learning graph.

New word: "{word}" (category: {cat})

Existing nodes:
{chr(10).join(f'{i+1}. {n}' for i,n in enumerate(sample))}

Find the 4 most semantically related nodes to "{word}".
Respond ONLY with a raw JSON array. No markdown. No explanation. No extra text.
Format:
[
  {{"word":"node name","score":0.85,"reason":"short reason max 6 words"}},
  {{"word":"node name","score":0.72,"reason":"short reason max 6 words"}}
]
Only include nodes with score >= 0.5. Maximum 4 results."""

    try:
        raw = await _llm_generate(prompt, timeout=60)
        # extract JSON array robustly
        start = raw.find("[")
        end   = raw.rfind("]")
        if start == -1 or end == -1:
            return {"results": []}
        arr = json.loads(raw[start:end+1])
        results = [
            {"word": a["word"], "score": float(a["score"]), "reason": a.get("reason","")}
            for a in arr
            if isinstance(a, dict) and "word" in a and float(a.get("score",0)) >= 0.5
            and a["word"] in existing
        ]
        return {"results": results}
    except Exception as e:
        print(f"[Similarity error] {e}")
        return {"results": []}

# ── AI: SPELL / GRAMMAR CHECK ────────────────────────────────
@app.post("/api/ai/spellcheck")
async def spellcheck(body: dict):
    word = body.get("word", "").strip()
    if not word:
        return {"changed": False, "correction": word}

    prompt = f"""You are a spelling and grammar checker for an English vocabulary learning app.
The user wants to add this word or phrase: "{word}"

Rules:
- If it is correctly spelled and grammatically valid as a vocabulary item, reply with exactly: CORRECT
- If there is a spelling or grammar mistake, reply with exactly: CORRECTION: <corrected version>
- Do NOT change proper nouns, technical acronyms, or intentional stylizations.
- Do NOT add extra explanation. Output ONLY "CORRECT" or "CORRECTION: <text>"."""

    try:
        raw = (await _llm_generate(prompt, timeout=20)).strip()
        upper = raw.upper()
        if upper.startswith("CORRECTION:"):
            correction = raw[len("CORRECTION:"):].strip().strip('"\'')
            changed = correction.lower() != word.lower()
            return {"changed": changed, "correction": correction if changed else word}
        return {"changed": False, "correction": word}
    except Exception:
        return {"changed": False, "correction": word}

# ── AI: VISUAL PROMPT FOR IMAGE GENERATION ───────────────────
@app.get("/api/ai/visual-prompt/{word}")
async def visual_prompt(word: str):
    """Ask Qwen to produce a short visual scene description for Pollinations.ai"""
    prompt = f'In 10 words or less, describe a simple visual scene that helps remember the concept "{word}" in systems engineering. Only output the scene description, nothing else.'
    try:
        text = (await _llm_generate(prompt, timeout=30)).strip().replace("\n", " ")
        return {"prompt": text}
    except Exception:
        return {"prompt": word}

# ── AI: PHRASE ANALYSIS ───────────────────────────────────────
@app.post("/api/ai/phrase")
async def analyze_phrase(body: dict):
    """Analyze a combination of words as a phrase"""
    words = body.get("words", [])
    if len(words) < 2:
        raise HTTPException(400, "Need at least 2 words")

    prompt = f"""A systems engineer is learning English. They combined these concepts: {', '.join(f'"{w}"' for w in words)}.
1. Do these words form a real English phrase or expression? If yes, what does it mean?
2. How are these concepts connected in systems engineering?
3. Two example sentences showing how these ideas relate in a technical context.
4. One tip on how to use this combination when speaking in meetings.
Keep it short, practical, and easy to understand."""

    async def generate():
        try:
            async for chunk in _llm_stream(prompt):
                yield chunk
        except Exception as e:
            yield f"\n\n[Error: {e}]"

    return StreamingResponse(generate(), media_type="text/plain")

# ── AI: VERB TENSES ──────────────────────────────────────────
@app.get("/api/ai/tenses/{word}")
async def word_tenses(word: str):
    prompt = f"""You are an English grammar teacher for a Spanish-speaking systems engineer.
The word or phrase to study is: "{word}"

First, determine if "{word}" is a verb or not.

If it is NOT a verb, output only:
## NOT A VERB
State what part of speech it is and any relevant grammatical forms (plural, adjective degrees, etc.).

If it IS a verb (or can be used as one), output each section below with the exact header, the conjugation, and 3 example sentences in a systems engineering or work context.

## INFINITIVE
Conjugation: to ___
Example 1: "..."
Example 2: "..."
Example 3: "..."

## PRESENT SIMPLE
Conjugation:
I / You / We / They: ___
He / She / It: ___
Example 1: "..."
Example 2: "..."
Example 3: "..."

## PRESENT CONTINUOUS
Conjugation: I am ___ing / He is ___ing / They are ___ing
Example 1: "..."
Example 2: "..."
Example 3: "..."

## PAST SIMPLE
Conjugation: I / He / She / We / They: ___
Example 1: "..."
Example 2: "..."
Example 3: "..."

## PAST PARTICIPLE
Conjugation: have/has ___
Example 1: "..."
Example 2: "..."
Example 3: "..."

## FUTURE SIMPLE
Conjugation: will ___
Example 1: "..."
Example 2: "..."
Example 3: "..."

## PRESENT PERFECT
Conjugation: have/has + past participle
Example 1: "..."
Example 2: "..."
Example 3: "..."

Keep it simple and practical for a non-native speaker. Fill every ___ with the real form of "{word}"."""

    async def generate():
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream(
                    "POST", f"{OLLAMA_URL}/api/generate",
                    json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": True},
                ) as r:
                    async for line in r.aiter_lines():
                        if line:
                            try:
                                chunk = json.loads(line)
                                if chunk.get("response"):
                                    yield chunk["response"]
                            except Exception:
                                pass
        except Exception as e:
            yield f"\n\n[Error: {e}]"

    return StreamingResponse(generate(), media_type="text/plain")

# ── AI: TRANSLATE DEFINITION TO SPANISH ──────────────────────
@app.post("/api/ai/translate")
async def translate_definition(body: dict):
    word       = body.get("word", "")
    definition = body.get("definition", "").strip()
    analogy    = body.get("analogy", "").strip()

    if not definition:
        return {"definition_es": "", "analogy_es": ""}

    prompt = f"""Translate the following English text to Spanish for a systems engineer learning English.
Keep technical terms in English (API, latency, bottleneck, deploy, cache, thread, etc).
Be natural and clear. Use simple Spanish.

Word being studied: "{word}"
English definition: {definition}
{"English analogy/mnemonic: " + analogy if analogy else ""}

Respond ONLY with a raw JSON object. No markdown. No explanation. No extra text.
Format: {{"definition_es": "...", "analogy_es": "..."}}
If there is no analogy provided, set analogy_es to "".
"""
    try:
        raw = await _llm_generate(prompt, timeout=40)
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            return {"definition_es": "", "analogy_es": ""}
        data = json.loads(raw[start:end])
        return {
            "definition_es": data.get("definition_es", ""),
            "analogy_es":    data.get("analogy_es", ""),
        }
    except Exception as e:
        print(f"[Translate error] {e}")
        return {"definition_es": "", "analogy_es": ""}

# ── DETECTIVE MODE ────────────────────────────────────────────

@app.get("/api/detective/challenge")
def detective_challenge(level: int = 3):
    """Return a word challenge with clues. Level 1=easy (5 clues), 5=hard (1 clue)."""
    n_clues = max(1, min(5, 6 - level))  # level1→5 clues, level5→1 clue

    with get_db() as db:
        rows = db.execute(
            "SELECT id, cat, ai_cache FROM nodes WHERE ai_cache != '' AND cat='concept' ORDER BY RANDOM() LIMIT 30"
        ).fetchall()

    if not rows:
        raise HTTPException(404, "No concept-category words with AI data yet. Add concept words first.")

    chosen = None
    definition = ""
    for row in rows:
        d = _parse_definition(row["ai_cache"])
        if len(d) > 20:
            chosen = row
            definition = d
            break

    if not chosen:
        raise HTTPException(404, "No words with parseable definitions found.")

    word = chosen["id"]
    cat  = chosen["cat"]

    card      = _parse_card_full(chosen["ai_cache"])
    clue_pool = _build_detective_clues(word, cat, card)

    # take n_clues from the hardest end; show them in hardest→easiest order
    selected = clue_pool[:max(1, min(n_clues, len(clue_pool)))]
    return {"word": word, "cat": cat, "clues": selected, "level": level}


@app.post("/api/detective/result")
def detective_result(body: dict):
    """Record a detective challenge result and update knowledge level."""
    word    = body.get("word", "").strip()
    correct = bool(body.get("correct", False))
    if not word:
        raise HTTPException(400, "word required")

    with get_db() as db:
        row = db.execute(
            "SELECT review_count, correct_count FROM nodes WHERE id=?", (word,)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Word not found")

        rc = (row["review_count"] or 0) + 1
        cc = (row["correct_count"] or 0) + (1 if correct else 0)
        kl = _auto_knowledge_level(rc, cc)

        db.execute("""
            UPDATE nodes SET review_count=?, correct_count=?, knowledge_level=?,
                             last_reviewed=datetime('now')
            WHERE id=?
        """, (rc, cc, kl, word))

    trust = round(cc / rc, 2)
    return {"ok": True, "review_count": rc, "correct_count": cc, "trust": trust, "knowledge_level": kl}


# ── REVIEW MODE ───────────────────────────────────────────────

@app.get("/api/review/next")
def review_next():
    """Get the next word to review with 4-option multiple choice."""
    with get_db() as db:
        # auto-mark forgotten: learned/mastered not touched in 30+ days
        db.execute("""
            UPDATE nodes SET knowledge_level='forgotten'
            WHERE knowledge_level IN ('learned','mastered')
              AND last_reviewed IS NOT NULL
              AND last_reviewed < datetime('now','-30 days')
        """)

        row = db.execute("""
            SELECT id, cat, ai_cache, knowledge_level, last_reviewed, review_count, correct_count
            FROM nodes WHERE ai_cache != ''
            ORDER BY
                CASE knowledge_level
                    WHEN 'forgotten' THEN 0
                    WHEN 'new'       THEN 1
                    WHEN 'learned'   THEN 2
                    WHEN 'mastered'  THEN 3
                    ELSE 1 END,
                COALESCE(last_reviewed,'1970-01-01'),
                RANDOM()
            LIMIT 1
        """).fetchone()

        if not row:
            raise HTTPException(404, "No words to review")

        word       = row["id"]
        raw_def    = _parse_definition(row["ai_cache"])
        if len(raw_def) < 10:
            raise HTTPException(404, "No usable definition for review word")
        # clean correct definition: strip markdown, remove "Word:" prefix, blank the word
        definition = _clean_def_text(raw_def, strip_word=word, blank_word=word)

        decoy_rows = db.execute("""
            SELECT id, ai_cache FROM nodes WHERE id != ? AND ai_cache != ''
            ORDER BY RANDOM() LIMIT 10
        """, (word,)).fetchall()

    decoy_defs = []
    for d in decoy_rows:
        raw = _parse_definition(d["ai_cache"])
        # clean decoy: strip markdown and "Word:" prefix (don't blank — it's a different word)
        cleaned = _clean_def_text(raw, strip_word=d["id"])
        if len(cleaned) > 10 and len(decoy_defs) < 3:
            decoy_defs.append({"word": d["id"], "definition": cleaned})

    options = [{"word": word, "definition": definition, "correct": True}]
    for d in decoy_defs:
        options.append({"word": d["word"], "definition": d["definition"], "correct": False})
    random.shuffle(options)

    rc = row["review_count"] or 0
    cc = row["correct_count"] or 0

    days_since = None
    if row["last_reviewed"]:
        try:
            from datetime import timedelta
            lr = datetime.fromisoformat(row["last_reviewed"])
            days_since = max(0, (datetime.utcnow() - lr).days)
        except Exception:
            pass

    return {
        "word":              word,
        "cat":               row["cat"],
        "knowledge_level":   row["knowledge_level"] or "new",
        "days_since_review": days_since,
        "review_count":      rc,
        "trust":             round(cc / max(rc, 1), 2) if rc > 0 else 0,
        "options":           options,
    }


@app.post("/api/review/answer")
def review_answer(body: dict):
    return detective_result(body)


@app.patch("/api/nodes/{node_id}/knowledge")
def set_knowledge(node_id: str, body: dict):
    level = body.get("level", "new")
    if level not in ("new", "learned", "mastered", "forgotten"):
        raise HTTPException(400, "level must be: new, learned, mastered, or forgotten")
    with get_db() as db:
        db.execute("UPDATE nodes SET knowledge_level=? WHERE id=?", (level, node_id))
    return {"ok": True, "knowledge_level": level}


@app.get("/api/stats/trust")
def stats_trust():
    """Return per-word trust scores and knowledge-level distribution."""
    with get_db() as db:
        # auto-mark forgotten on stats load too
        db.execute("""
            UPDATE nodes SET knowledge_level='forgotten'
            WHERE knowledge_level IN ('learned','mastered')
              AND last_reviewed IS NOT NULL
              AND last_reviewed < datetime('now','-30 days')
        """)
        rows = db.execute("""
            SELECT id, cat, knowledge_level, review_count, correct_count, last_reviewed
            FROM nodes ORDER BY review_count DESC, id
        """).fetchall()

    totals   = {"new": 0, "learned": 0, "mastered": 0, "forgotten": 0}
    reviewed = []
    for r in rows:
        rc  = r["review_count"] or 0
        cc  = r["correct_count"] or 0
        kl  = r["knowledge_level"] or "new"
        tr  = round(cc / rc, 2) if rc > 0 else None
        totals[kl] = totals.get(kl, 0) + 1
        if rc > 0:
            reviewed.append(tr)

    avg_trust = round(sum(reviewed) / len(reviewed), 2) if reviewed else 0
    return {
        "totals":         totals,
        "avg_trust":      avg_trust,
        "total_reviewed": len(reviewed),
        "total_words":    len(rows),
    }


# ── KNOWLEDGE ROADMAP ─────────────────────────────────────────

@app.get("/api/roadmap/technologies")
def roadmap_technologies():
    with get_db() as db:
        rows = db.execute("SELECT * FROM rm_technologies ORDER BY name").fetchall()
    return {"technologies": [dict(r) for r in rows]}


@app.post("/api/roadmap/technologies")
def roadmap_add_technology(body: dict):
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(400, "name required")
    tech_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    desc = body.get("description", "")
    with get_db() as db:
        existing = db.execute("SELECT id FROM rm_technologies WHERE id=?", (tech_id,)).fetchone()
        if existing:
            raise HTTPException(409, f"Technology '{tech_id}' already exists")
        db.execute(
            "INSERT INTO rm_technologies (id, name, description) VALUES (?,?,?)",
            (tech_id, name, desc),
        )
    return {"ok": True, "id": tech_id, "name": name}


@app.delete("/api/roadmap/technologies/{tech_id}")
def roadmap_delete_technology(tech_id: str):
    with get_db() as db:
        db.execute(
            """DELETE FROM rm_links
               WHERE source IN (SELECT id FROM rm_nodes WHERE tech_id=?)
                  OR target IN (SELECT id FROM rm_nodes WHERE tech_id=?)""",
            (tech_id, tech_id),
        )
        db.execute("DELETE FROM rm_nodes WHERE tech_id=?", (tech_id,))
        db.execute("DELETE FROM rm_technologies WHERE id=?", (tech_id,))
    return {"ok": True}


@app.patch("/api/roadmap/technologies/{tech_id}/focus")
def roadmap_set_focus(tech_id: str, body: dict):
    focus = body.get("focus", "").strip()
    with get_db() as db:
        updated = db.execute(
            "UPDATE rm_technologies SET focus=? WHERE id=?", (focus, tech_id)
        ).rowcount
    if not updated:
        raise HTTPException(404, f"Technology '{tech_id}' not found")
    return {"ok": True, "focus": focus}


async def _stream_collect(prompt: str) -> str:
    """Collect all chunks from _llm_stream into a single string (avoids read-timeout)."""
    parts: list[str] = []
    async for chunk in _llm_stream(prompt):
        parts.append(chunk)
    return "".join(parts)


def _parse_roadmap_json(raw: str) -> tuple[list, list, list]:
    """Extract nodes, links, and sections from a raw LLM response that contains JSON."""
    start = raw.find("{")
    end   = raw.rfind("}") + 1
    if start == -1 or end == 0:
        return [], [], []
    try:
        data = json.loads(raw[start:end])
        return data.get("nodes", []), data.get("links", []), data.get("sections", [])
    except json.JSONDecodeError:
        return [], [], []


def _save_roadmap_batch(db, tech_id: str, nodes: list, links: list,
                        all_valid_ids: set, offset: int = 0,
                        sections: list = None, default_section: str = ''):
    """Insert nodes and links for one batch, skipping duplicates."""
    if sections is not None:
        db.execute("UPDATE rm_technologies SET sections=? WHERE id=?",
                   (json.dumps(sections), tech_id))
    for i, n in enumerate(nodes):
        nid = f"{tech_id}_{n['id']}"
        db.execute("""
            INSERT OR IGNORE INTO rm_nodes
                (id, tech_id, name, category, section, difficulty, priority)
            VALUES (?,?,?,?,?,?,?)
        """, (nid, tech_id, n.get("name", ""), n.get("category", "fundamentals"),
              n.get("section", default_section),
              max(1, min(5, int(n.get("difficulty", 1)))),
              offset + i))
    for l in links:
        rs, rt = l.get("source", ""), l.get("target", "")
        if rs in all_valid_ids and rt in all_valid_ids and rs != rt:
            src = f"{tech_id}_{rs}"
            tgt = f"{tech_id}_{rt}"
            db.execute(
                "INSERT OR IGNORE INTO rm_links (source, target, type) VALUES (?,?,?)",
                (src, tgt, l.get("type", "prerequisite"))
            )


_ROADMAP_JSON_SCHEMA = """{
  "sections": ["Section Name 1", "Section Name 2", "…"],
  "nodes": [
    {{"id": "snake_case_id", "name": "Human Readable Name",
      "section": "Section Name 1", "category": "fundamentals",
      "difficulty": 1}}
  ],
  "links": [
    {{"source": "node_id", "target": "node_id", "type": "prerequisite"}}
  ]
}}"""


@app.post("/api/roadmap/generate/{tech_id}")
async def roadmap_generate(tech_id: str, expand: bool = False):
    """
    Two-phase progressive roadmap generation.
    expand=false (default): clear existing nodes and generate the first 15-20 core concepts.
    expand=true            : keep existing nodes and add 10-15 more advanced concepts.
    """
    with get_db() as db:
        tech = db.execute("SELECT * FROM rm_technologies WHERE id=?", (tech_id,)).fetchone()
        existing_rows = db.execute(
            "SELECT id, name FROM rm_nodes WHERE tech_id=? ORDER BY priority", (tech_id,)
        ).fetchall()
    if not tech:
        raise HTTPException(404, f"Technology '{tech_id}' not found")

    tech_name     = tech["name"]
    tech_focus    = (tech["focus"] or "").strip()
    existing_list = [r["name"] for r in existing_rows]
    existing_ids  = {r["id"].replace(f"{tech_id}_", "") for r in existing_rows}

    focus_clause = (
        f"\nFOCUS AREA: {tech_focus}\n"
        "Generate concepts that are ONLY relevant to this focus area. "
        "Skip any concept that does not directly apply to it.\n"
    ) if tech_focus else ""

    if not expand:
        # ── PHASE 1: core concepts ──────────────────────────────
        prompt = f"""Generate a knowledge roadmap for learning {tech_name}.
{focus_clause}
Return ONLY a valid JSON object. No markdown. No code blocks. Just raw JSON.

{_ROADMAP_JSON_SCHEMA}

Rules:
- Include exactly 15-20 of the MOST FUNDAMENTAL concepts (beginner to intermediate)
- Define 4-7 sections ordered earliest-to-learn first (e.g. ["Ruby Basics", "OOP", "Web Framework", "Data"])
- section: assign every node to exactly one of your defined section names
- id: unique snake_case
- name: human-readable (1-4 words)
- category: one of: fundamentals, oop, web, data, testing, tooling, advanced
- difficulty: 1 (beginner) to 5 (expert)
- links: prerequisite relationships only (direct dependencies)
- All source/target IDs must match node IDs in this same response
- No duplicate IDs"""

        try:
            raw   = await _stream_collect(prompt)
            nodes, links, sections = _parse_roadmap_json(raw)
            if not nodes:
                raise HTTPException(500, "AI returned no usable nodes")

            all_ids = {n["id"] for n in nodes if "id" in n}
            with get_db() as db:
                db.execute("""
                    DELETE FROM rm_links WHERE source IN (
                        SELECT id FROM rm_nodes WHERE tech_id=?
                    ) OR target IN (SELECT id FROM rm_nodes WHERE tech_id=?)
                """, (tech_id, tech_id))
                db.execute("DELETE FROM rm_nodes WHERE tech_id=?", (tech_id,))
                _save_roadmap_batch(db, tech_id, nodes, links, all_ids, offset=0, sections=sections)

            return {"ok": True, "phase": 1, "nodes": len(nodes), "links": len(links)}
        except json.JSONDecodeError as e:
            raise HTTPException(500, f"JSON parse error: {e}")

    else:
        # ── PHASE 2: advanced / complementary concepts ──────────
        if not existing_list:
            raise HTTPException(400, "Run phase 1 first (expand=false)")

        existing_sections = json.loads(tech["sections"] or "[]") if tech else []
        sections_hint = (
            f"Use these existing sections (assign each new node to one): {existing_sections}"
            if existing_sections else
            "Define the same sections as before and assign each new node to one."
        )
        existing_block = "\n".join(f"- {name}" for name in existing_list)
        prompt = f"""I am learning {tech_name}. I already have these concepts in my roadmap:

{existing_block}

Generate 10-15 MORE concepts that complement and extend the above list.
Do NOT repeat or rephrase any concept already in the list.
Focus on intermediate and advanced topics not yet covered.
{focus_clause}
{sections_hint}
Return ONLY a valid JSON object. No markdown. No code blocks. Just raw JSON.

{_ROADMAP_JSON_SCHEMA}

Rules:
- 10-15 NEW concepts only (not in the list above)
- id: unique snake_case (must not match any existing id: {', '.join(sorted(existing_ids)[:10])}…)
- name: human-readable (1-4 words)
- section: assign to one of the existing sections listed above
- category: one of: fundamentals, oop, web, data, testing, tooling, advanced
- difficulty: 1-5
- links: prerequisite relationships between the NEW nodes only
- All source/target IDs must match IDs in THIS response only"""

        try:
            raw   = await _stream_collect(prompt)
            nodes, links, _ = _parse_roadmap_json(raw)  # sections already saved in phase 1
            if not nodes:
                raise HTTPException(500, "AI returned no usable nodes")

            new_ids = {n["id"] for n in nodes if "id" in n}
            # guard: skip any node whose id collides with existing ones
            nodes = [n for n in nodes if n.get("id","") not in existing_ids]
            new_ids = {n["id"] for n in nodes}

            with get_db() as db:
                _save_roadmap_batch(db, tech_id, nodes, links, new_ids,
                                    offset=len(existing_list))

            return {"ok": True, "phase": 2, "nodes": len(nodes),
                    "links": len(links), "total": len(existing_list) + len(nodes)}
        except json.JSONDecodeError as e:
            raise HTTPException(500, f"JSON parse error: {e}")


@app.get("/api/roadmap/{tech_id}/graph")
def roadmap_graph(tech_id: str):
    with get_db() as db:
        tech = db.execute("SELECT * FROM rm_technologies WHERE id=?", (tech_id,)).fetchone()
        nodes = db.execute(
            "SELECT * FROM rm_nodes WHERE tech_id=? ORDER BY priority", (tech_id,)
        ).fetchall()
        if not nodes:
            return {"nodes": [], "links": [], "sections": []}
        node_ids = {r["id"] for r in nodes}
        links = db.execute("""
            SELECT * FROM rm_links
            WHERE source IN (SELECT id FROM rm_nodes WHERE tech_id=?)
        """, (tech_id,)).fetchall()
    sections = json.loads(tech["sections"] or "[]") if tech else []
    return {
        "nodes": [dict(r) for r in nodes],
        "links": [dict(l) for l in links if l["target"] in node_ids],
        "sections": sections,
    }


@app.post("/api/roadmap/nodes/{node_id}/expand")
async def roadmap_expand_node(node_id: str):
    """Generate 4-7 deeper sub-topics for a node and wire them into the graph."""
    with get_db() as db:
        node = db.execute("SELECT * FROM rm_nodes WHERE id=?", (node_id,)).fetchone()
        if not node:
            raise HTTPException(404, "Node not found")
        tech_id = node["tech_id"]
        tech = db.execute("SELECT * FROM rm_technologies WHERE id=?", (tech_id,)).fetchone()
        existing_rows = db.execute(
            "SELECT id, name FROM rm_nodes WHERE tech_id=? ORDER BY priority", (tech_id,)
        ).fetchall()
        max_priority = db.execute(
            "SELECT COALESCE(MAX(priority),0) FROM rm_nodes WHERE tech_id=?", (tech_id,)
        ).fetchone()[0]

    tech_name     = tech["name"] if tech else tech_id
    concept_name  = node["name"]
    node_short_id = node_id.replace(f"{tech_id}_", "")

    existing_short_ids = {r["id"].replace(f"{tech_id}_", "") for r in existing_rows}
    existing_block = "\n".join(
        f"- {r['name']} (id: {r['id'].replace(tech_id + '_', '')})"
        for r in existing_rows
    )

    prompt = f"""You are expanding the concept "{concept_name}" inside a {tech_name} learning roadmap.

Parent concept: "{concept_name}" (id: {node_short_id})

Concepts already in the roadmap:
{existing_block}

Generate 4-7 deeper sub-topics that go specifically inside "{concept_name}".
Each sub-topic must be a concrete technique, pattern, or sub-feature of "{concept_name}" — not a general topic.

Return ONLY valid JSON, no markdown, no code blocks:
{{
  "nodes": [
    {{"id": "unique_snake_case", "name": "Short Human Name", "category": "fundamentals|oop|web|data|testing|tooling|advanced", "difficulty": 1}}
  ],
  "links": [
    {{"source": "source_short_id", "target": "target_short_id", "type": "prerequisite"}}
  ]
}}

Rules:
- Every new node MUST appear as a link target from "{node_short_id}": {{"source":"{node_short_id}","target":"<new_id>","type":"prerequisite"}}
- Also add links between new nodes when one depends on another
- Also add links from new nodes TO existing concepts when relevant (use their IDs above)
- DO NOT reuse these IDs (already exist): {', '.join(sorted(existing_short_ids))}
- difficulty 1-5
- No duplicate IDs in this response"""

    raw = await _stream_collect(prompt)
    nodes, links, _ = _parse_roadmap_json(raw)
    if not nodes:
        raise HTTPException(500, "AI returned no usable nodes")

    new_short_ids = {n["id"] for n in nodes if "id" in n}
    all_valid_ids = new_short_ids | existing_short_ids   # links to existing nodes are valid

    with get_db() as db:
        # Expanded children belong in their own section named after the parent node.
        # Also ensure this section is added to the technology's ordered section list.
        existing_sections = json.loads(tech["sections"] or "[]")
        if concept_name not in existing_sections:
            # Insert right after the parent's section so columns stay in learning order
            parent_sec = node["section"] or ""
            try:
                insert_at = existing_sections.index(parent_sec) + 1
            except ValueError:
                insert_at = len(existing_sections)
            existing_sections.insert(insert_at, concept_name)

        _save_roadmap_batch(db, tech_id, nodes, links, all_valid_ids,
                            offset=max_priority + 1,
                            sections=existing_sections,
                            default_section=concept_name)

        # Fix section for any already-existing direct children that inherited the
        # wrong section (e.g. from a previous expand before this bug was fixed).
        db.execute(
            """UPDATE rm_nodes SET section=?
               WHERE id IN (
                   SELECT target FROM rm_links WHERE source=?
               ) AND tech_id=? AND section != ?""",
            (concept_name, node_id, tech_id, concept_name)
        )

        # Guarantee parent → every new child link exists even if LLM missed it
        for n in nodes:
            db.execute(
                "INSERT OR IGNORE INTO rm_links (source,target,type) VALUES (?,?,?)",
                (node_id, f"{tech_id}_{n['id']}", "prerequisite"),
            )

    return {"ok": True, "new_nodes": len(nodes), "new_links": len(links)}


@app.post("/api/roadmap/{tech_id}/fix-sections")
def roadmap_fix_sections(tech_id: str):
    """
    Retroactively fix section assignments: any node whose direct children share
    its own section gets those children moved into a section named after the node.
    Fixes data created before the expand-section bug was patched.
    """
    with get_db() as db:
        tech = db.execute("SELECT * FROM rm_technologies WHERE id=?", (tech_id,)).fetchone()
        if not tech:
            raise HTTPException(404, "Technology not found")

        nodes = db.execute("SELECT * FROM rm_nodes WHERE tech_id=?", (tech_id,)).fetchall()
        links = db.execute(
            "SELECT source, target FROM rm_links WHERE source LIKE ?", (f"{tech_id}_%",)
        ).fetchall()

        node_map = {n["id"]: n for n in nodes}
        children_of = {}
        for l in links:
            children_of.setdefault(l["source"], []).append(l["target"])

        existing_sections = json.loads(tech["sections"] or "[]")
        updated = 0

        for parent_id, child_ids in children_of.items():
            parent = node_map.get(parent_id)
            if not parent:
                continue
            parent_sec = parent["section"] or ""
            parent_name = parent["name"]
            if parent_name == parent_sec:
                continue  # already its own section

            # Children that share the parent's section (wrong placement)
            wrong = [cid for cid in child_ids
                     if node_map.get(cid) and node_map[cid]["section"] == parent_sec]
            if not wrong:
                continue

            # Add parent name as a new section right after its current section
            if parent_name not in existing_sections:
                try:
                    idx = existing_sections.index(parent_sec) + 1
                except ValueError:
                    idx = len(existing_sections)
                existing_sections.insert(idx, parent_name)

            for cid in wrong:
                db.execute("UPDATE rm_nodes SET section=? WHERE id=?", (parent_name, cid))
                updated += 1

        db.execute("UPDATE rm_technologies SET sections=? WHERE id=?",
                   (json.dumps(existing_sections), tech_id))

    return {"ok": True, "nodes_updated": updated, "sections": existing_sections}


@app.patch("/api/roadmap/nodes/{node_id}/status")
def roadmap_set_status(node_id: str, body: dict):
    status = body.get("status", "not_started")
    if status not in {"not_started", "studying", "understood", "practiced", "mastered"}:
        raise HTTPException(400, "Invalid status")
    with get_db() as db:
        db.execute("UPDATE rm_nodes SET status=? WHERE id=?", (status, node_id))
    return {"ok": True, "status": status}


@app.patch("/api/roadmap/nodes/{node_id}/notes")
def roadmap_save_notes(node_id: str, body: dict):
    notes = body.get("notes", "")
    with get_db() as db:
        db.execute("UPDATE rm_nodes SET personal_notes=? WHERE id=?", (notes, node_id))
    return {"ok": True}


@app.get("/api/roadmap/nodes/{node_id}/card")
async def roadmap_node_card(node_id: str, force: bool = False):
    """Stream an AI-generated learning card for a roadmap concept."""
    with get_db() as db:
        node = db.execute("SELECT * FROM rm_nodes WHERE id=?", (node_id,)).fetchone()
        tech = None
        if node:
            tech = db.execute(
                "SELECT * FROM rm_technologies WHERE id=?", (node["tech_id"],)
            ).fetchone()

    if not node:
        raise HTTPException(404, "Node not found")

    if node["ai_cache"] and not force:
        cached = node["ai_cache"]
        async def send_cached():
            yield cached
        return StreamingResponse(send_cached(), media_type="text/plain")

    tech_name  = tech["name"] if tech else node["tech_id"]
    concept    = node["name"]
    difficulty = node["difficulty"] or 1

    prompt = f"""You are teaching {tech_name} to an experienced systems engineer learning programming.
Explain the concept: "{concept}" (difficulty {difficulty}/5)

## WHAT IT IS
A clear, concise definition in 2-3 sentences.

## WHEN TO USE IT
A concrete situation where you would apply this concept.

## CODE EXAMPLE
A short, realistic code snippet with a brief comment.

## COMMON MISTAKES
Three bullet points of mistakes beginners make.

## ANALOGY
One memorable analogy that makes this concept stick.

Keep it brief, practical, and direct."""

    full_text = []

    async def generate():
        try:
            async for chunk in _llm_stream(prompt):
                full_text.append(chunk)
                yield chunk
        except Exception as e:
            yield f"\n\n[Error: {e}]"
        finally:
            if full_text:
                try:
                    with get_db() as db:
                        db.execute(
                            "UPDATE rm_nodes SET ai_cache=? WHERE id=?",
                            ("".join(full_text), node_id)
                        )
                except Exception:
                    pass

    return StreamingResponse(generate(), media_type="text/plain")


# ── SERVE FRONTEND ────────────────────────────────────────────
frontend_path = Path(__file__).parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
