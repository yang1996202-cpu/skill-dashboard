# 📊 Skill Dashboard

可视化管理本地技能库（Skills）的轻量 WebUI。零前端依赖，纯 Python 标准库。

## 功能

### Layer 0 — 自主能力（毫秒级）
- 📦 **列出 Skills** — 即时扫描任意技能库目录
- 🔄 **切换目标库** — 支持 Claude Code / Codex / Agents / Alice / CC-Switch 等 10+ 个技能库
- 🏷️ **自动分类** — JS 关键词引擎，14 个分类（代码开发、内容创作、图片生成…）
- 📖 **查看内容** — 点击查看 SKILL.md 全文

### Layer 1 — 按需诊断（需 skill-mgr，可选）
- 🏥 **健康评分** — Python 自主计算，不依赖 bash
- ⚠️ **结构问题** — broken symlink、缺 frontmatter、oversized 检测
- 🔍 **相似度检测** — 语义重叠分析（通过 skill-mgr）
- 🔗 **上游追踪** — GitHub 仓库版本状态（通过 skill-mgr）
- 💾 **每目标缓存** — 诊断结果缓存到本地，切回直接用

### Layer 2 — 写操作
- ➕ **安装 Skill** — 输入 GitHub URL，一键安装（steal）
- 🗑️ **删除 Skill**
- 🔄 **更新上游**

## 快速开始

```bash
# 无需安装任何依赖
cd skill-dashboard
python3 serve.py
```

浏览器自动打开 `http://localhost:3457`。

## 不装 skill-mgr 能用吗？

**能。** 基础功能（列出、分类、切换、查看、结构检查、健康评分）全部 Python 自主完成。

装了 [skill-mgr](https://github.com/yang1996202-cpu/local-skill-manager) 后解锁深度诊断：语义相似度、上游追踪、来源库索引。

## 架构

```
用户操作 → fast-scan (5-10ms) → 页面立刻渲染
                ↓
          Python quick-check (~10ms) → 健康分 + 结构问题
                ↓
    点「一键诊断」→ skill-mgr scan (7s) + check (40s) → 完整数据
```

## 技术栈

- **后端**：Python 3 标准库（`http.server`），零依赖
- **前端**：单文件 HTML + CSS + JS，无框架
- **数据源**：直接读文件系统 + 可选 skill-mgr bash 脚本

## License

MIT
