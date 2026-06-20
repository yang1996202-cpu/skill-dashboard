# Skill Dashboard — 项目约定

## GBrain / GStack

- 这个项目优先使用 GBrain MCP/HTTP 能力查询项目记忆；不要把本地 `gbrain search` CLI 当首选入口。
- GBrain HTTP 单例由 launchd 管理，健康检查用 `curl -sS http://127.0.0.1:14243/health`；不要探测旧端口 `14242`。
- 如果 MCP/HTTP 不可用，才使用 CLI fallback。Codex 桌面执行环境的 `PATH` 可能不包含 `~/.local/bin`，不要直接跑裸 `gbrain`；用稳定入口 `/Users/yang/.local/bin/gbrain`，这个 wrapper 会补上 bun 路径。当前机器上 CLI search 可能触发 macOS/PGLite/WASM 初始化失败；这种情况下只用 `doctor` 记录健康状态，继续用 HTTP/MCP 或仓库证据推进，不要反复重试同一条 CLI。
- 新的清理准则、扫描分类口径、可恢复删除边界等长期决策，应该同步写进本文件或项目文档，避免后续 Agent 重复摸索。

## 是什么

零依赖本地 WebUI，可视化管理本地 AI skill 文件。Python 标准库 http.server + 单文件前端 + 少量后端模块。

## 运行

```bash
python3 serve.py
# 自动打开 http://localhost:3457
```

端口固定 3457，绑定 127.0.0.1。

## 文件结构

```
serve.py           — 后端入口：HTTP handler + 路由编排
skilldash/paths.py          — 共享路径、端口、缓存文件定位
skilldash/classification.py — skill 分类关键词和描述读取
skilldash/discovery.py      — skill 目录发现、Agent 推断、目录治理分层
skilldash/overlap.py        — 跨目录同名、完全重复扫描
skilldash/cleanup.py        — 清理计划、执行预案、重复 skill 处理准则
skilldash/content_hash.py   — SKILL.md 内容 hash 追踪
skilldash/decisions.py      — 本地运行态决策（多端部署）
skilldash/understanding.py  — 离线理解层
_diag_worker.py    — 诊断子进程（由 serve.py 通过 subprocess 调用）
tests/             — 零依赖 unittest 回归测试（`python3 -m unittest discover -s tests -t .`）
index.html         — 前端 HTML 骨架（~150 行）
static/skill-dashboard.css — 前端样式
static/app-core.js         — 前端状态、数据加载、仪表盘、当前目录技能
static/issues-cleanup.js   — 问题与整理、清理计划、垃圾站
static/sources.js          — 全部目录技能、来源浏览、批量同步/删除
static/skill-detail.js     — skill 详情、对比、分类编辑
static/app-bootstrap.js    — 刷新、目标切换、诊断、安装入口、启动加载
.data/             — 运行时状态与缓存（state/、cache/，.gitignore）
docs/              — 项目文档（troubleshooting.md）
README.md
LICENSE
screenshots/       — 截图（当前只有 .gitkeep）
```

## 架构

```
浏览器 → index.html + static/* (静态)
          ↓ fetch API
       serve.py (HTTPServer, 端口 3457)
          ↓ 调用本地模块
       skilldash/{discovery,cleanup,overlap,understanding,...}
          ↓ 读文件系统
       本地 skill 目录 (如 ~/.codex/skills/)
          ↓ GitHub REST API (无认证)
       上游版本检测
```

**数据分层加载（二哥扫描模式）**：
1. **页面加载（秒开）**：`/api/fast-scan` + `/api/targets` + `/api/global-stats`
2. **用户手动扫描**：`/api/scan-run`（选目录 + 选分析类型）
3. **缓存读取**：`/api/scan-result`（上次扫描结果）

分析功能（同名/上游/内容变更）不自动触发，由用户在"问题与整理"页面的"二哥扫描"面板手动选择。

## 关键 API

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/fast-scan` | GET | 列出当前目标库的 skills 和当前 Agent commands |
| `/api/targets` | GET | 列出所有发现的 skill/commands 目录（按 Agent 分组） |
| `/api/scan-run` | POST | **二哥扫描**：用户选目录 + 分析类型，返回分析结果 |
| `/api/scan-result` | GET | 读取缓存的扫描结果 |
| `/api/global-stats` | GET | 全域分类分布统计（5 分钟缓存） |
| `/api/diagnose` | POST | 触发完整诊断（旧流程，保留兼容） |
| `/api/diagnosis-status` | GET | 轮询诊断进度 |
| `/api/scan` | GET | 返回最近一次完整扫描结果 |
| `/api/history` | GET | 操作历史记录 |
| `/api/installed-plugins` | GET | 读取 Claude 插件安装和启用状态 |
| `/api/target` | POST | 切换当前目标库 |
| `/api/source/skills` | GET | 读取来源 skill 列表 |
| `/api/custom-sources` | GET/POST/PATCH/DELETE | 管理自定义来源 |
| `/api/steal` | POST | 从 GitHub URL 安装 skill |
| `/api/copy-skill` | POST | 复制 skill 到当前目标库 |
| `/api/skill/{name}` | GET/DELETE | 获取/删除 skill |
| `/api/skill/{name}/content` | GET | 读取 SKILL.md 原始内容 |
| `/api/skill/{name}/upstream` | GET | 检查上游版本状态 |
| `/api/skill/{name}/update` | PATCH | 从上游更新 skill |
| `/api/skill/{name}/fix` | PATCH | 修复 skill 结构问题 |
| `/api/preview` | GET | 跨目录预览 skill 内容（?dir=xxx&name=xxx） |
| `/api/cleanup-plan` | GET | 生成目录治理计划（dry-run） |
| `/api/cleanup-execution-plan` | GET | 生成可执行形态的清理预案（仍是 dry-run） |
| `/api/cleanup-execute` | POST | 将选中的清理候选移入项目垃圾站 |
| `/api/duplicate-decision` | POST | 记录完全重复 skill 的本地处理决策，如多端部署副本 |
| `/api/batch-delete` | POST | 批量删除 skills（body: `{items: [{target, name}]}`） |
| `/api/openapi` | GET | 返回路由清单（调试用） |

## 设计决策

### 项目级 skill 分类：按 Agent 应用分组

**核心原则**：每个 Agent 应用（Claude、Kiro、Cursor 等）都有自己的全局目录和项目级目录。按照 Agent 的部署位置来区分 user (全局) vs project (项目级)。

**分类规则**：

1. **`user` 分类** — **Agent 全局技能**（在家目录下）
   - 模式：`~/.agent/skills/`（agent 是 Agent 应用名称）
   - 示例：
     - `~/.claude/skills/` ✓（Claude 全局）
     - `~/.kiro/skills/` ✓（Kiro 全局）
     - `~/.cursor/skills/` ✓（Cursor 全局）
   - 特征：路径正好是 `~/.agent/skills/` 三层结构
   - 作用域：全局（跨所有项目）

2. **`project` 分类** — **Agent 项目级技能** 或 **其他非全局位置**
   - **Agent 项目级**（标准路径）：
     - 模式：`~/projects/xxx/.agent/skills/`
     - 示例：
       - `~/projects/my-app/.claude/skills/` ✓
       - `~/projects/backend/.kiro/skills/` ✓
       - `~/code/foo/.cursor/skills/` ✓
     - 特征：`.agent/skills/` 在项目目录内（不在家目录下）
   
   - **其他项目级**（非标准路径）：
     - 示例：
       - `~/AI-Skills/` ✓（自定义技能库，不在家目录 agent 下）
       - `~/projects/my-skills-repo/skills/` ✓（技能集合仓库）
       - `~/Downloads/skills-pack/` ✓（下载目录）
     - 特征：不符合 `~/.agent/skills/` 模式的都算 project
   
   - 作用域：项目/特定场景（不是全局）

**关键判断逻辑**：
```python
# 1. 检查是否在 ~/.agent/skills/ 格式 → user
if _is_user_level_skill(dir_path):
    return "user"

# 2. 检查是否在项目内的 .agent/skills/ 格式 → project
if _is_project_agent_skill(dir_path):
    return "project"

# 3. 其他所有非标准路径 → project（因为不是全局的）
return "project"
```

**发现策略改进**：

借鉴 [skill-discover](https://github.com/yang1996202-cpu/skill-discover) 的递归扫描策略，在项目目录下使用 `os.walk` 递归查找 `.agent/skills/` 目录：

```python
# ~/projects/ 下递归扫描（深度限制 5 层）
for root, dirs, _files in os.walk(projects_dir):
    depth = root.count(os.sep) - str(projects_dir).count(os.sep)
    if depth >= 5:
        del dirs[:]  # 不再深入
        continue
    
    # 检查当前目录是否是 .agent 目录
    if os.path.basename(root).startswith("."):
        skills_path = Path(root) / "skills"
        if skills_path.is_dir():
            add_dir(skills_path)
```

这样可以发现任意深度的项目级 skill 目录，例如：
- `~/projects/foo/bar/.claude/skills/` ✓
- `~/projects/backend/src/.kiro/skills/` ✓

**与 Anthropic 对齐**：
- Anthropic 官方定义：User Skills (`~/.claude/skills/`) vs Project Skills (`<repo>/.claude/skills/`)
- 本项目扩展支持：多 Agent 应用 + 非标准路径的发现和分类
- 非标准路径统一归为 `project`，因为它们不是 Agent 的全局配置

**实现函数**：
- `_is_user_level_skill(dir_path)` — 判断是否为 `~/.agent/skills/` 格式
- `_is_project_agent_skill(dir_path)` — 判断是否为项目内 `.agent/skills/` 格式
- `_classify_skill_dir_detail(dir_path)` — 治理分类（category/layer/policy），UI 唯一分类入口

### 目录发现：不预设"谁是 Agent"

`_discover_skill_dirs()` 负责发现用户机器上所有 skill 目录。核心原则：

**只排除确信的系统垃圾（.Trash/.cache/.git），其他一律交给 SKILL.md 特征判断。**

不硬编码 Agent 名单、不预设哪些 `.xxx` 目录是 Agent。目录里有没有 `*/SKILL.md` 是唯一可靠的判断信号。排除列表和硬编码列表是一体两面——都是用人的判断替代数据特征。

扫描策略：
1. `~/.xxx/` — 任意隐藏目录，做 depth-3 递归特征扫描
2. `~/非隐藏/skills/` — 非隐藏目录的标准 skills/ 子目录
3. `~/projects/*//skills/` — 项目级 skill 目录
4. 配置文件 — `.skill-dashboard.json` + `custom-sources.json`

辅助函数 `_has_skill_md(d)` 验证目录是否包含 `*/SKILL.md`，是整个发现逻辑的最终判断。

### Agent 分组：_agent_from_path()

`_agent_from_path()` 从路径推断 Agent 名称。保留已知映射（`.codex` → `Codex`），但未知 `.xxx` 直接用目录名。**不在其他地方重复这个映射**——`_list_targets()` 等调用 `_agent_from_path()` 而不是自己维护 if/elif 列表。

### 扫描 API：用户选范围 + 选类型

`POST /api/scan-run` 接受 `{directories, checks}` 参数。只跑用户请求的分析类型（同名/上游/内容变更）在用户选择的目录上。

辅助函数 `_find_same_name_duplicates(dirs)` 接受 Path 列表参数，被 cleanup 和扫描复用。

### 清理执行准则：hash 一致不是直接删除依据

`/api/cleanup-execution-plan` 只生成预案，不直接改文件。推荐移入垃圾站的候选限定在：

- 备份、快照、导入副本、下载包、App 本地库等复核层目录
- `SKILL.md` 内容 hash 完全一致
- 保留副本仍存在，且执行前 hash 没有变化

其他 Agent 根目录里的完全重复 skill 不进垃圾站候选，归入 `deploy` 阶段，表示“多端部署副本”。用户点击“标记多端部署”后，写入 `.data/state/duplicate-decisions.json`，按 `skill_name + content_hash` 隐藏同一提醒；如果内容变化，hash 变化，提醒会重新出现。前端“本地决策”入口用于查看和撤销这些本机运行状态，帮助开源用户理解哪些信息不会随 Git 提交。

相似度/TF-IDF 不再作为当前主线能力。问题页优先解释来源、状态、同名/完全重复、上游和内容变更证据，避免把低置信文本相似误当成可删除依据。

Broken symlink 和目录壳里的 broken `SKILL.md` 会作为可清理残留展示，可移入项目垃圾站。删除接口只放开 symlink 或带 `SKILL.md` 标记的目录壳，不允许任意普通目录被误删。

### 前端数据流

- `loadData()` 只跑轻量 API（fast-scan、targets、global-stats）
- 扫描结果通过 `runScan()` 调 `/api/scan-run`，映射到 `health` 和 `globalOverlap` 变量
- `renderIssues()` 复用现有的卡片渲染逻辑
- `loadCachedScanResult()` 在页面加载时检查缓存

### 全部目录技能页 UX

- **分类折叠**：每个 Agent 卡片内，按 5 分类（user/marketplace/cache/cross-copy/project）分组显示，每个分类标题可点击展开/收起
- **分类一键删除**：分类子标题有 `🗑 删除全部` 按钮，调 `deleteCategoryDirs()`
- **Agent 级操作**：卡片头部有 `🗑 删除全部` 按钮和 `⭐ 常用目录` 切换
- **目录级操作**：每个目录行有 `切换为当前目录`、`设为常用目录`、`🗑` 按钮
- **拖拽排序**：仅从 `⋮⋮` 手柄触发拖拽（`draggable` 在 handle 上），不干扰文字选择/复制
- **删除后保留展开状态**：`refreshAfterDelete()` 重拉 targets 后恢复已展开卡片
- **Skill 内容查看**：点击 skill 名或"查看"按钮，调 `showSkill(name, dir)` 通过 `/api/preview` 跨目录查看

### 批量添加来源

- 对话框支持多行路径（一行一个），带引导说明（自己找 / 让 Agent 找）
- `addCustomSource()` 逐个验证路径，汇总结果（成功/失败/已存在）

### /api/targets 缓存

`_list_targets()` 带 3 分钟 TTL 内存缓存（`_targets_cache` / `_targets_cache_ts`）。冷启动 ~6s，缓存命中 ~0.1s。缓存期间 `is_current` 标志实时刷新（对比 state 里存的当前目标）。

前端 `loadData()` 的异步 targets 回调：如果 sources DOM 已有内容（用户已展开过），跳过 `renderSources()` 只更新 badge 数字，避免覆盖用户交互状态。

## 数据目录

- 状态与缓存：`.data/`（state/ 存 current-target.json/latest-scan.json，cache/ 存诊断结果和全域分类）
- 完全重复处理决策：`.data/state/duplicate-decisions.json`（本地运行态，不提交）
- Skill 快照：`<target>/.snapshots/`（安装/更新时自动备份）

## 注意事项

- **零依赖**：只用 Python 标准库，不引入任何 pip 包
- **Skill name 校验**：`_validate_skill_name()` 白名单 `[a-zA-Z0-9._@+\-()]+`，路由层会先 URL decode
- **CSRF 防护**：POST/DELETE/PATCH 校验 Origin/Referer
- **GitHub API 限流**：未认证 60 次/小时，有 5 分钟 TTL 缓存 + 熔断
- **诊断子进程**：`_diag_worker.py` 通过 `sys.argv[1]` 接收目标路径（不拼接代码字符串）
- **前端数据保护**：`scan.totals.skills` 只取 fast-scan 值，不被过期缓存覆盖
- **路径安全**：所有文件操作用 `is_relative_to()` 验证，不用 `startswith()`
- **Symlink 安全**：垃圾站移动 symlink 时只移动链接入口本身，不追随链接目标；broken symlink 可清理
- **前端 JS 调试**：模板字符串嵌套 HTML 属性时注意引号冲突，优先抽全局函数而非内联 onclick

## 下一步方向

**"问题与整理"页的分类展示与扫描规则优化**：
- 当前分类 tab（5 类：user/marketplace/cache/cross-copy/project）的展示逻辑和切换体验
- 二哥扫描的规则调优（同名检测、上游比对策略、内容变更证据）
- 分类标签与扫描结果的联动展示
- 问题页的删除操作与全部目录技能页的分类删除联动

**竞品调研与差异化**：
- skillslm、cc-switch 等同类工具的方法论对比
- 数据存储策略（纯文件 vs 数据库 vs 混合）
