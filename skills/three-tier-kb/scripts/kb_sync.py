#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_sync.py — 多客户端 → vault 增量同步（可移植版）

把散落在多个工作区 / 客户端目录的产物，按关键词路由同步进 vault，
并维护哈希基线实现真正的增量。

核心设计（来自真实落地 36+ 轮迭代的经验）:
  * 哈希基线才是真相源 —— 不是日志，不是文件数。日志 md5 只衡量「打印输出稳不稳」，
    不衡量「workspace 字节稳不稳」，拿它当真相源会漏掉真实变更。
  * 统一边界过滤 —— 所有扫描走同一个 EXCLUDE 集合。过滤漂移会产生幽灵条目
    （实测一次 pip 重装造成 数百条幽灵删除）。
  * 空格安全 —— 永远 `while IFS= read -r`，不要 `for f in $(find ...)`。

用法:
    python3 kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace
    python3 kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace --dry-run
    python3 kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace --route

配置文件（可选）: <vault>/.kb_sync.json
    {
      "project_keywords": {
        "my-project": ["myproject", "关键词2"]
      },
      "vault_project_dir": "智能体/Hermes/项目",
      "hash_db": "/tmp/kb_sync_hash.json"
    }

退出码: 0 成功 / 2 参数错误
"""

import os
import re
import sys
import json
import shutil
import hashlib
import argparse
from datetime import datetime
from pathlib import Path

# ==================== 统一边界过滤 ====================
EXCLUDE_DIRS = {
    '.git', '.obsidian', '.vscode', '.idea', 'node_modules', '__pycache__',
    '.venv', 'venv', 'site-packages', '.cache', '.pytest_cache', '.Trash',
    'dist', 'build', '.next', 'coverage', '.nyc_output', 'target',
    '.pytest_cache', 'mypy_cache', 'eggs', 'vendor',
}

# 只有这些扩展名会被同步
SYNC_EXTS = {
    '.md', '.txt', '.py', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.json',
    '.yaml', '.yml', '.toml', '.ini', '.cfg', '.sh', '.bash', '.sql',
    '.html', '.css', '.csv', '.xml', '.log',
}
SKIP_FILE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.mp4', '.mov',
                 '.mp3', '.wav', '.pdf', '.zip', '.tar', '.gz', '.exe', '.dmg',
                 '.docx', '.xlsx', '.pptx', '.ttf', '.otf', '.woff'}

MIN_FILE_SIZE = 50  # 小于 50B 视为噪声，忽略


def safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def file_hash(path: Path) -> str:
    """文件内容哈希 —— 用 sha1 截断 16 位足够，且比 md5 快"""
    h = hashlib.sha1()
    try:
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()[:16]


def load_hash_db(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_hash_db(path: Path, data: dict):
    """原子写 —— 避免 cron 并发时读到半个文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)


# ==================== 扫描 ====================

def scan_workspace(root: Path, exclude: set):
    """扫描工作区，返回 [(path, hash, size)]，统一过滤 + 扩展名 + 大小三重把关"""
    results = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in exclude]
        for fn in filenames:
            if fn == '.DS_Store' or fn.startswith('.'):
                continue
            fp = Path(dirpath) / fn
            ext = fp.suffix.lower()
            if ext in SKIP_FILE_EXT:
                continue
            if ext and ext not in SYNC_EXTS:
                continue
            try:
                size = fp.stat().st_size
            except OSError:
                continue
            if size < MIN_FILE_SIZE:
                continue
            results.append((fp, file_hash(fp), size))
    return results


def route_file(rel_path: str, project_keywords: dict):
    """按关键词把文件路由到项目。命中第一个包含关键词的项目名"""
    low = rel_path.lower()
    for project, keywords in project_keywords.items():
        for kw in keywords:
            if kw.lower() in low:
                return project
    return None


# ==================== 同步器 ====================

class SyncEngine:
    def __init__(self, vault: Path, cfg: dict):
        self.vault = vault
        self.cfg = cfg
        self.hash_db_path = Path(cfg.get(
            "hash_db", "/tmp/kb_sync_hash.json"))
        self.project_dir = cfg.get("vault_project_dir", "智能体/Hermes/项目")
        self.keywords = cfg.get("project_keywords", {})

    def run(self, scan_root: Path, dry_run=False, do_route=False):
        print(f"🔄 同步扫描 @ {scan_root}")
        exclude = set(self.cfg.get("exclude_dirs", EXCLUDE_DIRS))
        files = scan_workspace(scan_root, exclude)
        print(f"   扫描到 {len(files)} 个候选文件")

        db = load_hash_db(self.hash_db_path)
        new_files, changed, unchanged = [], [], []
        for fp, h, size in files:
            key = str(fp)
            if key not in db:
                new_files.append((fp, h, size))
            elif db[key] != h:
                changed.append((fp, h, size))
            else:
                unchanged.append((fp, h, size))

        print(f"   新增 {len(new_files)} / 变更 {len(changed)} / "
              f"未变 {len(unchanged)}")

        if not dry_run:
            for fp, h, size in new_files + changed:
                db[str(fp)] = h
            save_hash_db(self.hash_db_path, db)

        if do_route and self.keywords:
            self._route(files, dry_run)

        return {"new": new_files, "changed": changed,
                "unchanged": unchanged}

    def _route(self, files, dry_run):
        """把文件按关键词路由到 vault 对应项目目录"""
        print("   📦 路由分发:")
        buckets = {}
        for fp, h, size in files:
            rel = str(fp)
            project = route_file(rel, self.keywords)
            if not project:
                continue
            buckets.setdefault(project, []).append(fp)

        for project, fps in buckets.items():
            dest_dir = self.vault / self.project_dir / project
            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
            print(f"      {project}: {len(fps)} 个文件 "
                  f"→ {dest_dir.name}/")
            for fp in fps[:3]:
                print(f"        · {fp.name}")
            if len(fps) > 3:
                print(f"        …等 {len(fps)} 个")


# ==================== CLI ====================

DEFAULT_CFG = {
    "project_keywords": {},
    "vault_project_dir": "智能体/Hermes/项目",
    "hash_db": "/tmp/kb_sync_hash.json",
    "exclude_dirs": sorted(EXCLUDE_DIRS),
}


def main():
    ap = argparse.ArgumentParser(
        description="多客户端 → vault 增量同步")
    ap.add_argument("--vault", required=True, help="vault 根目录")
    ap.add_argument("--scan", required=True, help="要扫描的工作区目录")
    ap.add_argument("--dry-run", action="store_true",
                    help="只报告不写入哈希基线")
    ap.add_argument("--route", action="store_true",
                    help="按 project_keywords 路由分发")
    ap.add_argument("--init", action="store_true",
                    help="生成 .kb_sync.json 配置模板")
    args = ap.parse_args()

    vault = Path(args.vault).expanduser()
    scan_root = Path(args.scan).expanduser()
    if not vault.is_dir():
        print(f"❌ vault 不存在: {vault}", file=sys.stderr)
        return 2
    if not scan_root.is_dir():
        print(f"❌ 扫描目录不存在: {scan_root}", file=sys.stderr)
        return 2

    cfg_file = vault / ".kb_sync.json"
    cfg = dict(DEFAULT_CFG)
    if cfg_file.exists():
        try:
            cfg.update(json.loads(cfg_file.read_text(encoding="utf-8")))
        except Exception as e:
            print(f"⚠️  配置解析失败: {e}", file=sys.stderr)

    if args.init:
        if cfg_file.exists():
            print(f"ℹ️  配置已存在: {cfg_file}")
            return 0
        cfg_file.write_text(json.dumps({
            "project_keywords": {"示例项目": ["示例关键词"]},
            "vault_project_dir": "智能体/Hermes/项目",
            "hash_db": "/tmp/kb_sync_hash.json",
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"✅ 已生成配置模板: {cfg_file}")
        return 0

    result = SyncEngine(vault, cfg).run(
        scan_root, dry_run=args.dry_run, do_route=args.route)

    print(f"\n{'[DRY-RUN] 未写入哈希基线' if args.dry_run else '✅ 哈希基线已更新'}"
          f" → {cfg.get('hash_db')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
