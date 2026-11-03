<div align="center">

# Three-Tier KB

**Local knowledge base with a three-tier index · Shared across AI clients · 12-point quality gate**

[中文](README.md) · [Install](#install) · [Quick Start](#quick-start) · [Docs](#docs)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-green.svg)](https://www.python.org/)
[![No Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](https://pypi.org/)
[![Obsidian](https://img.shields.io/badge/Obsidian-compatible-7C3AED.svg)](https://obsidian.md/)

</div>

---

## What is this

An **executable local knowledge base setup**, not a methodology write-up.

It turns files scattered across multiple AI clients and workspaces into a knowledge base that
**every client can query in under a second, always returns the same answer, and validates every change**.

Ships with 3 runnable Python scripts (zero third-party dependencies), 5 field-tested documents, and 2 templates.

```bash
git clone https://github.com/zhoudibo/three-tier-kb.git
cd three-tier-kb/skills/three-tier-kb/scripts
python3 kb_index.py --vault ~/Documents/my-vault --init
python3 kb_index.py --vault ~/Documents/my-vault --build
python3 kb_gate.py ~/Documents/my-vault
```

---

## The problem

A flat "scan everything + full-text match" approach breaks in three ways once you pass a thousand files:

| Symptom | Real cause | How three tiers fix it |
|---|---|---|
| Takes forever to find anything | O(n) full-library scan | **L0 meta-index** precomputed, `<1s` |
| 200 hits, no idea which to read | No priority | **L0 wins**, layered dedup by tier |
| Client A and Client B disagree | Each searches its own way | **Unified query API**, consistent results |

---

## The three tiers

| Tier | Name | Latency | Holds | Maintained by |
|---|---|---|---|---|
| **L0** | Meta index | `<1s` | `vault_index.json` / `topic_index.json` / `graph_index.json` | Scripts (auto-rebuild) |
| **L1** | Project index | `<5s` | `<project>/index.md` | Humans / targeted AI (semantic) |
| **L2** | Raw content | `<30s` | every Markdown file in the vault | **Single source of truth** |

**Hard rule: when one keyword hits multiple tiers, L0 always wins.**

```
        ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
        │  AI client A  │  │  AI client B  │  │  AI client C  │
        └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
               └────────┬────────┴────────┬────────┘
                        │  kb_sync.py (hash-baseline incremental, 5-min cron)
                        ▼
              ┌─────────────────────────────────────┐
              │      L2 · Raw content (vault)         │ ← source of truth
              └───────────────┬─────────────────────┘
                              │ kb_index.py --build (daily baseline)
                              ▼
              ┌─────────────────────────────────────┐
              │      L0 · Meta index (JSON cache)     │ ← machine layer
              └───────────────┬─────────────────────┘
                              │ query routing (L0 first)
                              ▼
              ┌─────────────────────────────────────┐
              │      L1 · Project index (Markdown)    │ ← human layer
              └─────────────────────────────────────┘
```

---

## The three scripts

### `kb_index.py` — index engine

Builds the L0 meta-index and routes three-tier queries.

```bash
python3 kb_index.py --vault ~/Documents/my-vault --init     # write a config template
python3 kb_index.py --vault ~/Documents/my-vault --build    # build L0
python3 kb_index.py --vault ~/Documents/my-vault --query "keyword"
```

### `kb_gate.py` — quality gate

12 checks, **read-only**, mutates nothing.

```bash
python3 kb_gate.py ~/Documents/my-vault
```

```
[2/12] Orphan files (no inbound and no outbound links)...
  ⚠️  WARN  2 orphans: empty.md, agent-b/project-beta/orphan.md
[7/12] Sensitive files and credential scan...
  ❌ FAIL  sensitive file credentials.json / suspected credential content

Result: 4 PASS / 7 WARN / 1 FAIL (12 total)
❌ Blocked: FAIL present — do not ship this batch, roll back and re-run
```

Exit code `1` means FAIL — usable directly as a CI / cron gate.

### `kb_sync.py` — incremental sync

Incremental sync from multiple clients into the vault, driven by a hash baseline.

```bash
python3 kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace --route --dry-run
python3 kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace --route
```

Three-state detection — **added / changed / unchanged** — based on content hash, never mtime.

---

## Why it's worth doing

The hard part of a knowledge base isn't building it. It's **keeping it alive**.

In practice, a library decays in this order:
content stops arriving → index drifts → graph falls apart → nobody trusts it → nobody maintains it.

Each layer counters one link in that chain:

- Three-tier index → queries are **fast and consistent**, so agents actually use it
- Hash-baseline sync → new content **gets in**, so the library isn't empty
- 12-point gate → decay **becomes visible**, so it can be fixed early
- Cluster cross-links → the graph **stays connected**, so humans keep browsing it

Any single layer isn't enough.

---

## Docs

| Document | Covers |
|---|---|
| [`references/three-tier-architecture.md`](skills/three-tier-kb/references/three-tier-architecture.md) | Tier boundaries, query routing, session FTS5, architecture decision records |
| [`references/sync-protocol.md`](skills/three-tier-kb/references/sync-protocol.md) | Hash baseline, triple filtering, space-safe traversal, atomic writes, troubleshooting |
| [`references/quality-gate-guide.md`](skills/three-tier-kb/references/quality-gate-guide.md) | All 12 checks, fix priority, frontmatter spec, safe batch editing |
| [`references/index-configuration.md`](skills/three-tier-kb/references/index-configuration.md) | `index_dirs` / `topic_map` / `project_keywords` reference |
| [`references/graph-view-repair.md`](skills/three-tier-kb/references/graph-view-repair.md) | Force-directed layout, cluster strategies, orphan-zeroing protocol |

---

## Install

### As a Hermes Skill

```bash
git clone https://github.com/zhoudibo/three-tier-kb.git \
  ~/.hermes/skills/note-taking/three-tier-kb

hermes skills list | grep three-tier-kb
```

### Standalone

Zero third-party dependencies, Python 3.9+:

```bash
git clone https://github.com/zhoudibo/three-tier-kb.git
cd three-tier-kb/skills/three-tier-kb/scripts
python3 kb_gate.py --help
```

### Optional cron

```bash
# 5-minute incremental sync
*/5 * * * * python3 ~/path/to/kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace --route

# daily baseline rebuild + quality gate
30 17 * * * python3 ~/path/to/kb_index.py --vault ~/Documents/my-vault --build \
  && python3 ~/path/to/kb_gate.py ~/Documents/my-vault
```

---

## Layout

```
three-tier-kb/
├── README.md
├── README_EN.md
├── LICENSE
├── docs/
│   └── real-world-lessons.md
├── skills/three-tier-kb/
│   ├── SKILL.md
│   ├── references/
│   ├── templates/
│   └── scripts/
└── assets/
    └── banner.svg
```

---

## Red lines

- Always filter `.git/` `.obsidian/` `node_modules/` `__pycache__/` `.venv/` — filter drift creates ghost entries
- Traverse space-containing filenames with `while IFS= read -r`, never `for f in $(find ...)`
- **Never bulk-rewrite Markdown bodies with regex** — use DOM parsing
- `.env` / `*credentials*` / tokens never enter the vault; logs record paths, never contents
- Don't create a standalone index per resource type — embed wikilinks in the existing project index
- Deleting existing files or pushing content outside the vault requires explicit user authorization

---

## FAQ

**Why rebuild the baseline from scratch instead of updating incrementally?**
One missed delete permanently drifts the index (ghost entries). A from-scratch rebuild is slower but **always self-heals**.

**Why a 5-minute cron instead of a filesystem watcher?**
Watchers thrash on concurrent writes from multiple clients and are painful to debug. A 5-minute window is small enough, retryable, and leaves an auditable log.

**The gate FAILs on its very first run — is it broken?**
No, that's by design. A real vault always surfaces issues on first inspection. Expect 3-6 iterations to reach zero FAILs.

**The script says "0 orphans" but the Obsidian graph still looks scattered?**
Script and GUI rendering differ slightly — trust the GUI. Check that every file has ≥3 outbound links (the force-directed threshold).

---

## License

MIT © [zhoudibo](https://github.com/zhoudibo)

---

<div align="center">
<sub>If this helped you, consider a ⭐ — it helps others find it</sub>
</div>
