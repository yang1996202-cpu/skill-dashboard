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
skilldash/overlap.py        — 跨目录同名、完全重复扫描
skilldash/cleanup.py        — 清理计划、执行预案、重复 skill 处理准则
skilldash/content_hash.py   — SKILL.md 内容 hash 追踪
skilldash/decisions.py      — 本地运行态决策（多端部署）
skilldash/understanding.py  — 离线理解层
_diag_worker.py    — 诊断子进程（由 serve.py 通过 subprocess 调用）
index.html         — 前端 HTML 骨架（~150 行）
static/skill-dashboard.css — 前端样式
static/app-core.js         — 前端状态、数据加载、仪表盘、当前目录技能
static/issues-cleanup.js   — 问题与整理、清理计划、垃圾站
static/sources.js          — 能力来源、来源浏览、批量同步/删除
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
       serve.py (ThreadingHTTPServer, 端口 3457)
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
| `/api/fast-scan` | GET | 列出当前目标库的 skills |
| `/api/targets` | GET | 列出所有发现的 skill 目录（按 Agent 分组）；后端 3 分钟缓存，前端 `fetchTargets()` 另有 3 分钟内存缓存 |
| `/api/scan-run` | POST | **二哥扫描**：用户选目录 + 分析类型，返回分析结果 |
| `/api/scan-result` | GET | 读取缓存的扫描结果 |
| `/api/global-stats` | GET | 全域分类分布统计（5 分钟缓存） |
| `/api/global-overlap` | GET | 跨目录同名重复分析（遗留接口，前端不再自动调用） |
| `/api/quick-check` | GET | 健康评分 + 结构问题（遗留接口，前端不再自动调用） |
| `/api/diagnose` | POST | 触发完整诊断（旧流程，保留兼容） |
| `/api/diagnosis-status` | GET | 轮询诊断进度 |
| `/api/scan` | GET | 返回最近一次完整扫描结果 |
| `/api/health` | GET | 返回最近一次健康检查结果 |
| `/api/history` | GET | 操作历史记录 |
| `/api/target` | POST | 切换当前目标库 |
| `/api/export` | GET | 导出 skill 清单 JSON |
| `/api/source/skills` | GET | 读取来源 skill/command 列表；默认不生成 understanding，加 `?understanding=1` 才计算 |
| `/api/installed-plugins` | GET | 返回本机 Claude 插件状态（已启用 / 已安装 / 市场列表）|
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

### Host Inspectors：文件库存 ≠ 运行时暴露

通用扫描器只回答“哪里有 `SKILL.md`”。这不足以解释 Codex/Claude/WorkBuddy/CodeBuddy 这类宿主，因为插件缓存、marketplace 目录、App 内置包和 connector 包里也可能带 skills，但不一定进入当前上下文。

宿主专属解释放在 `skilldash/host_inspectors.py`。它输出统一 runtime metadata，再由 `discovery.py` 合并到 target governance：

- `enabled`：宿主配置明确启用的插件包，例如 Codex `~/.codex/config.toml` 里的 `[plugins."..."] enabled=true`
- `connector`：Codex app/connector 包或工具缓存显示曾暴露运行时工具
- `user-root`：宿主用户技能根，例如 `~/.workbuddy/skills`
- `builtin`：宿主 App 自带技能，例如 WorkBuddy.app 的 `resources/builtin-skills`
- `catalog`：宿主 marketplace/connector-marketplace 货架目录，只解释来源，不等于上下文加载
- `cache`：只有本地插件缓存存在，没有启用证据
- `stale`：同名插件在别处启用，此目录只是非当前副本

原则：不要把所有 Agent 的私有逻辑塞进泛化扫描器；每个宿主用 adapter/inspector 把私有配置转成统一字段。

### Host Profile：通用扫描与 Agent 范儿的结合层

扫描管线分三层：

1. **Generic discovery**：`discovery.py` 高召回找 `SKILL.md`、commands 目录和已知 app builtin skill roots；它不判断“是否加载进上下文”。
2. **Host profile**：`discover_host_profiles()` 给每个宿主生成非敏感轮廓，包括 source roots、profile family、MCP 配置数量、runtime/catalog MCP server 数。MCP 只保留 server 名、transport、disabled 标记和计数；不返回 URL、headers、env、command args。
3. **Host inspector**：`plugin_context_for_dir()` 把目录解释成统一 runtime metadata。Claude/Codex 保留独立逻辑；WorkBuddy 和 CodeBuddy 走同一个 `buddy-family` inspector，因为二者共享 `skills`、`skills-marketplace`、`plugins/marketplaces`、`connectors`、`connectors-marketplace`、`mcp.json` 目录范式。

`/api/targets` 会把 compact `profile_summary` 挂到每个 Agent group 上；`/api/host-profiles` 返回完整非敏感 profile。新增 Agent 时，先看 generic profile 是否已发现 source roots/MCP，再决定是否补专属 inspector。

### 扫描 API：用户选范围 + 选类型

`POST /api/scan-run` 接受 `{directories, scope, checks}` 参数。`checks` 为 `['same-name', 'upstream', 'content-changes']` 的子集，只跑用户勾选的分析类型；`scope` 控制目录范围（`daily` 在 UI 上叫“重点扫描”，使用 `sourceIsDaily()` 的重点整理目标；`deep` 在 UI 上叫“全量扫描”，含全部目录）。

辅助函数 `_find_same_name_duplicates(dirs)` 从 `detect_cross_dir_overlaps()` 提取出来，接受 Path 列表参数，可复用。

### 清理执行准则：hash 一致不是直接删除依据

`/api/cleanup-execution-plan` 只生成预案，不直接改文件。推荐移入垃圾站的候选限定在：

- 备份、快照、导入副本、下载包、App 本地库等复核层目录
- `SKILL.md` 内容 hash 完全一致
- 保留副本仍存在，且执行前 hash 没有变化

其他 Agent 根目录里的完全重复 skill 不进垃圾站候选，归入 `deploy` 阶段，表示“多端部署副本”。用户点击“标记多端部署”后，写入 `.data/state/duplicate-decisions.json`，按 `skill_name + content_hash` 隐藏同一提醒；如果内容变化，hash 变化，提醒会重新出现。前端“本地决策”入口用于查看和撤销这些本机运行状态，帮助开源用户理解哪些信息不会随 Git 提交。

### 前端数据流

- `loadData()` 只跑轻量 API（fast-scan、targets、global-stats）
- `/api/targets` 通过 `fetchTargets(force)` 读取，前端带 3 分钟 TTL 内存缓存，避免重复请求
- `render()` 改为视图感知：只有在「能力来源」页激活时才渲染 sources 列表，其他视图只更新 sidebar/badge/stats
- `updateTargetSelector(force, scope)` 按 `dropdown/sidebar/full` 控制渲染范围，避免切换目录或视图时级联重渲染
- 目录切换后只做乐观 `is_current` 更新 + 本地缓存同步，不再强制刷新 `/api/targets`
- 扫描结果通过 `runScan()` 调 `/api/scan-run`，映射到 `health` 和 `globalOverlap` 变量
- `renderIssues()` 复用现有的卡片渲染逻辑
- `loadCachedScanResult()` 在页面加载时检查缓存

### 目录视图抽象层

目录视图抽象定义在 `static/issues-cleanup.js`，但职责已拆分：

- `filterGroupsByView(groups, viewMode)`：按 `active`/`inventory`/`review`/`all` 过滤 Agent 分组，重新计算 `total_skills`，过滤空分组
- `sortGroupsByCurrentAndSize(groups)`：current 组优先，再按 skill 数量降序
- `sourceIsDaily(t)`：仍用于「问题与整理」的重点扫描范围，保留 `user`/`project` 两类 + `is_current` 的当前目录，避免来源页筛选影响扫描。
- `sourceIsActive(t)` / `sourceIsInventory(t)` / `sourceIsReview(t)`：决定目录进入「当前可用」「来源库存」「待复核」哪个视图；依据 `sourceCapabilityBucket(t)` 而不是原始路径分类。
- `fetchTargets(force)` 为 `/api/targets` 提供前端 3 分钟 TTL 缓存
- 单一 `_sourceViewMode` 状态通过 `sd-source-view` 持久化，可取 `active`/`inventory`/`review`/`all`；旧值 `mine`/`source-market`/`deep` 会在启动时迁移为 `active`/`inventory`/`all`
- **sidebar「目录技能切换」下拉始终显示全部目录**，视图过滤只在「能力来源」页生效

### 能力来源页 UX

- **视图切换在页面顶部**：分段控件「当前可用 / 来源库存 / 待复核 / 全部」放在「能力来源」页头部，不再放在 sidebar
- **统一分段控件**：排序（默认排序 / 按 skills / 按目录）和视图切换（当前可用 / 来源库存 / 待复核 / 全部）使用 `.segmented-control` 组件
- **两排头部**：第一排标题 + 统计 + 添加来源；第二排当前目录 + 排序 + 视图切换
- **运行态折叠**：每个 Agent 卡片内，按能力桶（用户自建、系统内置、已启用插件、连接器包、命令、已安装未启用、市场目录、仅缓存、导入/副本、项目级、未知）分组显示，每个标题可点击展开/收起
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

前端 `fetchTargets(force)` 同样有 3 分钟内存缓存。复制、安装、删除、修复、更新、添加/移除来源等变更目录内容的操作成功后，必须调用 `invalidateTargetsCache()` 使缓存失效，再 `loadData()`，否则「能力来源」页会显示旧目录统计。

前端 `loadData()` 的异步 targets 回调：如果 sources DOM 已有内容（用户已展开过），跳过 `renderSources()` 只更新 badge 数字，避免覆盖用户交互状态。

## 数据目录

- 状态与缓存：`.data/`（state/ 存 current-target.json/latest-scan.json，cache/ 存诊断结果和全域分类）
- 完全重复处理决策：`.data/state/duplicate-decisions.json`（本地运行态，不提交）
- Skill 快照：`<target>/.snapshots/`（安装/更新时自动备份）

## 注意事项

- **零依赖**：只用 Python 标准库，不引入任何 pip 包
- **HTTP 并发**：`serve.py` 使用 `ThreadingHTTPServer`，浏览器并发请求不再互相阻塞
- **穿透浏览性能**：`/api/source/skills` 默认不计算 understanding，避免大目录穿透时超时；需要理解内容时由 skill 详情页单独加载
- **Skill name 校验**：`_validate_skill_name()` 白名单 `[a-zA-Z0-9._@+\-()]+`，路由层会先 URL decode
- **CSRF 防护**：POST/DELETE/PATCH 校验 Origin/Referer
- **GitHub API 限流**：未认证 60 次/小时；可通过 `GITHUB_TOKEN` 环境变量或项目根目录 `.env` 文件配置 token，额度提升至 5000 次/小时。`.env` 已加入 `.gitignore`，不随仓库提交
- **诊断子进程**：`_diag_worker.py` 通过 `sys.argv[1]` 接收目标路径（不拼接代码字符串）
- **前端数据保护**：`scan.totals.skills` 只取 fast-scan 值，不被过期缓存覆盖
- **路径安全**：所有文件操作用 `is_relative_to()` 验证，不用 `startswith()`
- **Symlink 安全**：垃圾站移动 symlink 时只移动链接入口本身，不追随链接目标；broken symlink 可清理
- **前端 JS 调试**：模板字符串嵌套 HTML 属性时注意引号冲突，优先抽全局函数而非内联 onclick

## 下一步方向

**"问题与整理"页的扫描规则与展示优化**：
- 当前 `checks` 控制已上线，后续可按检查项分别渲染卡片、避免空状态
- 二哥扫描的规则调优（同名检测、上游比对策略、内容变更证据）
- 分类标签与扫描结果的联动展示
- 问题页的删除操作与能力来源页的分类删除联动

**Buddy family 内置 Commands**：
- 不要把 `~/.codebuddy/plugins/marketplaces/**/commands` 或 `~/.workbuddy/plugins/marketplaces/**/commands` 当成截图里的内置 Commands；那是市场/插件货架，数量大且不等于当前 UI 命令。
- 截图里的 CodeBuddy `/init`、`/cr`、`/tests`、`/explain`、`/fix` 更像 IDE 运行时注册的内置行为；本机没有发现 `~/.codebuddy/commands` 或解包 resources 下的 `commands/*.md` 路径。后续若要展示它们，应做专门的 runtime command inspector，而不是扩 `_discover_command_dirs()`。

**竞品调研与差异化**：
- skillslm、cc-switch 等同类工具的方法论对比
- 数据存储策略（纯文件 vs 数据库 vs 混合）
