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
import sqlite3, json, httpx, asyncio, re, os
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
        """)
        # migrate: add columns added after initial release
        cols = [r[1] for r in db.execute("PRAGMA table_info(nodes)").fetchall()]
        if "image_url" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN image_url TEXT DEFAULT ''")
        if "weight" not in cols:
            db.execute("ALTER TABLE nodes ADD COLUMN weight INTEGER DEFAULT 1")

        # migrate: jargon → concept, temporal → phrase (both categories removed)
        db.execute("UPDATE nodes SET cat='concept' WHERE cat='jargon'")
        db.execute("UPDATE nodes SET cat='phrase'  WHERE cat='temporal'")

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
            # validate
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
            # anything else (CORRECT or unexpected) → no change
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

# ── SERVE FRONTEND ────────────────────────────────────────────
frontend_path = Path(__file__).parent / "frontend"
if frontend_path.exists():
    app.mount("/", StaticFiles(directory=str(frontend_path), html=True), name="frontend")
