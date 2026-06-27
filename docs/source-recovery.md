# 来源恢复：给 unknown skill 补上游

> skill-dashboard 的来源识别覆盖率约 35%（46 个 skill 里 16 个能追溯，30 个真 unknown）。
> unknown 的根因是"装的时候没留痕"（手 cp / Agent 自主放 / 旧工具装的）。
> 本文是"给 unknown 补来源"的设计依据，2026-06 研讨沉淀。

## 1. 来源识别现状（3 信号优先级）

`check_upstream_status`（source_ops.py）按优先级串行查，命中第一个就停：

| 优先级 | 信号 | 谁装的 |
|---|---|---|
| 1 | `.skill-source.env`（新）/ `.skill-manager-source.env`（旧） | dashboard / skill-manager steal 装 |
| 2 | `.git/origin` remote | 裸 git clone |
| 3 | `~/.agents/.skill-lock.json` | npx skills add |
| 都没命中 | `source: unknown` | 手 cp / Agent 自主放 |

steal 装的写 meta → 永久可追溯；不留痕的变 unknown。**装的方式决定可追溯性。**

## 2. 补来源分层策略（兜底链）

不是单靠哪一层，是分层兜底：

| 层 | 办法 | 依赖本机线索 | 对开源新用户 |
|---|---|---|---|
| 1 | steal meta / .git origin / npx lock | 是 | 多半空 |
| 2 | 同仓库关联（本机已知仓库扩散） | 是 | 空，无效 |
| 3 | 按内容 GitHub code search | 否 | ✅ 通用层 |
| 4 | 按名字搜 | 否 | 弱（合集仓失效） |
| 5 | 手动填 URL | 否 | 保底 |

第 3 层是缺的通用层——对开源用户 work，不依赖本机留痕。第 2 层（同仓库关联）只对"本机已有同仓库兄弟 skill"的用户有效（如 neat-freak 的 meta 暴露了 KKKKhazix/khazix-skills，同仓的 storage-analyzer 就能关联），**对全新开源用户不通用**。

## 3. 为什么不能"直接 hash 反查"

**因为没有"hash → 仓库"的全局反向索引**（无 skill registry）。

- hash 单向：能从 SKILL.md 算 hash，但不能从 hash 反查"它是哪个仓库"。
- hash 的角色：**在候选集合内精确确认**（裁判），不负责"找候选"。
- 搜索的角色：**从全网召回候选**（提供比对对象）——没搜索，hash 没东西可比。
- 加分：搜索模糊匹配，能容忍本机改过的 skill；hash 精确，改一字就失效。

类比：指纹（hash）查不出"这人是谁"，得先有嫌疑犯名单（搜索召回），再指纹比对（确认）。没有指纹库（registry）就反查不了身份。

**未来**：有 skill registry（hash 数据库）才能直接 hash 反查——现在没有。

## 4. 按内容 code search：可行性与限制（实测）

实测样本 `storage-analyzer`（真实来源是合集仓库 KKKKhazix/khazix-skills）：

| 搜法 | 命中合集仓库？ |
|---|---|
| 按**名字** "storage-analyzer" | ❌ 撞同类（WhiteMinds/disk-space-analyzer-skill 等），命不中 KKKKhazix |
| 按**独有内容话术**（"可自动清理/需人工判断/谨慎清理 全程只读"） | ✅ 命中 KKKKhazix/khazix-skills |
| content hash 比对 | ✅ 256 位零误差锁定 |

**合集仓库按名搜失效的根因**：仓库名是合集名（khazix-skills），≠ skill 名；GitHub 搜索不按子目录名把合集仓库顶出来。按内容（SKILL.md 文件内容）搜才能命中。

**限制**：中文 SKILL.md 全文索引不稳（实测首次中文精确短语返回空，换 query/换语言才命中）。所以：
- 单次搜索不保证准 → **多片段 / 多语言召回**（一次空就换片段重试）
- 最终确认永远靠 **content hash**（256 位，零误差）

实现应用 **GitHub Code Search API**（直接索引代码文件，比通用 web search 对代码友好），但仍需"多片段召回 + hash 确认"策略。

## 5. steal URL 容错（已修）

`parse_github_url`（source_ops.py）现支持：

| 用户粘的 | 状态 |
|---|---|
| 子目录 `.../tree/main/neat-freak` | ✅ |
| 主仓库根 `.../owner/repo` | ✅ 多候选返回 `multi`，前端弹勾选框（合集勾选） |
| SKILL.md `.../blob/main/neat-freak/SKILL.md` | ✅ 正则认 `tree\|blob`，blob 剥末尾文件名取父目录 |
| 纯名字 `neat-freak` | ❌ 不是 URL（接 code search 通用层，未实现） |

## 6. 核心洞察：装 = 补来源（同一机制）

steal / npx 装新 skill 和给 unknown 补来源，底层是同一套"映射 + hash 确认"：

```
输入（URL / 名字 / 内容）
  ├─ 有效 GitHub URL → tree / blob / 根 / SSH 解析（steal：install_skill）
  ├─ owner/repo 或 URL → npx skills add（npx：install_skill_npx，装 mode -g 用户级）
  └─ 不是 URL（名字/内容）→ code search 按内容找候选仓库
                              → content hash 确认（最终裁判）
```

steal 和 npx 都是"装的留痕"正向机制（steal 写 `.skill-source.env`，npx 写 `~/.agents/.skill-lock.json`），dashboard 都能追溯来源。做一次映射机制，装新 + 补旧都受益。

## 7. 落地优先级与进度

1. ✅ **blob 链接解析**（`parse_github_url` 认 tree|blob）
2. ✅ **主仓库多候选** → 合集勾选弹窗（install_skill 批量 clone 复用 + renderStealPicker，默认勾当前 target 内未装的）
3. ✅ **npx 安装入口**（install_skill_npx 包装 skills CLI，防注入三道，-g 用户级）
4. ✅ 按内容 code search 通用层（`skilldash/code_search.py` 多片段召回 + hash 确认 + 无 token 降级；POST `/api/code-search`）
5. ✅ unknown skill"补来源"入口（skill 详情页"补上游来源" → 召回+hash 确认 → 写 `.skill-source.env`；POST `/api/attach-source`；写完清 upstream 短路缓存避免复用旧 unknown 结果）

## 8. app 自管宿主（WorkBuddy / CodeBuddy）：dashboard 只读旁观

WorkBuddy / CodeBuddy（腾讯 buddy family）的 skill 由 **app 自己管版本**——SkillHub（skillhub.cloud.tencent.com，1.3 万+ skill）一键装 + 自动检测新版 + 4.7.5 起批量更新 + 一键回滚，skill 落地 `~/.workbuddy/skills` / `~/.codebuddy/skills`，启用开关在各自 `settings.json`。**没有硬版本锁**（只有"关自动升级 + 回滚"软控制）。CodeBuddy 还多一层项目级 `.codebuddy/skills/`（同名覆盖用户级）。

**对 dashboard 的含义**：dashboard 对 `~/.workbuddy/skills` 等 app 自管目录是**只读旁观**——读 + 展示 OK。**steal 装进去可以工作**（实测 neat-freak 装入 `~/.workbuddy/skills/neat-freak`，`.skill-source.env` 来源留痕正常，没被清理——之前以为"装成功但看不到"是误判）。与 app 版本管理并存存在**潜在冲突**（app 自动升级 vs dashboard 手动装的版本），尚未实测，先不写死。dashboard 不该自己实现 buddy family 的更新逻辑（app 已管）。**版本锁是 dashboard 可差异化的点**（buddy family 没覆盖）。
