# 同步协议

多客户端 → vault 的增量同步。这份协议里的每条规则都对应一个真实踩过的坑。

---

## 一、核心原则

> **哈希基线是真相源。日志不是，文件数不是，mtime 不是。**

```python
import hashlib
db_md5 = hashlib.md5(open("/tmp/kb_sync_hash.json", "rb").read()).hexdigest()
```

**为什么不能用日志 md5**：
日志 md5 衡量的是「脚本打印输出的稳定性」，不是「workspace 字节的稳定性」。
实测有 30+ 轮「字节一致」只是巧合相关，不是不变式。用日志当真相源会漏掉真实变更。

**为什么不能用文件数**：
文件数不变但内容全变（批量重写），文件数是骗人的。

---

## 二、三重过滤

同步一个 workspace 到 vault，必须过滤三样东西：

### 1. 目录黑名单（统一）

```python
EXCLUDE_DIRS = {
    '.git', '.obsidian', '.vscode', '.idea', 'node_modules', '__pycache__',
    '.venv', 'venv', 'site-packages', '.cache', '.pytest_cache', '.Trash',
    'dist', 'build', '.next', 'coverage', '.nyc_output', 'target',
    '.pytest_cache', 'mypy_cache', 'eggs', 'vendor',
}
```

> **真实教训**：一次 pip 重装，`.git` 内部文件变化被当成 workspace 内容变更，
> 产生了 **数百条幽灵删除**。根因是 watcher 和 re-scan 用了**不同的过滤集合**。
> 修复：所有扫描走**同一个函数**，过滤漂移物理上不可能发生。

### 2. 扩展名白名单

```python
SYNC_EXTS = {'.md', '.txt', '.py', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.json',
             '.yaml', '.yml', '.toml', '.ini', '.cfg', '.sh', '.bash', '.sql',
             '.html', '.css', '.csv', '.xml', '.log'}
```

### 3. 大小下限

```python
MIN_FILE_SIZE = 50   # 字节
```

小于 50B 的文件基本是空壳、锁文件、残留。**但要区分「有效小文件」和「噪声」**：
真实教训里，冷知识项目的 2-6 个文件是真内容，不能一刀切靠大小过滤。

---

## 三、空格安全（最常见的 shell bug）

```bash
# ❌ 错误 —— 带空格的文件名会被拆开
for f in $(find . -name "*.md"); do
    echo "$f"
done

# ✅ 正确
find . -name "*.md" -print0 | while IFS= read -r -d '' f; do
    echo "$f"
done
```

**症状**：文件名里有空格时，脚本只处理了前半段，剩下的静默丢失。
**表现**：同步「成功」，但 vault 里少文件，而且不报错。

Python 侧对应写法：

```python
# ❌ 不要这样
for f in subprocess.check_output("find ...").split("\n"): ...

# ✅ 这样
import subprocess
out = subprocess.check_output(["find", ".", "-name", "*.md", "-print0"])
for f in out.decode().split("\0"):
    ...
```

---

## 四、哈希基线的原子写

cron 任务可能和手动运行并发。直接写文件会读到半个 JSON。

```python
def save_hash_db(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)     # 原子替换
```

---

## 五、三态判定

每次扫描后，每个文件处于三种状态之一：

| 状态 | 判定 | 动作 |
|---|---|---|
| **新增** | 路径不在基线里 | 同步 + 写入基线 |
| **变更** | 路径在基线里但 hash 不同 | 同步 + 更新基线 |
| **未变** | 路径在基线里且 hash 相同 | 跳过 |

```python
if key not in db:
    new_files.append(...)
elif db[key] != h:
    changed.append(...)
else:
    unchanged.append(...)
```

**注意**：判定用的是**文件内容 hash**，不是 mtime。mtime 会因为 `touch`、
版本控制 checkout、编辑器保存而变，内容却没变 —— 这会产生大量无意义同步。

---

## 六、路由：文件该进哪个项目

```python
def route_file(path_str, project_keywords):
    low = path_str.lower()
    for project, keywords in project_keywords.items():
        for kw in keywords:
            if kw.lower() in low:
                return project
    return None
```

**三条要点**：
1. 匹配**完整路径**，不是文件名（项目名通常在目录名里）
2. **顺序敏感**，第一个命中胜出
3. 高区分度关键词放前面：`{"短视频项目": ["hyperframes", "video"], "通用": ["app"]}`

---

## 七、调度：为什么是 5 分钟 cron

| 方案 | 优点 | 缺点 | 结论 |
|---|---|---|---|
| 实时文件监听（watchdog） | 延迟最低 | 多客户端并发写入时锁竞争严重；崩溃后状态难恢复；调试困难 | ❌ |
| 5 分钟 cron | 窗口足够小；失败可重试；日志可回溯；无锁竞争 | 最坏 5 分钟延迟 | ✅ |
| 每日一次 | 最省资源 | 白天工作时的内容要等一天 | ❌ |

**与基线的关系**：
- 5 分钟增量 = 白天保持新鲜度
- 每日基线 = 兜底自愈（把任何漂移抹平）

两者是**互补**，不是二选一。

---

## 八、每日基线重建（从零，不是增量）

```bash
python3 kb_index.py --vault ~/Documents/my-vault --build
```

**为什么从零重建**：
增量更新一旦漏掉一次删除，索引就会永久漂移，表现为「幽灵条目」——
索引里指向一个已经不存在的文件。这类漂移很难排查，因为「多出来的条目」比
「少掉的条目」难发现得多。

从零重建慢一点（30 秒），但**永远自愈**。

---

## 九、同步自检清单

每次同步后确认：

- [ ] 新增/变更/未变三个数字之和 = 扫描总数
- [ ] 哈希基线文件已更新（非 dry-run）
- [ ] 路由分发的文件确实落到了目标项目目录
- [ ] 没有 `.git` / `node_modules` 内容混入
- [ ] 带空格的文件名没丢失

---

## 十、故障排查

| 症状 | 根因 | 修复 |
|---|---|---|
| 每次都全量「新增」 | 哈希基线没持久化 | `hash_db` 换到固定路径 |
| 大量幽灵删除/新增 | 过滤集合不一致 | 所有扫描统一走 `walk_files()` |
| 文件名含空格就丢 | shell 循环没转义 | `while IFS= read -r -d ''` |
| 同步后 vault 里出现依赖目录 | `exclude_dirs` 没覆盖 | 补进排除列表 |
| 哈希库损坏 | 并发写入 | 原子写 + `.tmp` 中转 |
| 路由到错误项目 | 关键词顺序 | 高区分度关键词提前 |
| 内容变了但没同步 | 用了 mtime 判定 | 改用内容 hash |
