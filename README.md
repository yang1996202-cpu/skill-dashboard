# 📊 Skill Dashboard

可视化管理本地技能库（Skills）的轻量 WebUI。**零前端依赖，纯 Python 标准库。**

![Python](https://img.shields.io/badge/python-3.8+-blue)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen)
![License](https://img.shields.io/badge/license-MIT-green)

---

## 截图

> 运行 `python3 serve.py` 后，浏览器自动打开 `http://localhost:3457`。

### 仪表盘

![仪表盘](./screenshots/dashboard.png)

> 上图替换方式：运行项目后按 `Cmd+Shift+4` 截图保存到 `screenshots/dashboard.png`

### 技能库来源浏览

![技能库](./screenshots/sources.png)

### 上游追踪

![上游追踪](./screenshots/upstream.png)

### 问题与整理

![问题与整理](./screenshots/issues.png)

---

## 功能

**完全独立，零外部依赖。** 不装任何额外工具，一个 Python 入口跑起来。

| 功能 | 说明 |
|---|---|
| 📦 **列出 Skills** | 即时扫描任意技能库目录，毫秒级 |
| 🧠 **理解层** | 离线规则解析 SKILL.md，生成中文用途、场景、能力标签、风险提示和证据片段 |
| 🧭 **清理计划** | dry-run 生成目录治理方案：保护区、复核区、观察区、隐藏区，先给证据不直接删除 |
| 🧩 **推荐清理** | 将目录治理和完全重复 skill 转成可恢复的垃圾站候选；推荐候选可一键移入垃圾站 |
| 🔁 **多端部署识别** | 同一 skill 同内容出现在多个 Agent 根目录时默认保留，可标记为已知部署副本，并可在“本地决策”里撤销 |
| 🔄 **切换目标库** | 支持 Claude Code / Codex / Agents / Alice / CC-Switch / Hermes / WorkBuddy / CodeBuddy 等 10+ 个技能库 |
| 📚 **技能库来源浏览** | 扫描 150+ 个来源库，支持穿透查看、批量同步到目标库 |
| ⌨️ **Commands 浏览** | 识别 Claude commands 目录，和 skills 分层展示 |
| 🧩 **插件状态** | 展示 Claude 已启用插件、已安装未启用插件和市场目录差异 |
| 🏷️ **自动分类** | JS 关键词引擎，14 个分类 + 支持 frontmatter `category` 覆盖 |
| 📖 **查看内容** | 点击 skill 名称查看 SKILL.md 全文 |
| 🏥 **健康评分** | Python 自主计算，不依赖 bash |
| ⚠️ **结构问题** | broken symlink、缺 frontmatter、oversized 检测 |
| 🔗 **上游追踪** | 自动检测 `.git` 来源 + `.skill-source.env` 安装记录 |
| 🔄 **上游更新检测** | urllib 调 GitHub API，对比 installed vs latest commit |
| ⬇️ **安装 Skill** | 粘贴 GitHub URL → Python 自动 git clone + 子目录选择 + 快照备份 |
| ⬆️ **更新 Skill** | 一键从上游重新安装，自动快照 |
| 💾 **清理候选** | 基于规则自动推荐无用/低质量 skill |
| 📤 **导入/导出** | 批量导入 GitHub URL，导出 Markdown 格式清单 |
| 📜 **操作日志** | 记录切换、删除、清空垃圾站、安装、更新等本地操作 |

---

## 安装

```bash
# 克隆仓库
git clone https://github.com/yang1996202-cpu/skill-dashboard.git
cd skill-dashboard

# 启动（零依赖，无需 npm/pip install）
python3 serve.py
```

浏览器自动打开 `http://localhost:3457`。

---

## 架构

```
页面加载 → fast-scan + targets + global-stats → 先看到当前技能库和目录地图
                ↓
          understanding cache → 中文用途 + 场景/能力/风险标签
                ↓
          点「开始整理」→ cleanup-execution-plan → 推荐移入垃圾站 / 复核 / 多端部署
                ↓
          展开高级线索 → scan-run → 同名、上游和内容变更证据
```

**设计原则**：
- Layer 0（自主）：列出、分类、切换、查看、结构检查、健康评分、上游追踪、同名/完全重复线索、清理候选、安装、更新
- 理解层默认离线可用，不要求 API key；未来可接可选 AI 增强，但 UI 只依赖统一理解 schema
- 清理计划默认 dry-run，目录级动作先解释来源、状态、去向和证据，不做直接删除
- 推荐清理只允许候选移入垃圾站，可恢复；不会直接永久删除，不做当前目录级删除
- 完全重复 skill 只有在 `SKILL.md` 内容一致且保留副本明确时才可能进入候选；备份、导入、下载、本地库副本可移入垃圾站，其他 Agent 根目录副本按多端部署默认保留
- 多端部署标记记录在本地状态里，按 `skill + content hash` 生效；内容变更后会重新出现，避免长期误藏；“本地决策”入口可查看和撤销这些运行状态
- “标记多端部署”属于本机运行状态，记录在 `.data/state/`，用于减少重复提醒，不随 Git 提交
- 所有写操作（安装、删除、更新）都有自动快照备份
- Broken symlink 和目录壳里的 broken `SKILL.md` 会作为可清理残留展示，可移入项目垃圾站

---

## 技术栈

- **后端**：Python 3 标准库（`serve.py` + `skilldash/` 轻量模块），零依赖
- **前端**：HTML + CSS + 多个 classic JS 静态文件，无框架、无构建步骤
- **数据源**：直接读文件系统 + GitHub REST API

后端模块边界：

- `serve.py`：HTTP 路由和请求/响应编排
- `skilldash/discovery.py`：目录发现、Agent 推断、目录治理分层
- `skilldash/cleanup.py`：清理计划和可执行 dry-run 预案
- `skilldash/overlap.py`：跨目录同名和完全重复扫描
- `skilldash/decisions.py` / `skilldash/content_hash.py`：本地运行态决策和内容 hash 追踪

前端模块边界：

- `index.html`：页面骨架和静态挂载点
- `static/skill-dashboard.css`：样式
- `static/app-core.js`：状态、数据加载、仪表盘、当前目录技能
- `static/issues-cleanup.js`：问题与整理、清理计划、垃圾站
- `static/sources.js`：全部目录技能、来源浏览、批量同步/删除
- `static/skill-detail.js`：详情、对比、分类编辑
- `static/app-bootstrap.js`：刷新、目标切换、诊断、安装入口、启动加载

---

## 上游追踪说明

上游追踪通过两种方式检测：

1. **`.git` 目录**：读取 `git remote get-url origin`
2. **`.skill-source.env`**：读取来源记录文件（Dashboard 安装时自动写入）

更新检测使用 GitHub REST API（`repos/{owner}/{repo}/commits`），无需 `gh` CLI，无需 token。

如果某个 skill 既没有 `.git` 也没有 `.skill-source.env`，则检测不到上游。这不是 bug，是本地没有来源记录。

---

## License

MIT
