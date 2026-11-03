# 索引配置指南

所有配置都是**可选**的 JSON 文件，放在 vault 根目录。缺省即可运行。

---

## 一、`.kb_index.json` —— 索引引擎配置

```json
{
  "index_dirs": [
    "智能体/*/项目/*",
    "Projects/*"
  ],
  "topic_map": {
    "关键词A": "项目名A",
    "关键词B": "项目名B"
  },
  "exclude_dirs": [
    ".git", "node_modules"
  ]
}
```

### `index_dirs` —— 项目索引目录（glob）

**作用**：告诉索引引擎去哪里找 `索引.md` / `index.md`。

```bash
python3 $S/kb_index.py --vault ~/Documents/my-vault --init
# 生成模板后按需编辑
```

常见形态：

| vault 结构 | index_dirs |
|---|---|
| `智能体/<客户端>/项目/<项目>/索引.md` | `["智能体/*/项目/*"]` |
| `Projects/<项目>/索引.md` | `["Projects/*"]` |
| 扁平 `项目/<项目>/索引.md` | `["项目/*"]` |
| 多层嵌套 | `["**/项目/*"]`（Python glob recursive=True） |

**注意**：glob 匹配的是**目录**，脚本会在匹配到的目录下找 `索引.md` 或 `index.md`。

### `topic_map` —— 主题关键词 → 项目映射

**作用**：让 L0 元索引能按「关键词」而非「项目名」路由。

```json
"topic_map": {
  "视频": "短视频项目",
  "video": "短视频项目",
  "剪辑": "短视频项目"
}
```

**一个关键词可以映射多个项目吗？** 当前实现是 1:1（值是项目名字符串）。
如果同一个关键词应该路由到多个项目，写多条不同的关键词。

**未配置时**：`topic_index.json` 的 `topics` 为空，`--build` 会提示跳过。
这不是错误，只是失去了 L0 的主题路由能力。

**怎么选关键词**：用**你在对话里真的会说的词**，不是代码里的类名。
「视频」比 `video_pipeline` 好，因为用户说的是「视频」。

### `exclude_dirs` —— 额外排除目录

默认已排除：`.git` `.obsidian` `.vscode` `.idea` `node_modules` `__pycache__`
`.venv` `venv` `site-packages` `.cache` `.pytest_cache` `.Trash`
`dist` `build` `.next` `coverage` `.nyc_output` `target`

配置项里的值会**追加**到默认集合，不会覆盖默认集合：

```json
"exclude_dirs": ["my_private_dir", "some_build_output"]
```

---

## 二、`.kb_sync.json` —— 同步配置

```json
{
  "project_keywords": {
    "项目名A": ["关键词1", "关键词2", "project-a"],
    "项目名B": ["关键词3"]
  },
  "vault_project_dir": "智能体/Hermes/项目",
  "hash_db": "/tmp/kb_sync_hash.json"
}
```

### `project_keywords` —— 文件路由规则

**匹配逻辑**：对文件的**完整绝对路径**做小写包含匹配，命中第一个匹配的项目。

```python
def route_file(rel_path, project_keywords):
    low = rel_path.lower()
    for project, keywords in project_keywords.items():
        for kw in keywords:
            if kw.lower() in low:
                return project
    return None
```

**设计要点**：
- 匹配的是**路径**不是文件名 —— 因为项目名通常出现在目录名里
- **顺序敏感** —— 第一个命中的项目胜出，短的/通用的关键词放前面
- 建议为每个项目至少配 1 个**高区分度**关键词（如 `projcodename`），
  通用词（`app`、`web`）放最后，避免误抢

### `vault_project_dir` —— 落地目录

相对 vault 根的路径。默认 `智能体/Hermes/项目`。

### `hash_db` —— 哈希基线位置

**这个文件是同步的真相源。**

| 位置 | 影响 |
|---|---|
| `/tmp/`（默认） | 每次重启丢失 → 下次同步全量重扫（安全，只是慢） |
| 固定路径（如 `~/.kb_sync_hash.json`） | 持久化 → 真正的增量 |

放在 `/tmp` 的好处：不会把状态文件混进 vault，也不会误提交到 git。
放在固定路径的好处：重启后仍然增量。

---

## 三、配置优先级

```
命令行参数 > 环境变量 > 配置文件 > 代码默认值
```

目前所有脚本都是「配置文件 > 代码默认值」，命令行只覆盖 vault / scan 路径。

---

## 四、最小可用配置

**什么都不配也能跑**。默认配置适用于最常见的结构：

```
vault/
├── 00_总导航.md
└── 智能体/<客户端>/项目/<项目>/索引.md
```

想跑得更好，加两样：

1. `index_dirs` —— 告诉引擎你的项目目录长什么样
2. `topic_map` —— 列出用户会说的关键词

---

## 五、排查清单

| 症状 | 检查 |
|---|---|
| `--build` 找到 0 个项目 | `index_dirs` glob 没匹配上，手动验证：`python3 -c "import glob;print(glob.glob('/your/vault/智能体/*/项目/*',recursive=True))"` |
| 查询 L0 永远空 | `topic_map` 没配，或 `.kb_meta` 没生成 |
| 每次同步都是「新增 N」 | 哈希基线没持久化，换到固定路径 |
| 同步把不该同步的文件拉进来了 | `exclude_dirs` 没覆盖，或文件扩展名在 `SYNC_EXTS` 里 |
| 路由到了错误的项目 | `project_keywords` 顺序问题，把高区分度关键词提前 |
