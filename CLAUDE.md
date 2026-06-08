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
serve.py           — 后端：HTTP handler + 业务逻辑（~1400 行）
_diag_worker.py    — 诊断子进程（由 serve.py 通过 subprocess 调用）
index.html         — 前端：HTML + CSS + JS 单文件（~1300 行）
_cache/            — 诊断结果缓存（.gitignore）
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
| `/api/targets` | GET | 列出所有可用的技能库目录 |
| `/api/steal` | POST | 从 GitHub URL 安装 skill |
| `/api/skill/{name}` | DELETE | 删除 skill |
| `/api/skill/{name}/update` | PATCH | 从上游更新 skill |
| `/api/diagnose` | POST | 触发完整诊断 |
| `/api/diagnosis-status` | GET | 轮询诊断进度 |

## 数据目录

- 状态文件：`~/.skill-manager/state/`（current-target.json、latest-scan.json）
- 诊断缓存：`.cache/`（按目标路径生成缓存文件名）
- Skill 快照：`<target>/.snapshots/`（安装/更新时自动备份）

## 注意事项

- **零依赖**：只用 Python 标准库，不引入任何 pip 包
- **Skill name 校验**：`_validate_skill_name()` 白名单 `[a-zA-Z0-9._@+\-]+`
- **CSRF 防护**：POST/DELETE/PATCH 校验 Origin/Referer
- **GitHub API 限流**：未认证 60 次/小时，有 5 分钟 TTL 缓存 + 熔断
- **诊断子进程**：`_diag_worker.py` 通过 `sys.argv[1]` 接收目标路径（不拼接代码字符串）
- **前端数据保护**：`scan.totals.skills` 只取 fast-scan 值，不被过期缓存覆盖
