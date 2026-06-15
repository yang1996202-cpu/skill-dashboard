# Skill Dashboard — 项目约定

## GBrain / GStack

- 这个项目优先使用 GBrain MCP/HTTP 能力查询项目记忆；不要把本地 `gbrain search` CLI 当首选入口。
- 如果 MCP/HTTP 不可用，才使用 CLI fallback；若 CLI 出现 PGLite/WASM 初始化失败或命令不在 PATH，记录失败原因后继续用仓库证据推进，不要反复重试同一条 CLI。
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
serve.py           — 后端入口：HTTP handler + 路由编排（~2400 行）
skilldash/paths.py          — 共享路径、端口、缓存文件定位
skilldash/classification.py — skill 分类关键词和描述读取
skilldash/discovery.py      — skill 目录发现、Agent 推断、目录治理分层
skilldash/overlap.py        — 跨目录同名、完全重复、轻量相似扫描
skilldash/cleanup.py        — 清理计划、执行预案、重复 skill 处理准则
skilldash/content_hash.py   — SKILL.md 内容 hash 追踪
skilldash/decisions.py      — 本地运行态决策（多端部署/标记不相似）
skilldash/similarity.py     — signature 相似度 + TF-IDF 深度审计能力
skilldash/understanding.py  — 离线理解层
_diag_worker.py    — 诊断子进程（由 serve.py 通过 subprocess 调用）
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
       skilldash/{discovery,cleanup,overlap,similarity,understanding,...}
          ↓ 读文件系统
       本地 skill 目录 (如 ~/.codex/skills/)
          ↓ GitHub REST API (无认证)
       上游版本检测
```

**数据分层加载（二哥扫描模式）**：
1. **页面加载（秒开）**：`/api/fast-scan` + `/api/targets` + `/api/global-stats`
2. **用户手动扫描**：`/api/scan-run`（选目录 + 选分析类型）
3. **缓存读取**：`/api/scan-result`（上次扫描结果）

分析功能（同名/相似/上游）不自动触发，由用户在"问题与整理"页面的"二哥扫描"面板手动选择。

## 关键 API

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/fast-scan` | GET | 列出当前目标库的 skills |
| `/api/targets` | GET | 列出所有发现的 skill 目录（按 Agent 分组）；后端 3 分钟缓存，前端 `fetchTargets()` 另有 3 分钟内存缓存 |
| `/api/scan-run` | POST | **二哥扫描**：用户选目录 + 分析类型，返回分析结果 |
| `/api/scan-result` | GET | 读取缓存的扫描结果 |
| `/api/global-stats` | GET | 全域分类分布统计（5 分钟缓存） |
| `/api/global-overlap` | GET | 跨目录同名 + 相似分析（遗留接口，前端不再自动调用） |
| `/api/quick-check` | GET | 健康评分 + 结构问题（遗留接口，前端不再自动调用） |
| `/api/diagnose` | POST | 触发完整诊断（旧流程，保留兼容） |
| `/api/diagnosis-status` | GET | 轮询诊断进度 |
| `/api/scan` | GET | 返回最近一次完整扫描结果 |
| `/api/health` | GET | 返回最近一次健康检查结果 |
| `/api/history` | GET | 操作历史记录 |
| `/api/target` | POST | 切换当前目标库 |
| `/api/export` | GET | 导出 skill 清单 JSON |
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
| `/api/similar-decision` | POST/DELETE | 记录或撤销“这组不是相似 skill”的本地运行决策 |
| `/api/batch-delete` | POST | 批量删除 skills（body: `{items: [{target, name}]}`） |
| `/api/openapi` | GET | 返回路由清单（调试用） |

## 设计决策

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

`POST /api/scan-run` 接受 `{directories, checks}` 参数。只跑用户请求的分析类型（同名/相似/上游/内容变更）在用户选择的目录上。

辅助函数 `_find_same_name_duplicates(dirs)` 和 `_find_agent_cross_dir_similar(dirs)` 从 `detect_cross_dir_overlaps()` 提取出来，接受 Path 列表参数，可复用。

### 清理执行准则：hash 一致不是直接删除依据

`/api/cleanup-execution-plan` 只生成预案，不直接改文件。推荐移入垃圾站的候选限定在：

- 备份、快照、导入副本、下载包、App 本地库等复核层目录
- `SKILL.md` 内容 hash 完全一致
- 保留副本仍存在，且执行前 hash 没有变化

其他 Agent 根目录里的完全重复 skill 不进垃圾站候选，归入 `deploy` 阶段，表示“多端部署副本”。用户点击“标记多端部署”后，写入 `.data/state/duplicate-decisions.json`，按 `skill_name + content_hash` 隐藏同一提醒；如果内容变化，hash 变化，提醒会重新出现。前端“本地决策”入口用于查看和撤销这些本机运行状态，帮助开源用户理解哪些信息不会随 Git 提交。

默认相似度使用轻量 signature：`name + description + keywords + headings` 的关键词集合 Jaccard，阈值 0.30，强调可解释性和“为什么像”。TF-IDF 全文相似函数保留为深度审计能力，但不作为默认问题页口径。

相似度只用于人工复核和合并判断，不参与自动清理。前端相似卡片不提供删除入口，只提供查看、并排对比、标记不相似。“标记不相似”写入 `.data/state/similar-decisions.json`，属于本机运行状态，不提交 Git。

### 前端数据流

- `loadData()` 只跑轻量 API（fast-scan、targets、global-stats）
- `/api/targets` 通过 `fetchTargets(force)` 读取，前端带 3 分钟 TTL 内存缓存，避免重复请求
- `render()` 改为视图感知：只有在「全部目录技能」页激活时才渲染 sources 列表，其他视图只更新 sidebar/badge/stats
- `updateTargetSelector(force, scope)` 按 `dropdown/sidebar/full` 控制渲染范围，避免切换目录或视图时级联重渲染
- 目录切换后只做乐观 `is_current` 更新 + 本地缓存同步，不再强制刷新 `/api/targets`
- 扫描结果通过 `runScan()` 调 `/api/scan-run`，映射到 `health` 和 `globalOverlap` 变量
- `renderIssues()` 复用现有的卡片渲染逻辑
- `loadCachedScanResult()` 在页面加载时检查缓存

### 目录视图抽象层

sidebar 的「目录技能切换」下拉与「全部目录技能」页共用同一套目录视图抽象（定义在 `static/issues-cleanup.js`）：

- `filterGroupsByView(groups, viewMode)`：按 daily/deep 过滤 Agent 分组，重新计算 `total_skills`，过滤空分组
- `sortGroupsByCurrentAndSize(groups)`：current 组优先，再按 skill 数量降序
- `sourceIsDaily(t)` 决定目录是否进入日常视图；当前目录始终可见
- `fetchTargets(force)` 为 `/api/targets` 提供前端 3 分钟 TTL 缓存
- 单一 `_sourceViewMode` 状态通过 `sd-source-view` 持久化，sidebar 与 sources 页切换同步

### 全部目录技能页 UX

- **视图与 sidebar 同步**：日常/全量视图状态和 sidebar「目录技能切换」下拉共用 `_sourceViewMode`，切换一边另一边同步
- **统一分段控件**：排序（默认排序 / 按 skills / 按目录）和视图切换（日常视图 / 全量审计）使用 `.segmented-control` 组件，与 sidebar 下拉风格一致
- **两排头部**：第一排标题 + 统计 + 添加来源；第二排当前目录 + 排序 + 视图切换
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
- 相似度误报处理决策：`.data/state/similar-decisions.json`（本地运行态，不提交）
- Skill 快照：`<target>/.snapshots/`（安装/更新时自动备份）

## 注意事项

- **零依赖**：只用 Python 标准库，不引入任何 pip 包
- **Skill name 校验**：`_validate_skill_name()` 白名单 `[a-zA-Z0-9._@+\-]+`
- **CSRF 防护**：POST/DELETE/PATCH 校验 Origin/Referer
- **GitHub API 限流**：未认证 60 次/小时，有 5 分钟 TTL 缓存 + 熔断
- **诊断子进程**：`_diag_worker.py` 通过 `sys.argv[1]` 接收目标路径（不拼接代码字符串）
- **前端数据保护**：`scan.totals.skills` 只取 fast-scan 值，不被过期缓存覆盖
- **路径安全**：所有文件操作用 `is_relative_to()` 验证，不用 `startswith()`
- **Symlink 安全**：垃圾站移动拒绝 skill 目录 symlink，避免误移动链接本身造成宿主目录状态混乱
- **前端 JS 调试**：模板字符串嵌套 HTML 属性时注意引号冲突，优先抽全局函数而非内联 onclick

## 下一步方向

**"问题与整理"页的分类展示与扫描规则优化**：
- 当前分类 tab（5 类：user/marketplace/cache/cross-copy/project）的展示逻辑和切换体验
- 二哥扫描的规则调优（同名检测、相似度阈值、上游比对策略）
- 分类标签与扫描结果的联动展示
- 问题页的删除操作与全部目录技能页的分类删除联动

**竞品调研与差异化**：
- skillslm、cc-switch 等同类工具的方法论对比
- 数据存储策略（纯文件 vs 数据库 vs 混合）
