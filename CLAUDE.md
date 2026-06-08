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
serve.py           — 后端：HTTP handler + 业务逻辑（~1700 行）
_diag_worker.py    — 诊断子进程（由 serve.py 通过 subprocess 调用）
index.html         — 前端：HTML + CSS + JS 单文件（~1450 行）
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

**数据分层加载**：
1. `/api/fast-scan` — 毫秒级，扫描当前目标目录的 SKILL.md
2. `/api/quick-check` — ~10ms，健康评分 + 结构问题
3. `/api/diagnose` — 5-15s，完整诊断（上游版本 + 相似度）

## 关键 API

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/fast-scan` | GET | 列出当前目标库的 skills |
| `/api/quick-check` | GET | 健康评分 + 结构问题 |
| `/api/scan` | GET | 返回最近一次完整扫描结果 |
| `/api/health` | GET | 返回最近一次健康检查结果 |
| `/api/history` | GET | 操作历史记录 |
| `/api/targets` | GET | 列出所有可用的技能库目录 |
| `/api/target` | POST | 切换当前目标库 |
| `/api/global-stats` | GET | 全域分类分布统计 |
| `/api/export` | GET | 导出 skill 清单 JSON |
| `/api/source/skills` | GET | 读取来源 skill 列表 |
| `/api/custom-sources` | GET | 获取自定义来源列表 |
| `/api/custom-sources` | POST | 添加自定义来源 |
| `/api/custom-sources` | PATCH | 更新自定义来源 |
| `/api/custom-sources` | DELETE | 删除自定义来源 |
| `/api/steal` | POST | 从 GitHub URL 安装 skill |
| `/api/copy-skill` | POST | 复制 skill 到当前目标库 |
| `/api/diagnose` | POST | 触发完整诊断 |
| `/api/diagnosis-status` | GET | 轮询诊断进度 |
| `/api/skill/{name}` | GET | 获取 skill 详情 |
| `/api/skill/{name}` | DELETE | 删除 skill |
| `/api/skill/{name}/content` | GET | 读取 SKILL.md 原始内容 |
| `/api/skill/{name}/upstream` | GET | 检查上游版本状态 |
| `/api/skill/{name}/update` | PATCH | 从上游更新 skill |
| `/api/skill/{name}/fix` | PATCH | 修复 skill 结构问题 |
| `/api/openapi` | GET | 返回路由清单（调试用） |

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
