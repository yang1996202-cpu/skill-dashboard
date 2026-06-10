# Skill Dashboard — 项目约定

## 是什么

零依赖本地 WebUI，可视化管理本地 AI skill 文件。Python 标准库 http.server + 单文件 HTML/CSS/JS。

## 运行

```bash
python3 serve.py
# 自动打开 http://localhost:3457
```

端口固定 3457，绑定 127.0.0.1。

## 文件结构

```
serve.py           — 后端：HTTP handler + 业务逻辑（~2500 行）
_diag_worker.py    — 诊断子进程（由 serve.py 通过 subprocess 调用）
index.html         — 前端：HTML + CSS + JS 单文件（~2100 行）
.data/             — 运行时状态与缓存（state/、cache/，.gitignore）
docs/              — 项目文档（troubleshooting.md）
README.md
LICENSE
screenshots/       — 截图（当前只有 .gitkeep）
```

## 架构

```
浏览器 → index.html (静态)
          ↓ fetch API
       serve.py (HTTPServer, 端口 3457)
          ↓ 读文件系统
       本地 skill 目录 (如 ~/.claude/skills/)
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
| `/api/targets` | GET | 列出所有发现的 skill 目录（按 Agent 分组） |
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

`_agent_from_path()` 从路径推断 Agent 名称。保留已知映射（`.claude` → `Claude Code`），但未知 `.xxx` 直接用目录名。**不在其他地方重复这个映射**——`_list_targets()` 等调用 `_agent_from_path()` 而不是自己维护 if/elif 列表。

### 扫描 API：用户选范围 + 选类型

`POST /api/scan-run` 接受 `{directories, checks}` 参数。只跑用户请求的分析类型（同名/相似/上游/内容变更）在用户选择的目录上。

辅助函数 `_find_same_name_duplicates(dirs)` 和 `_find_agent_cross_dir_similar(dirs)` 从 `detect_cross_dir_overlaps()` 提取出来，接受 Path 列表参数，可复用。

### 前端数据流

- `loadData()` 只跑轻量 API（fast-scan、targets、global-stats）
- 扫描结果通过 `runScan()` 调 `/api/scan-run`，映射到 `health` 和 `globalOverlap` 变量
- `renderIssues()` 复用现有的卡片渲染逻辑
- `loadCachedScanResult()` 在页面加载时检查缓存

## 数据目录

- 状态与缓存：`.data/`（state/ 存 current-target.json/latest-scan.json，cache/ 存诊断结果和全域分类）
- Skill 快照：`<target>/.snapshots/`（安装/更新时自动备份）

## 注意事项

- **零依赖**：只用 Python 标准库，不引入任何 pip 包
- **Skill name 校验**：`_validate_skill_name()` 白名单 `[a-zA-Z0-9._@+\-]+`
- **CSRF 防护**：POST/DELETE/PATCH 校验 Origin/Referer
- **GitHub API 限流**：未认证 60 次/小时，有 5 分钟 TTL 缓存 + 熔断
- **诊断子进程**：`_diag_worker.py` 通过 `sys.argv[1]` 接收目标路径（不拼接代码字符串）
- **前端数据保护**：`scan.totals.skills` 只取 fast-scan 值，不被过期缓存覆盖
- **路径安全**：所有文件操作用 `is_relative_to()` 验证，不用 `startswith()`
