# Skill 模型：定义与抽象层

> skill-dashboard 的核心定位是 **skill 管理器**。skill 之外的一切(plugin / connector / MCP / agent / hook)归为「能力扩展项」。
> 本文是这套分类的权威定义,后续 Agent 接入以此为准。

## 1. 为什么需要这个模型

每个 Agent 的 skill/MCP/plugin 管理方式各异,但通过对本机多个 Agent 的实测,发现**表层实现不同、抽象模型趋同**。

演进脉络:早期只看「有没有 SKILL.md」,后来发现大量「skill 模样」的东西并非真 skill(connector guide、子 skill、壳),又摸出 plugin / connector / MCP 等概念。本模型把这条演进收敛成清晰定义,避免把伪装形态当 skill 管理。

## 2. 二元核心定义

- **Skill(管理主体)**:skill-dashboard 的核心管理对象。
- **能力扩展项(Capability Extension)**:skill 周边的一切,是 skill 的载体 / 协议 / 包装 / 演化形态。

**判断基线**:所有扩展项最终都为「让 Agent 获得 skill 能力」服务。
- MCP 是 skill 的**传输层**(连外部工具)
- Plugin 是 skill 的**打包层**(容器)
- Connector 是 skill + MCP 的**业务封装层**(buddy 特有)

它们不跟 skill 平级,是 skill 的周边。

## 3. Skill 主体的 5 种角色(语定义)

一个文件叫 `SKILL.md` ≠ 顶层可注入的 skill。跨 Agent 实测,SKILL.md 扮演 5 种角色:

| 角色 | 是否真 skill | 判定信号 | 实例 |
|---|---|---|---|
| 顶层 workflow | ✅ | description 是工作流描述 | 普通 skill |
| connector-guide | ❌ 假 | description 含「如何用某 connector」 | Codex apollo/hubspot;buddy lexiang/connectors |
| focused sub-skill | 半 | 大 skill 拆出的子能力,靠 index 路由 | lexiang search/writer;buddy cloudbase 子 |
| router-index | 半 | `name: index` | 路由入口 |
| 非 skill 壳 | ❌ | connector 后端带的 SKILL.md | connector 注册表附带 |

**只有「顶层 workflow + index」算真 skill。** guide / 壳要从 skill 计数剔除或单独标,否则会出现「sales 21 文件但只注入 7」这类文件数 ≠ 注入数的偏差。

## 4. 能力扩展项的 5 类

| 类 | 定义 | transport / 形态 | 实例 |
|---|---|---|---|
| **MCP server** | 协议层,连外部工具 | http / sse / stdio | Claude 8 / Codex 3 / buddy connector 30+ / Kimi managed |
| **Plugin** | 容器,打包 skill+MCP+agent+hook+lsp | 目录 + manifest | Codex `.codex-plugin` / CodeBuddy `.codebuddy-plugin` / Claude `.claude-plugin` |
| **Connector** | MCP+认证+skill指南的业务封装 | per-connector `mcp.json` | **buddy 独有**(fbs/kdocs/notion…) |
| **Agent/Subagent** | 子智能体 | `agents/` 目录 | Codex agents / OpenClaw subagents / Alice subagent-results |
| **Hook/LSP/Command** | 其他扩展机制 | 各异 | CodeBuddy hook/lsp / Codex hooks |

**关键区分**:connector(全是 MCP)≠ plugin(MCP 只占极少数,多数是 skill/agent/hook/lsp 包)。两者在 buddy 系是独立市场。

## 5. MCP 的双向性

MCP 不只是「消费」,还能「提供」。这是跨 Agent 的一个隐藏维度:

- **消费者**:Claude / Codex / buddy / Kimi(连外部 MCP 获得工具)
- **提供者**:Alice(http MCP server,给 Claude 用)、OpenClaw(gateway)、gbrain(http,给 Claude/Codex)
- **两者皆是**:部分 Agent 既消费又提供

skill-dashboard 应能标「这个 Agent 对外暴露了哪些 MCP」,而不只看它消费了什么。

## 6. 跨 Agent 全景(实测)

| Agent | Skill | MCP(消费) | MCP(提供) | Plugin | Connector | 就绪度 | 形态 |
|---|---|---|---|---|---|---|---|
| Claude | 42 | 8 | — | ✅ | — | heavy | dotdir CLI |
| Codex | 9+.system | 3 | — | ✅ | remote | heavy | dotdir CLI |
| WorkBuddy | 9 | proxy聚合 | — | ✅ | 30+(全 disabled) | heavy / 0启用 | app+dotdir |
| CodeBuddy | 少 | 空 | — | ✅(含残留) | 少 | heavy | app+dotdir |
| Kimi | 49(daimon) | managed | — | ✅ | — | **盲区** | app-embedded |
| Alice | 25 | — | ✅(http) | subagent | — | heavy | 桌面Agent + MCP源 |
| Gemini | 8 | ? | — | — | — | mid | dotdir |
| Cursor | 14 | mcp.json | — | ✅ | — | mid | IDE |
| Trae | 50(builtin) | mcps | — | — | — | heavy | IDE |
| Qwen/Windsurf/Factory | 0 | — | — | — | — | configured-empty | dotdir |
| OpenClaw | meta层 | gateway | ✅ | — | — | 编排器 | meta-layer(非被管) |

## 7. 状态维度

贯穿所有 Agent 的两个状态轴:

- **就绪度**: `uninitialized` → `configured-empty` → `builtin-only` → `light` → `heavy`
- **启用态**: `enabled` → `installed-disabled` → `marketplace-only` → `stale`

空 Agent(就绪度 `configured-empty`,如 qwen/windsurf/factory)不需要装东西就能判断能力范式——有 `skills/` 目录 = 支持 skill 加载,有 `mcp.json` = 支持 MCP,有 `builtin` = app 自带。

## 8. 现有四维与新模型的关系

`discovery.py` 现有四维:`layer` / `policy` / `category` / `capability bucket`。它们不废弃,而是映射 / 收敛进新模型:

| 现有维度 | 方向 | 新模型对应 |
|---|---|---|
| `layer`(目录来源性质) | 标「这目录是什么」 | 拆成 **扩展项类型 + 启用态** |
| `policy`(manage/review/observe/hidden) | 治理面 | 直接对应 **启用态** |
| `category`(内容分类) | skill 讲什么 | 保留,与语定义正交 |
| `capability bucket`(前端) | 运行态能力桶 | 收敛进 **扩展项 5 类** |
| —(新增) | skill 是不是真 skill | **skill 角色**(语定义) |

落地时保留现有四维不破坏前端契约,在其上派生新字段(`skill_role` / `extension_type` / `readiness`),前端渐进切换。

## 9. 落地 roadmap

1. ✅ 本文档(抽象锚点)
2. ✅ `skill_role` 判定 —— guide/focused/壳/router/workflow/helper/automation 6 角色,focused 融入 `_classify_skill_role`,接入 source.py / fast-scan
3. ✅ `extension_type` 字段(目录级)—— skill/builtin/plugin/connector/catalog/cache/agent,从 layer + runtime_state + package_role 派生,接入 `_classify_skill_dir_detail`
4. ✅ `readiness` 字段(Agent 级)—— uninitialized/configured-empty/builtin-only/light/heavy,从 host_profile + 目录聚合派生,挂到 `/api/targets` group;前端 group 卡片头显示徽章
5. ⏳ 前端视图按新轴重组 —— readiness 徽章已上 group 卡片头;`extension_type` 暂不单占目录行(与 runtime_state/layer 重叠,防噪音),一级 skill / 二级扩展项的视图结构重组留下一轮定

## 10. 新 Agent 接入判断流程

```
有 SKILL.md? → 进 skill 主体,判角色(第 3 节)
是 MCP/Plugin/Connector/Agent? → 进扩展项,对号入座(第 4 节)
都不是? → 新增一类扩展项(预留口子)
```

模型可扩展:遇到新形态往 5 类扩展项或 5 角色 skill 里加,不动核心定义。
