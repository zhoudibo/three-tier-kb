---
name: three-tier-kb
description: 三层架构本地知识库构建与运维——元索引/项目索引/原始内容三层查询结构、多客户端增量同步、12 项质量门禁、Graph View 孤儿修复。当用户说「搭建本地知识库」「三层知识库」「知识库索引」「让多个 AI 客户端共享一个知识库」「知识库质量体检」「知识库图谱连不上」「知识库同步」「vault 索引」「多智能体查同一个库」时使用。Use when: local knowledge base, three-tier index, obsidian vault indexing, multi-agent shared knowledge base, vault quality gate, graph view orphan, incremental sync, index priority.
version: 1.0.0
author: zhoudibo
license: MIT
platforms: [macos, linux]
metadata:
  hermes:
    tags: [knowledge-base, obsidian, indexing, multi-agent, sync, quality-gate, graph-view]
    category: note-taking
---

# 三层架构本地知识库（three-tier-kb）

把一堆散落的文件，变成**任何 AI 客户端都能秒查、全程可验证**的知识库。

这不是方法论拼凑，是一套在真实 vault（数十个项目、数百个文件、数千条 wikilink、
多套 AI 客户端、数十轮同步迭代）上跑出来的可执行资产。

---

## 一、为什么是三层

单层「全库扫描 + 全文匹配」在文件量过千后会同时踩三个坑：**慢**（每次查询扫全库）、
**糊**（搜到 200 个文件不知道该看哪个）、**不一致**（不同 agent 各搜各的，答案不一样）。

三层设计把每个问题交给最适合它的层：

| 层 | 名称 | 响应 | 内容 | 谁维护 |
|---|---|---|---|---|
| **L0** | 元索引 | <1s | `vault_index.json` / `topic_index.json` / `graph_index.json` | 脚本自动重建 |
| **L1** | 项目索引 | <5s | `<项目>/索引.md` | **人/AI 语义判断** |
| **L2** | 原始内容 | <30s | vault 内全部 MD | 唯一真相源 |

**硬规则：同一关键词命中多层时，L0 永远优先。** 这条规则让查询结果稳定可预测。

关键认知：
- **L0 是机器造的，L1 是人写的。** 项目索引表达「这个项目该放哪些东西」——
  这是纯语义判断，AI 全库扫描扫不出来，只能靠人或定向 AI 生成。
- **L0 和 L1 都可以从 L2 重建。** 所以它们是缓存，不是数据。缓存漂移了，重建即可。

---

## 二、资源索引

| 场景 | 读哪个 |
|---|---|
| 设计三层结构、决定索引层怎么分 | `references/three-tier-architecture.md` |
| 配置索引目录、主题映射、查询参数 | `references/index-configuration.md` |
| 多客户端同步、增量、路由、哈希基线 | `references/sync-protocol.md` |
| 跑质量门禁、解读 12 项检查结果 | `references/quality-gate-guide.md` |
| Graph View 孤儿修复、cluster 互链 | `references/graph-view-repair.md` |
| 直接填的知识卡模板 | `templates/knowledge-card.md` |
| 直接填的项目索引模板 | `templates/project-index.md` |

---

## 三、三条总纲

1. **唯一写者** —— 共享索引/汇总类文件只能有一个 agent 写，其余只读。
2. **可观测优先** —— 门禁第一次跑必然 FAIL/WARN。先列出具体缺失，再追求 0 WARN。
3. **可重建** —— 任何索引都能从原始内容重建。索引不是数据，是缓存。

---

## 四、标准作业顺序

```
盘点（只读扫描，出体检报告）
  → 立项（定项目清单 + 每个项目的索引.md）
  → 建层（配置 .kb_index.json → L0 元索引 + L1 项目索引）
  → 同步（多客户端增量入库）
  → 门禁（kb_gate.py → 迭代 3-6 轮 → FAIL=0）
  → 修复（孤儿归零 → 断链清零 → 图谱密度达标）
  → 运维（每日基线 + 5 分钟增量 + 对话驱动查询）
```

**每批操作前先备份**：`cp -R vault /tmp/kb_batchN_backup-$(date +%Y%m%d-%H%M%S)`
**任一 FAIL 立即回滚该批**，不要带病上线。

---

## 五、快跑命令

```bash
S=~/.hermes/skills/note-taking/three-tier-kb/scripts

# 1) 初始化配置模板（编辑 index_dirs / topic_map）
python3 $S/kb_index.py --vault ~/Documents/my-vault --init

# 2) 构建 L0 元索引（每日基线）
python3 $S/kb_index.py --vault ~/Documents/my-vault --build

# 3) 三层查询
python3 $S/kb_index.py --vault ~/Documents/my-vault --query "关键词"

# 4) 质量体检（12 项，退出码 1 = 有 FAIL）
python3 $S/kb_gate.py ~/Documents/my-vault

# 5) 多客户端增量同步
python3 $S/kb_sync.py --vault ~/Documents/my-vault --scan ~/workspace --route
```

---

## 六、运维节奏（已验证的双层模式）

| 任务 | 频率 | 命令 |
|---|---|---|
| 增量同步（多客户端 → vault） | 每 5 分钟 | `kb_sync.py --scan <workspace> --route` |
| 基线重建（L0 全量从零重建） | 每日固定时刻 | `kb_index.py --build` |
| 质量体检 | 每日基线后 | `kb_gate.py <vault>` |

**为什么基线要「从零重建」而不是增量更新**：
增量更新一旦漏掉一次删除，索引就永久漂移（幽灵条目）。从零重建慢一点，但**永远自愈**。

**为什么同步要 5 分钟增量而不是实时监听**：
实时监听（watchdog）在多客户端并发写入时锁竞争严重，且难以调试。
5 分钟 cron 的窗口足够小，失败可重试，日志可回溯。

---

## 七、对话驱动查询（无需用户执行任何命令）

用户的自然语言 → 自动路由到对应层：

| 用户说 | 查哪层 | 手段 |
|---|---|---|
| 「X 项目怎么样 / 有哪些进展」 | L1 | 项目索引 `索引.md` |
| 「我之前说过 X 吗」 | L2 | 会话库 FTS5 检索 |
| 「今天更新了吗 / 同步了吗」 | L0 | `vault_index.last_daily_sync` |
| 「立即同步 / 现在更新」 | — | 触发基线重建 |
| 「找一下 X 文件在哪」 | L0+L1 | `vault_index` + 项目索引 |
| 「X 和 Y 相似吗」 | 跨层 | 主题映射 + wikilink 邻接 |

**硬规则：用户不该需要记住任何 CLI 命令。** 智能体自主判断查哪层。

---

## 八、红线（不可违反）

- **扫描必须统一过滤**：`.git/`、`.obsidian/`、`.vscode/`、`node_modules/`、
  `__pycache__/`、`.venv/`、`venv/`、`site-packages/`、`dist/`、`build/`。
  过滤漂移会产生幽灵条目（实测一次依赖重装造成 数百条幽灵删除）。
- **shell 遍历带空格文件名用 `while IFS= read -r`**，禁止 `for f in $(find ...)`。
- **禁止用正则批量扫描改写 Markdown 正文**（曾有正则修 HTML 一次损坏 28 篇文章）。
  必须用 DOM 解析。
- **批量覆盖前先 read 旧内容再 patch**；自动生成段用 `<!-- AUTO-INDEX-START/END -->` 保护。
- **`.env` / `*credentials*` / token 一律不进库**，日志只写路径不写内容。
- **不要为每类资源单独建索引文件** —— 正确做法是在已有项目索引里内嵌 wikilink。
  独立索引文件会成为孤儿节点。
- **删除既有文件、或把内容推送到库外，必须先拿到用户明确授权。**

---

## 九、安装

```bash
# 从 GitHub 安装到 Hermes
git clone https://github.com/zhoudibo/three-tier-kb.git \
  ~/.hermes/skills/note-taking/three-tier-kb

# 验证
hermes skills list | grep three-tier-kb
```

也支持直接下载 `skills/three-tier-kb/` 目录放进 `~/.hermes/skills/note-taking/`。

---

## 十、为什么这套东西值得存在

知识库最难的不是「建」，是**让它一直活着**。

真实落地里，库崩溃的顺序通常是：文件进不来 → 索引不同步 → 图谱散架 → 没人信它 → 没人维护它。
每一层都在对抗其中一环：

- 三层索引 → 让查询**快且一致**，agent 才愿意用
- 哈希基线同步 → 让新内容**进得来**，库才不空
- 12 项门禁 → 让退化**看得见**，才能早修
- cluster 互链 → 让图谱**连得起**，人愿意翻

单独任何一环都不够。
