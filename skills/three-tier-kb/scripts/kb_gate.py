#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
kb_gate.py — 知识库质量门禁（可移植版，仅标准库，只读）

12 项检查，覆盖知识库最容易崩坏的地方：
  孤儿文件 / 断链 / frontmatter 完整性 / 出链覆盖 / 入链覆盖 /
  敏感文件 / 索引一致性 / 重复标题 / 空文件 / 未来日期 / 库结构 / 图谱密度

设计原则（来自真实落地经验）:
  * 可观测优先 —— 第一次跑必然 FAIL/WARN。先列出具体缺失文件，再追求 0 WARN。
  * 只读 —— 本脚本绝不写入、绝不移动、绝不删除任何文件。
  * 有 FAIL 就不要带病上线 —— 回滚该批，重跑，再上线。

用法:
    python3 kb_gate.py <vault-root> [--top 10] [--json]

退出码: 0 放行 / 1 拦截（有 FAIL）/ 2 参数错误
"""

import os
import re
import sys
import json
import argparse
from datetime import date
from pathlib import Path
from collections import defaultdict

EXCLUDE_DIRS = {
    '.git', '.obsidian', '.vscode', '.idea', 'node_modules', '__pycache__',
    '.venv', 'venv', 'site-packages', '.cache', '.pytest_cache', '.Trash',
    'dist', 'build', '.next', 'coverage', '.nyc_output', 'target',
}

REQUIRED_FM = ['title', 'type', 'owner', 'status', 'version',
               'effective_date', 'review_date', 'source', 'confidentiality']

WIKILINK_RE = re.compile(r'\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]')
DATE_RE = re.compile(r'^(\d{4})-(\d{2})-(\d{2})$')
SENSITIVE_NAME = re.compile(
    r'(\.env|credentials|secret[_-]?key|api[_-]?key|access[_-]?token|'
    r'\.pem$|\.key$|id_rsa)', re.I)
SENSITIVE_CONTENT = re.compile(
    r'(sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|'
    r'-----BEGIN [A-Z ]*PRIVATE KEY-----)')


class Gate:
    def __init__(self, root, top=10):
        self.root = Path(root).expanduser().resolve()
        self.top = top
        self.passed = 0
        self.warn = 0
        self.failed = 0
        self.issues = []
        self.mds = []
        self.texts = {}
        self.fm = {}
        self.outlinks = {}
        self.inlinks = defaultdict(int)
        self.stems = set()

    # ---------- 输出 ----------
    def ok(self, msg):
        self.passed += 1
        print(f"  ✅ PASS  {msg}")

    def warn_(self, msg):
        self.warn += 1
        self.issues.append(("WARN", msg))
        print(f"  ⚠️  WARN  {msg}")

    def fail(self, msg):
        self.failed += 1
        self.issues.append(("FAIL", msg))
        print(f"  ❌ FAIL  {msg}")

    def rel(self, p):
        try:
            return str(Path(p).relative_to(self.root))
        except ValueError:
            return str(p)

    def head(self, items):
        items = list(items)
        if not items:
            return ""
        s = '、'.join(items[:self.top])
        return s + (f' …等 {len(items)} 个' if len(items) > self.top else '')

    # ---------- 扫描 ----------
    def scan(self):
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                if fn == '.DS_Store':
                    continue
                full = Path(dirpath) / fn
                if fn.lower().endswith('.md'):
                    self.mds.append(full)
        for p in self.mds:
            self.texts[p] = safe_read(p)
            self.stems.add(p.stem)

    def parse_fm(self, path):
        txt = self.texts.get(path, '')
        if not txt.startswith('---'):
            return {}
        m = re.match(r'^---\n(.*?)\n---', txt, re.S)
        if not m:
            return {}
        fm = {}
        for line in m.group(1).split('\n'):
            if ':' in line and not line.strip().startswith('-'):
                k, _, v = line.partition(':')
                fm[k.strip()] = v.strip().strip('"').strip("'")
        return fm

    def build_graph(self):
        # 索引表：stem -> 相对路径集合，用于路径式 wikilink 解析
        # Obsidian 的 [[dir/file]] 按路径解析，[[file]] 按文件名解析，两种都要认
        self.rel_stems = defaultdict(set)
        for p in self.mds:
            rel = self.rel(p)
            self.rel_stems[rel[:-3]].add(rel)      # 无扩展名的相对路径
            self.rel_stems[p.stem].add(rel)          # 纯文件名
        for p in self.mds:
            self.fm[p] = self.parse_fm(p)
            links = set()
            for raw in WIKILINK_RE.findall(self.texts.get(p, '')):
                t = Path(raw.strip()).stem
                if not t:
                    continue
                links.add(t)
                self.inlinks[t] += 1
            self.outlinks[p] = links

    # ---------- 12 项检查 ----------
    def c01_structure(self):
        print("[1/12] 库结构与入口...")
        if not self.root.is_dir():
            self.fail(f"目录不存在: {self.root}")
            return
        names = set(os.listdir(self.root))
        has_index = any(('索引' in n) or ('index' in n.lower())
                        or ('导航' in n) or ('moc' in n.lower())
                        for n in names)
        if has_index:
            self.ok(f"库入口索引齐备（{len(self.mds)} 篇 MD）")
        else:
            self.fail("既无索引也无总导航，等于没有库 —— 新 agent 接入会迷路")

    def c02_orphan(self):
        print("[2/12] 孤儿文件（无入链且无出链）...")
        orphans = [p for p in self.mds
                   if not self.outlinks.get(p) and self.inlinks.get(p.stem, 0) == 0]
        idx_like = [p for p in orphans
                    if '索引' in p.name or 'index' in p.name.lower()
                    or 'AGENTS' in p.name]
        real = [p for p in orphans if p not in idx_like]
        if not real:
            self.ok("孤儿文件数 = 0")
        elif len(real) <= 20:
            self.warn_(f"孤儿 {len(real)} 个: {self.head([self.rel(p) for p in real])}")
        else:
            self.fail(f"孤儿 {len(real)} 个（>20 说明库已散架）: "
                      f"{self.head([self.rel(p) for p in real])}")

    def c03_broken(self):
        print("[3/12] 断链（wikilink 指向不存在的文件）...")
        total = 0
        samples = []
        for p in self.mds:
            for raw in WIKILINK_RE.findall(self.texts.get(p, '')):
                target = raw.strip()
                t = Path(target).stem
                if not t:
                    continue
                # 路径式 wikilink（a/b/file）先按路径找，再退回按文件名找
                path_key = target[:-3] if target.endswith('.md') else target
                resolved = (path_key in self.rel_stems) or (t in self.stems)
                if not resolved:
                    total += 1
                    if len(samples) < self.top:
                        samples.append(f"{self.rel(p)}→{target}")
        if total == 0:
            self.ok("无断链")
        elif total <= 30:
            self.warn_(f"断链 {total} 处: {self.head(samples)}")
        else:
            self.fail(f"断链 {total} 处（>30 说明发生过批量改名/迁移未修链）")

    def c04_frontmatter(self):
        print("[4/12] frontmatter 9 字段完整率...")
        if not self.mds:
            self.warn_("库内无 MD 文件")
            return
        missing = [p for p in self.mds
                   if any(k not in self.fm.get(p, {}) for k in REQUIRED_FM)]
        ratio = int((len(self.mds) - len(missing)) * 100 / len(self.mds))
        if ratio >= 90:
            self.ok(f"{ratio}% 卡片 9 字段齐全（{len(self.mds)-len(missing)}"
                    f"/{len(self.mds)}）")
        elif ratio >= 60:
            self.warn_(f"{ratio}% 齐全，缺字段的: "
                       f"{self.head([self.rel(p) for p in missing])}")
        else:
            self.fail(f"仅 {ratio}% 齐全，库处于失控状态")

    def c05_outlink(self):
        print("[5/12] 出链覆盖（每卡 ≥1 条 wikilink）...")
        if not self.mds:
            self.warn_("库内无 MD 文件")
            return
        dead = [p for p in self.mds if not self.outlinks.get(p)]
        ratio = int((len(self.mds) - len(dead)) * 100 / len(self.mds))
        if ratio >= 80:
            self.ok(f"{ratio}% 卡片有出链（库是活的）")
        elif ratio >= 50:
            self.warn_(f"{ratio}% 有出链，无出链的: "
                       f"{self.head([self.rel(p) for p in dead])}")
        else:
            self.fail(f"仅 {ratio}% 有出链，库是一堆死文件")

    def c06_inlink(self):
        print("[6/12] 入链覆盖（被引用率）...")
        if not self.mds:
            self.warn_("库内无 MD 文件")
            return
        dead = [p for p in self.mds if self.inlinks.get(p.stem, 0) == 0]
        ratio = int((len(self.mds) - len(dead)) * 100 / len(self.mds))
        if ratio >= 70:
            self.ok(f"{ratio}% 卡片被引用过")
        elif ratio >= 40:
            self.warn_(f"{ratio}% 被引用，未被引用的: "
                       f"{self.head([self.rel(p) for p in dead])}")
        else:
            self.fail(f"仅 {ratio}% 被引用，图谱是散的")

    def c07_sensitive(self):
        print("[7/12] 敏感文件与凭据扫描...")
        bad_names = []
        bad_content = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
            for fn in filenames:
                full = Path(dirpath) / fn
                if SENSITIVE_NAME.search(fn):
                    bad_names.append(self.rel(full))
                    continue
                if fn.lower().endswith(('.md', '.txt', '.json', '.yaml', '.yml', '.env')):
                    content = safe_read(full)
                    if SENSITIVE_CONTENT.search(content):
                        bad_content.append(self.rel(full))
        if bad_names or bad_content:
            self.fail(f"敏感文件 {self.head(bad_names)} / "
                      f"疑似凭据内容 {self.head(bad_content)}")
        else:
            self.ok("无敏感文件，无明文凭据")

    def c08_duplicate_title(self):
        print("[8/12] 重复标题...")
        titles = defaultdict(list)
        for p in self.mds:
            t = self.fm.get(p, {}).get('title', '')
            if t:
                titles[t].append(p)
        dups = {t: ps for t, ps in titles.items() if len(ps) > 1}
        if not dups:
            self.ok("无重复标题")
        else:
            sample = '; '.join(f"{t}×{len(ps)}" for t, ps in list(dups.items())[:5])
            self.warn_(f"重复标题 {len(dups)} 组: {sample}")

    def c09_empty_file(self):
        print("[9/12] 空文件 / 超小文件...")
        small = [p for p in self.mds if len(self.texts.get(p, '').strip()) < 80]
        if not small:
            self.ok("无空文件")
        else:
            self.warn_(f"{len(small)} 个近空文件: "
                       f"{self.head([self.rel(p) for p in small])}")

    def c10_future_date(self):
        print("[10/12] 日期有效性（未来日期检测）...")
        today = date.today()
        bad = []
        for p in self.mds:
            for key in ('effective_date', 'created', 'updated', 'date'):
                v = self.fm.get(p, {}).get(key, '')
                m = DATE_RE.match(v.strip())
                if m:
                    try:
                        d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                        if d > today:
                            bad.append(f"{self.rel(p)}({key}={v})")
                    except ValueError:
                        bad.append(f"{self.rel(p)}({key}={v} 非法)")
                    break
        if not bad:
            self.ok("无未来日期 / 非法日期")
        else:
            self.warn_(f"未来或非法日期 {len(bad)} 处: {self.head(bad)}")

    def c11_index_consistency(self):
        print("[11/12] 索引一致性（元索引 vs 实际文件）...")
        meta = self.root / ".kb_meta" / "vault_index.json"
        if not meta.exists():
            self.warn_("未找到 .kb_meta/vault_index.json，"
                       "先运行 kb_index.py --build 再复检")
            return
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception as e:
            self.fail(f"vault_index.json 解析失败: {e}")
            return
        actual_md = len(self.mds)
        indexed_md = data.get("total_md_files", 0)
        diff = abs(actual_md - indexed_md)
        pct = int(diff * 100 / max(actual_md, 1))
        if diff == 0:
            self.ok(f"元索引与实际一致（{actual_md} MD）")
        elif pct <= 5:
            self.warn_(f"元索引落后 {diff} 个 MD（{pct}%），"
                       "说明基线后有新增文件未重建")
        else:
            self.fail(f"元索引漂移 {diff} 个 MD（{pct}%），基线已过期")

    def c12_graph_density(self):
        print("[12/12] 图谱密度（每节点平均出链）...")
        if not self.mds:
            self.warn_("库内无 MD 文件")
            return
        edges = sum(len(v) for v in self.outlinks.values())
        density = edges / max(len(self.mds), 1)
        if density >= 2.0:
            self.ok(f"图谱密度 {density:.1f} 出链/节点（结构健康）")
        elif density >= 1.0:
            self.warn_(f"图谱密度 {density:.1f} 出链/节点（偏稀，建议加 cluster 互链）")
        else:
            self.fail(f"图谱密度仅 {density:.1f}（库是一堆孤立卡片）")

    # ---------- 运行 ----------
    def run(self):
        print(f"\n🔍 知识库质量门禁 @ {self.root}\n")
        self.scan()
        self.build_graph()
        for fn in (self.c01_structure, self.c02_orphan, self.c03_broken,
                   self.c04_frontmatter, self.c05_outlink, self.c06_inlink,
                   self.c07_sensitive, self.c08_duplicate_title,
                   self.c09_empty_file, self.c10_future_date,
                   self.c11_index_consistency, self.c12_graph_density):
            fn()
        total = self.passed + self.warn + self.failed
        print(f"\n检查结果：{self.passed} PASS / {self.warn} WARN / "
              f"{self.failed} FAIL（共 {total} 项）")
        if self.failed:
            print("❌ 拦截：有 FAIL，禁止带病上线，回滚该批后重跑")
        else:
            print("✅ 放行：FAIL = 0")
        return self.failed == 0


def safe_read(path):
    try:
        return Path(path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description="知识库质量门禁")
    ap.add_argument("vault", help="vault 根目录")
    ap.add_argument("--top", type=int, default=10, help="每项最多展示多少个样本")
    ap.add_argument("--json", action="store_true", help="输出 JSON 摘要")
    args = ap.parse_args()

    root = Path(args.vault).expanduser()
    if not root.is_dir():
        print(f"❌ 目录不存在: {root}", file=sys.stderr)
        return 2

    gate = Gate(root, top=args.top)
    ok = gate.run()
    if args.json:
        print(json.dumps({
            "root": str(gate.root),
            "passed": gate.passed, "warn": gate.warn, "failed": gate.failed,
            "issues": [{"level": l, "message": m} for l, m in gate.issues],
        }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
