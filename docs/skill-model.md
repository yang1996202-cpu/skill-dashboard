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

## 3. SKILL.md 角色分类(已移除)

曾尝试给每个 SKILL.md 打静态角色标签(router / workflow / guide / focused / helper / automation),想区分「真 skill」与 connector guide、壳、子 skill 等伪装形态。实测命中率太低:判定靠关键词启发式,默认 fallback 到 workflow,90%+ 的 skill 命中不了任何角色分支,标签信息量约等于零;且后端没有任何计数逻辑消费它(纯展示层标签,buddy connector 子 skill 照样被当独立 skill 数)。已整体移除(`_classify_skill_role` / `_summarize_skill_roles` / `_is_focused_subskill`,2026-06)。

skill 真伪判定改靠 `extension_type` + `layer` 间接覆盖(第 4、8 节):connector 包内子 skill、市场货架、缓存壳由目录级载体类型识别,不做 SKILL.md 内容级角色推断。

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
| Kimi | 44(daimon) | managed | — | ✅ | — | heavy | app-embedded(已接入) |
| CherryStudio | 2 | — | — | — | — | light | app-embedded(已接入) |
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

落地时保留现有四维不破坏前端契约,在其上派生新字段(`extension_type` / `readiness`),前端渐进切换。

## 9. 落地 roadmap

1. ✅ 本文档(抽象锚点)
2. ✅ `extension_type` 字段(目录级)—— skill/builtin/plugin/connector/catalog/cache/agent,从 layer + runtime_state + package_role 派生,接入 `_classify_skill_dir_detail`
3. ✅ `readiness` 字段(Agent 级)—— uninitialized/configured-empty/builtin-only/light/heavy,从 host_profile + 目录聚合派生,挂到 `/api/targets` group;前端 group 卡片头显示徽章
4. ✅ 前端视图两级重组 —— 能力来源页卡片内分两级:一级「能力主体」(active: skill/builtin/plugin/connector,默认展开)、二级「扩展项」(inactive: catalog/cache/agent,默认折叠灰显);卡片头加身份徽章(形态 `agent_form` / family)+ 构成行(`extension_breakdown`)。`extension_type` 仍不单占目录行(与 runtime_state/layer 重叠,防噪音)
5. ⏺ `skill_role` 静态角色分类 —— 实测命中率低(默认 workflow 吞 90%+),已移除,详见第 3 节

## 10. 新 Agent 接入判断流程

```
有 SKILL.md? → 进 skill 主体,判 extension_type(第 8 节)
是 MCP/Plugin/Connector/Agent? → 进扩展项,对号入座(第 4 节)
都不是? → 新增一类扩展项(预留口子)
```

模型可扩展:遇到新形态往 5 类扩展项里加,不动核心定义。

## 11. app 形态宿主的发现路径

通用 discovery **不递归扫 `/Applications/*.app` 内部**(app 多、含二进制/asar 包、布局无统一约定,递归进去噪音大、性能差)。已知 app 宿主走两条针对性路,都靠 `host_inspectors.py` 把私有布局转成统一 source root,不污染泛化扫描器:

| 类 | 实例 | builtin skill 路径 | 发现机制 | 穿透浏览 API |
|---|---|---|---|---|
| buddy-family | WorkBuddy / CodeBuddy | `/Applications/*.app/Contents/Resources/.../builtin-skills` | `BUDDY_FAMILY_SPECS` 硬编码 source root(`p.exists()` 才登记) | 路径在 home 外,`/api/source/skills`、`/api/preview` 需 `is_app_builtin` 放行 |
| app-embedded | CherryStudio / Kimi | `~/Library/Application Support/<app>/skills` | `_APP_EMBEDDED_AGENTS` 白名单 + 大小写不敏感 depth-3 递归 | 本就在 home 下,无 403 |

**接新 app 宿主的判断**:先定位它的 builtin skill 落点。`/Applications/*.app/` 内 → 进 `BUDDY_FAMILY_SPECS` 硬编码,并补穿透 API 的 `is_app_builtin` 放行(否则 discovery 数得到、列表展开空目录);`~/Library/Application Support/` 下 → 进 `_APP_EMBEDDED_AGENTS` 白名单,穿透 API 无需改。写操作(删/复制 target)一律保持 home-only,不往 app bundle 写。
