# API Reference

> 从 CLAUDE.md 提取的完整 API 表。CLAUDE.md 只保留规则和踩坑警示。

## 关键 API

| 端点 | 方法 | 作用 |
|---|---|---|
| `/api/fast-scan` | GET | 列出当前目标库的 skills |
| `/api/targets` | GET | 列出所有发现的 skill 目录（按 Agent 分组）；后端 3 分钟缓存，前端 `fetchTargets()` 另有 3 分钟内存缓存 |
| `/api/scan-run` | POST | 二哥扫描：用户选目录 + 分析类型，返回分析结果；默认 checks 不含 upstream（upstream 烧 GitHub API，用户主动勾才查）；返回 `upstream_api_estimate`（将查 upstream 的 skill 总数，仅当 `checks` 含 upstream 时非零）和 `github_rate_limit`（限流轮廓）；`source_status`（每技能来源 `{name, dir, source, repo}`，跟随选定范围，只收 `category=user/project`，喂「待补来源」tab，0 API）|
| `/api/scan-result` | GET | 读取缓存的扫描结果 |
| `/api/upstream-cache/clear` | POST | 清空 upstream hash 缓存（内存 + 盘 `.data/state/upstream-hash-cache.json`），强制下次「开始上游检测」走真实 GitHub API（不受 24h 短路）；前端「上游检测」页「🗑 清缓存」按钮 |
| `/api/global-stats` | GET | 当前可用来源的分类分布（active-only，排除市场/缓存/已安装未启用；5 分钟缓存） |
| `/api/history` | GET | 操作历史（`?limit=N` 默认50最大500、`?hide=op1,op2` 过滤；前端默认 `limit=200&hide=switch_target` 隐藏切目录噪音） |
| `/api/target` | POST | 切换当前目标库 |
| `/api/source/skills` | GET | 读取来源 skill/command 列表；默认不生成 understanding，加 `?understanding=1` 才计算 |
| `/api/installed-plugins` | GET | 返回本机 Claude 插件状态（已启用 / 已安装 / 市场列表）|
| `/api/mcp-inventory` | GET | 跨 Agent MCP server 清单（Claude `.claude.json` / Codex `config.toml` / Cursor `mcp.json` / 项目级 `.mcp.json`，只读 name/transport/disabled）|
| `/api/source-aggregations` | GET | 按 owner/repo 聚合有来源的 skill + `unknown_skills` 列表（喂「按来源」视图）；复用 `detect_source_local` 三信号，0 GitHub API；3 分钟缓存，attach 后失效。unknown 只收 active-root 非软链 |
| `/api/custom-sources` | GET/POST/DELETE | 管理自定义来源 |
| `/api/steal` | POST | 从 GitHub URL 装 skill（合集仓库多候选返回 `multi`+candidates，前端弹勾选框批量装；`install_skill` clone 一次复用；支持 blob/tree/根 URL）|
| `/api/steal-npx` | POST | 走 `npx -y skills add` 装（探测返回 candidates；装带 names 批量；package 白名单 + subprocess 列表参数防注入；装 mode 总是 `-g` 用户级，`-a` 映射当前 target agent）|
| `/api/copy-skill` | POST | 复制 skill 到当前目标库 |
| `/api/code-search` | POST | 按内容召回 GitHub 候选仓库 + 可选 hash 确认（body `{snippets?, query?, skill_dir?, confirm?}`；无 GITHUB_TOKEN 降级返回 error；多片段召回，/search/code 限 10 次/分）|
| `/api/attach-source` | POST | 给 unknown skill 补来源写 `.skill-source.env`（body `{skill_dir, repo, subdir?, ref?, url?}`；复用 write_source_metadata，写完清 upstream 短路缓存 + patch scan-result.json）|
| `/api/search-source` | POST | 来源恢复主路线：按 skill 名字搜 GitHub 仓库，优先 `user:<login>` 自己仓库（通用名也命中，如 stay-awake→stay-awake-skill）。body `{name}`；返回 `{candidates:[{repo,description,stars,url,is_own}], login}` |
| `/api/probe-source` | POST | 借用 install_skill 解析层（`list_repo_skills`）：给仓库 URL → clone → 列 skills + hash 比对本地，确认来源（不安装，不依赖 search 索引，新仓库也行）。body `{url, skill_dir?}`；返回 `{ok, repo, skills:[{name,subdir,hash,match}], local_hash}` |
| `/api/skill/{name}` | DELETE | 删除 skill（`?target=<dir>` 指定目录、`?reason=broken\|same-name\|identical` 记删除原因喂治理统计；移入垃圾站可恢复） |
| `/api/skill/{name}/content` | GET | 读取 SKILL.md 原始内容 |
| `/api/skill/{name}/export` | GET | **导出单个 skill 为 zip**（整个目录结构，排除 `.snapshots`/`.trash`；返回 `application/zip`） |
| `/api/skill/{name}/upstream` | GET | 检查上游版本状态 |
| `/api/skill/{name}/rehash` | POST | 重新计算内容 hash |
| `/api/skill/{name}/update` | PATCH | 从上游更新 skill |
| `/api/skill/{name}/fix` | PATCH | 修复 skill 结构问题 |
| `/api/skill/export-batch` | POST | **批量导出** body `{names:[...]}` → zip 包（多个 skill 目录合并打包） |
| `/api/install/import` | POST | **导入 zip** body `{data:"<base64>"}` → 解压到当前 target，识别含 SKILL.md 的子目录自动安装 |
| `/api/preview` | GET | 跨目录预览 skill 内容（?dir=xxx&name=xxx） |
| `/api/understand` | GET | 单 skill 规则理解（?name=，可选 ?dir=） |
| `/api/search-skills` | GET | 关键词搜索来源 skills |
| `/api/cleanup-plan` | GET | 生成目录治理计划（dry-run） |
| `/api/cleanup-execution-plan` | GET | 生成可执行形态的清理预案（仍是 dry-run） |
| `/api/cleanup-execute` | POST | 将选中的清理候选移入项目垃圾站 |
| `/api/trash` | GET/DELETE | 列出垃圾站 / 清空 |
| `/api/trash/stats` | GET | 累计删除/清空统计（读全量 history.jsonl 聚合，不受 /api/history 50 条限制） |
| `/api/operation-stats` | GET | 操作统计（`{totals,recent,since}`，读全量 history.jsonl 聚合 op 计数；`recent` 近 7 天） |
| `/api/governance-stats` | GET | 仪表盘「治理成果」聚合：`cleanup_total`+`cleanup_by_reason`（skill 删除，按 `move_to_trash.detail.reason` 分桶，**不含 broken**）+`broken_total`（断链单独，不混 skill 删除）+`update/install/copy/attach_total`（count 累加）+`scan_total`（健康检测次数）。reason 是 2026-07 埋点，历史无 reason 归 `uncategorized`（显示「历史未分类」） |
| `/api/trash/{id}` | DELETE | 永久删除 |
| `/api/trash/{id}/restore` | POST | 恢复到原路径或当前目录（GET 写路由已移除，避免绕过 CSRF） |
| `/api/openapi` | GET | 返回路由清单（调试用） |
