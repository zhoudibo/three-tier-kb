#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_index.py — 三层架构知识库 · 索引引擎（可移植版）

把 Obsidian vault 变成「元索引 / 项目索引 / 原始内容」三层可查询结构，
并提供多智能体共用的统一查询接口。

三层设计（来自真实落地验证）:
  Level 0 · 元索引   <1s    vault_index.json / topic_index.json / graph_index.json
  Level 1 · 项目索引 <5s    <project>/索引.md
  Level 2 · 原始内容 <30s   vault 内全部 MD 全文

为什么是三层而不是一层:
  - 元索引把「一次全库扫描」压缩成「一次 JSON 读取」，是查询延迟的解药
  - 项目索引是人写的语义层，AI 扫不出来，只有人能保证「这个项目该放哪」
  - 原始内容是唯一真相源，元索引和项目索引都可以从它重建

用法:
    python3 kb_index.py --vault ~/Documents/my-vault --init
    python3 kb_index.py --vault ~/Documents/my-vault --build
    python3 kb_index.py --vault ~/Documents/my-vault --query "关键词"
    python3 kb_index.py --vault ~/Documents/my-vault --query "关键词" --json

配置文件（可选）: <vault>/.kb_index.json
    {
      "index_dirs": ["智能体/*/项目/*"],   // 项目索引目录（glob）
      "topic_map": {"关键词": "项目名"},    // 主题 → 项目 映射
      "exclude_dirs": [".git", "node_modules"]
    }

退出码: 0 成功 / 1 查询无命中 / 2 参数错误
"""

import os
import sys
import re
import json
import glob
import argparse
import hashlib
from datetime import datetime
from pathlib import Path

# ==================== 默认配置 ====================

DEFAULT_EXCLUDE_DIRS = {
    '.git', '.obsidian', '.vscode', '.idea', 'node_modules', '__pycache__',
    '.venv', 'venv', 'site-packages', '.cache', '.pytest_cache', '.Trash',
    'dist', 'build', '.next', 'coverage', '.nyc_output', 'target',
}

DEFAULT_INDEX_DIRS = ["智能体/*/项目/*"]

WIKILINK_RE = re.compile(r'\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]')
HASH_RE = re.compile(r'^#+\s+(.+)$', re.M)


# ==================== 工具函数 ====================

def detect_index_dirs(vault: Path) -> list:
    """自动探测 vault 里的项目索引目录结构。

    策略：找出所有含「索引.md / index.md」的目录，用「树压缩」反推能匹配它们的 glob。
    规则：某一层目录下只有一个子目录 → 保留原名；有两个以上 → 折叠成 *。

    例：
      vault/项目/Alpha/索引.md                → "项目/*"
      vault/智能体/A/项目/X/索引.md           → "智能体/*/项目/*"
      vault/智能体/B/项目/Y/索引.md           → "智能体/*/项目/*"
      vault/项目/Alpha/索引.md + 上面两个     → ["项目/*", "智能体/*/项目/*"]

    比要求用户手写 glob 更可靠 —— 用户往往不知道自己的结构该怎么表达。
    """
    hits = []
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS]
        if any(fn in ("索引.md", "index.md") for fn in filenames):
            hits.append(os.path.relpath(dirpath, vault))
    if not hits:
        return list(DEFAULT_INDEX_DIRS)

    # 去掉叶子（项目目录名本身用 * 通配），只保留父路径
    parents = []
    for h in hits:
        segs = [s for s in str(h).replace(os.sep, "/").split("/") if s not in (".", "")]
        if len(segs) >= 1:
            parents.append(tuple(segs[:-1]))
    if not parents:
        return list(DEFAULT_INDEX_DIRS)

    # 建树
    tree = {}
    for p in parents:
        node = tree
        for seg in p:
            node = node.setdefault(seg, {})

    def compress(node):
        """把子树压缩成段列表：单子节点保留原名，多子节点折叠为 *"""
        if not node:
            return []
        if len(node) == 1:
            k, v = next(iter(node.items()))
            return [k] + compress(v)
        # 多个子节点 → 通配，并合并它们的子树
        merged = {}
        for v in node.values():
            for kk, vv in v.items():
                merged.setdefault(kk, {}).update(vv)
        return ["*"] + compress(merged)

    patterns = []
    for k, v in tree.items():
        segs = [k] + compress(v)
        patterns.append("/".join(segs) + "/*")
    # 去重保序
    seen, uniq = set(), []
    for p in patterns:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def load_config(vault: Path) -> dict:
    """读取 <vault>/.kb_index.json，缺失则用默认值"""
    cfg = {
        "index_dirs": DEFAULT_INDEX_DIRS,
        "topic_map": {},
        "exclude_dirs": sorted(DEFAULT_EXCLUDE_DIRS),
    }
    cfg_file = vault / ".kb_index.json"
    if cfg_file.exists():
        try:
            user = json.loads(cfg_file.read_text(encoding="utf-8"))
            for k, v in user.items():
                if k == "exclude_dirs" and isinstance(v, list):
                    cfg[k] = sorted(set(DEFAULT_EXCLUDE_DIRS) | set(v))
                else:
                    cfg[k] = v
        except Exception as e:
            print(f"⚠️  配置解析失败，使用默认值: {e}", file=sys.stderr)
    return cfg


def walk_files(root: Path, exclude: set):
    """统一边界过滤 —— 所有扫描都必须走这个函数，过滤漂移会产生幽灵条目"""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for fn in filenames:
            if fn.startswith('.') and fn not in ('.md',):
                continue
            yield Path(dirpath) / fn


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()[:12]


def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def rel_of(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# ==================== Level 0 · 元索引构建 ====================

class MetaIndexBuilder:
    """构建 Level 0 元索引：一次扫描，多层复用"""

    def __init__(self, vault: Path, cfg: dict):
        self.vault = vault
        self.cfg = cfg
        self.exclude = set(cfg.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS))
        self.index_dir = vault / ".kb_meta"
        self.index_dir.mkdir(parents=True, exist_ok=True)

    # ---------- vault_index.json ----------
    def build_vault_index(self) -> dict:
        print("  📋 构建 vault_index.json ...")
        projects = {}
        # 项目索引：按配置的 glob 模式发现
        for pattern in self.cfg.get("index_dirs", DEFAULT_INDEX_DIRS):
            for d in glob.glob(str(self.vault / pattern), recursive=True):
                idx = Path(d) / "索引.md"
                if not idx.exists():
                    idx = Path(d) / "index.md"
                if not idx.exists():
                    continue
                name = Path(d).name
                projects[name] = {
                    "index_path": rel_of(idx, self.vault),
                    "last_modified": datetime.fromtimestamp(
                        idx.stat().st_mtime).isoformat(),
                    "size_kb": round(idx.stat().st_size / 1024, 1),
                }

        total = md = non_md = 0
        for f in walk_files(self.vault, self.exclude):
            total += 1
            if f.suffix.lower() == ".md":
                md += 1
            else:
                non_md += 1

        idx = {
            "generated_at": datetime.now().isoformat(),
            "last_daily_sync": datetime.now().isoformat(),
            "projects": projects,
            "total_files": total,
            "total_md_files": md,
            "total_non_md_files": non_md,
        }
        self._write("vault_index.json", idx)
        print(f"    ✅ {len(projects)} 个项目索引 / {total} 个文件")
        return idx

    # ---------- topic_index.json ----------
    def build_topic_index(self) -> dict:
        print("  📋 构建 topic_index.json ...")
        topics = {}
        total_mappings = 0
        topic_map = self.cfg.get("topic_map", {})
        if not topic_map:
            print("    ⏭️  未配置 topic_map，跳过（可在 .kb_index.json 中添加）")

        for f in walk_files(self.vault, self.exclude):
            if f.suffix.lower() != ".md":
                continue
            rel = rel_of(f, self.vault)
            content = safe_read(f)[:5000].lower()
            for key, project in topic_map.items():
                if key.lower() in content or key.lower() in rel.lower():
                    bucket = topics.setdefault(project, [])
                    if rel not in [b["file"] for b in bucket]:
                        bucket.append({"file": rel, "matched_keyword": key})
                        total_mappings += 1

        idx = {
            "generated_at": datetime.now().isoformat(),
            "topics": topics,
            "total_mappings": total_mappings,
        }
        self._write("topic_index.json", idx)
        print(f"    ✅ {len(topics)} 个主题 / {total_mappings} 条映射")
        return idx

    # ---------- graph_index.json ----------
    def build_graph_index(self) -> dict:
        print("  📋 构建 graph_index.json ...")
        md_files = 0
        wikilinks = 0
        stems = set()
        outlinks = {}
        inlinks = {}

        for f in walk_files(self.vault, self.exclude):
            if f.suffix.lower() != ".md":
                continue
            md_files += 1
            stems.add(f.stem)
            outlinks[f.stem] = set()

        for f in walk_files(self.vault, self.exclude):
            if f.suffix.lower() != ".md":
                continue
            content = safe_read(f)
            for raw in WIKILINK_RE.findall(content):
                target = Path(raw.strip()).stem
                if not target:
                    continue
                wikilinks += 1
                outlinks.setdefault(f.stem, set()).add(target)
                inlinks[target] = inlinks.get(target, 0) + 1

        isolated = [s for s in stems
                    if not outlinks.get(s) and inlinks.get(s, 0) == 0]

        idx = {
            "generated_at": datetime.now().isoformat(),
            "nodes": {"md_files": md_files, "isolated_md_files": len(isolated)},
            "edges": {"wikilinks": wikilinks},
            "isolated_samples": isolated[:30],
        }
        self._write("graph_index.json", idx)
        print(f"    ✅ {md_files} 个 MD 节点 / {wikilinks} 条 wikilink / "
              f"{len(isolated)} 个孤立")
        return idx

    def _write(self, name, data):
        path = self.index_dir / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        return path


# ==================== Level 1/2 · 查询 ====================

class IndexQueryAPI:
    """
    统一查询接口 —— 所有智能体必须走这一个入口。

    为什么必须统一:
      4 个 agent 用 4 套启发式查同一个库，会得到 4 份不同答案。
      同一条代码路径 = 同一个结果，这是「多智能体一致性」的唯一保证。
    """

    def __init__(self, vault: Path, cfg: dict = None):
        self.vault = Path(vault).expanduser().resolve()
        self.cfg = cfg or load_config(self.vault)
        self.meta_dir = self.vault / ".kb_meta"

    # ---------- 加载 ----------
    def _load_meta(self, name):
        p = self.meta_dir / name
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def query(self, keyword: str, agent: str = "Hermes") -> dict:
        """
        按三层优先级查询。

        硬规则：同一关键词命中多层时，Level 0 永远优先。
        """
        kw = (keyword or "").strip().lower()
        result = {
            "keyword": keyword,
            "agent": agent,
            "queried_at": datetime.now().isoformat(),
            "level_0_meta": self._query_level_0(kw),
            "level_1_project": self._query_level_1(kw),
            "level_2_raw": self._query_level_2(kw),
        }
        # 去重合并，Level 0 在前
        seen = set()
        merged = []
        for item in (result["level_0_meta"] + result["level_1_project"]
                     + result["level_2_raw"]):
            key = item.get("path")
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
        result["final_recommendations"] = merged[:20]
        result["hit_count"] = len(merged)
        return result

    # ---------- Level 0 ----------
    def _query_level_0(self, kw):
        hits = []
        vidx = self._load_meta("vault_index.json")
        for name, info in vidx.get("projects", {}).items():
            if kw in name.lower():
                hits.append({
                    "level": 0, "type": "project", "name": name,
                    "path": info.get("index_path", ""),
                    "last_modified": info.get("last_modified", ""),
                })
        tidx = self._load_meta("topic_index.json")
        for project, items in tidx.get("topics", {}).items():
            if kw in project.lower():
                hits.append({
                    "level": 0, "type": "topic", "name": project,
                    "path": f"{len(items)} 个相关文件",
                    "files": [i["file"] for i in items[:10]],
                })
        return hits

    # ---------- Level 1 ----------
    def _query_level_1(self, kw):
        hits = []
        for pattern in self.cfg.get("index_dirs", DEFAULT_INDEX_DIRS):
            for d in glob.glob(str(self.vault / pattern), recursive=True):
                idx = Path(d) / "索引.md"
                if not idx.exists():
                    idx = Path(d) / "index.md"
                if not idx.exists():
                    continue
                content = safe_read(idx)
                if kw in content.lower() or kw in Path(d).name.lower():
                    hits.append({
                        "level": 1, "type": "project_index",
                        "name": Path(d).name,
                        "path": rel_of(idx, self.vault),
                        "snippet": self._snippet(content, kw),
                    })
        return hits

    # ---------- Level 2 ----------
    def _query_level_2(self, kw):
        hits = []
        exclude = set(self.cfg.get("exclude_dirs", DEFAULT_EXCLUDE_DIRS))
        for f in walk_files(self.vault, exclude):
            if f.suffix.lower() != ".md":
                continue
            content = safe_read(f)
            if kw in content.lower():
                hits.append({
                    "level": 2, "type": "raw",
                    "name": f.name,
                    "path": rel_of(f, self.vault),
                    "snippet": self._snippet(content, kw),
                })
            if len(hits) >= 200:
                break
        return hits

    @staticmethod
    def _snippet(content, kw, width=120):
        low = content.lower()
        pos = low.find(kw)
        if pos < 0:
            return ""
        start = max(0, pos - width // 3)
        text = content[start:start + width].replace("\n", " ").strip()
        return ("…" if start > 0 else "") + text + ("…" if start + width < len(content) else "")


# ==================== 每日基线 ====================

class DailySyncController:
    """
    每日基线重建。

    为什么要「从零重建」而不是「增量更新」:
      增量更新一旦漏掉一次删除，索引就会永久漂移（幽灵条目）。
      从零重建慢一点，但永远自愈。7 天实践里这是最省心的选择。
    """

    def __init__(self, vault: Path, cfg: dict):
        self.vault = vault
        self.cfg = cfg

    def run(self) -> dict:
        print(f"🔄 每日基线重建 @ {datetime.now():%Y-%m-%d %H:%M:%S}")
        b = MetaIndexBuilder(self.vault, self.cfg)
        v = b.build_vault_index()
        b.build_topic_index()
        g = b.build_graph_index()
        print(f"✅ 基线完成：{v['total_files']} 文件 / "
              f"{g['nodes']['md_files']} MD / "
              f"{g['nodes']['isolated_md_files']} 孤立节点")
        return {"vault_index": v, "graph_index": g}


# ==================== CLI ====================

def main():
    ap = argparse.ArgumentParser(
        description="三层架构知识库索引引擎",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--vault", required=True, help="vault 根目录")
    ap.add_argument("--init", action="store_true",
                    help="初始化 .kb_index.json 配置模板")
    ap.add_argument("--build", action="store_true",
                    help="构建 Level 0 元索引（每日基线）")
    ap.add_argument("--query", metavar="KEYWORD", help="三层查询")
    ap.add_argument("--agent", default="Hermes", help="调用方标识")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        print(f"❌ vault 目录不存在: {vault}", file=sys.stderr)
        return 2

    cfg = load_config(vault)

    if args.init:
        target = vault / ".kb_index.json"
        if target.exists():
            print(f"ℹ️  配置已存在: {target}")
            return 0
        detected = detect_index_dirs(vault)
        template = {
            "index_dirs": detected,
            "topic_map": {
                "示例关键词": "示例项目名",
            },
            "exclude_dirs": sorted(DEFAULT_EXCLUDE_DIRS),
        }
        target.write_text(json.dumps(template, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"✅ 已生成配置模板: {target}")
        if detected and detected != DEFAULT_INDEX_DIRS:
            print(f"   ℹ️  已自动探测到 {len(detected)} 组项目索引目录: {detected}")
        else:
            print("   ℹ️  未自动探测到项目索引目录，请手动编辑 index_dirs")
        print("   请编辑 index_dirs 和 topic_map 后运行 --build")
        return 0

    if args.build:
        DailySyncController(vault, cfg).run()
        return 0

    if args.query:
        api = IndexQueryAPI(vault, cfg)
        result = api.query(args.query, agent=args.agent)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"\n🔍 「{args.query}」 命中 {result['hit_count']} 条 "
                  f"(agent={args.agent})\n")
            for i, item in enumerate(result["final_recommendations"], 1):
                print(f"{i:2}. [L{item['level']}] {item['type']} · {item['name']}")
                if item.get("path"):
                    print(f"    {item['path']}")
                if item.get("snippet"):
                    print(f"    {item['snippet']}")
        return 0 if result["hit_count"] else 1

    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
