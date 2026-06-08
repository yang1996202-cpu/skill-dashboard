# 排错手册

按现象关键词检索。每条记：**现象 → 根因 → 修复**。

---

## 前端渲染

### 页面显示乱码 / 内容截断 / 奇怪字符重叠

**现象**：切到某些技能库（如 WorkBuddy 268 skills）后，仪表盘或技能列表显示截断、重叠、或不可读内容。

**根因**：skill 的 `description` 字段包含反引号 `` ` `` 或 `${}`。前端用 JS 模板字符串（template literal）渲染 HTML，这些字符会截断模板字符串，导致后面的 HTML/JS 变成语法错误。

**影响范围**：任何把 skill 数据直接插入 `` ` ` ${} `` 模板的地方。

**修复**：
- `safeDesc()` 函数：`` ` `` → `'`，`${` → `{`
- **数据加载时统一清洗**（`loadData` / `switchTarget`），不要依赖渲染点逐个调用
- `esc()` 也需要转义反引号

**排查命令**：
```bash
curl -s http://localhost:3457/api/fast-scan | python3 -c "
import json,sys
d=json.load(sys.stdin)
for s in d.get('installed',[]):
  desc=s.get('description','') or ''
  if '\`' in desc or '\${' in desc:
    print(f'{s[\"name\"]}: {repr(desc[:80])}')
"
```

---

### 分类分布出现 30+ 个奇怪分类名（如 productivity、tencent、Education）

**现象**：切换到某些技能库后，分类分布卡片显示大量非标准分类名，混在标准分类里。

**根因**：SKILL.md frontmatter 的 `category` 字段值五花八门（如 `productivity`、`frontend`、`Design Tools`），但前端分类逻辑只对**空 category** 做 keyword 分类，有 frontmatter 值的直接保留。

**修复**：分类判断改为三段：
1. 用户手动覆盖（localStorage）→ 优先
2. 空 category 或**非标准 category**（不在 `CAT_NAMES` 里）→ keyword 分类
3. 标准 category → 保留 frontmatter 值

关键代码：
```js
if(!s.category || s.category==='' || !CAT_NAMES[s.category]) {
  s.category = classifySkillJS(s.name, s.description);
}
```

---

### 切换目标库后仪表盘不更新

**现象**：在目标库下拉切换后，技能库页面已更新但仪表盘的分类、统计还是旧的，需要手动刷新页面。

**根因**：`switchTarget()` 只更新了 `scan` / `skills`（来自 `/api/target`），没有重新拉 `/api/targets`（刷新 `targets` + `targetGroups`）和 `/api/global-stats`（刷新 `globalStats`）。

**修复**：`switchTarget()` 切换后追加：
```js
// 刷新 targets + groups
const td = await fetch('/api/targets').then(r=>r.json());
targets = td.targets; targetGroups = td.groups;
// 刷新 global stats
fetch('/api/global-stats').then(...);
```

---

## 数据层

### YAML 多行 description 解析为 `>` 或 `|`

**现象**：skill 的 description 显示为单个字符 `>` 或 `|`，而不是实际描述内容。

**根因**：SKILL.md 使用 YAML 多行语法（`description: >` 或 `description: |`），后端解析时只取了指示符本身，没有收集后续缩进行。

**修复**（`serve.py` `_fast_scan`）：
```python
if val in (">", "|", ">-", "|-", ">+", "|+"):
    parts = []
    for cont in fm_lines[i + 1:]:
        if cont and not cont[0].isspace():
            break
        parts.append(cont.strip())
    description = " ".join(parts)
```

---

### 技能库数量闪跳（152→133 或类似）

**现象**：页面加载时技能库数量先显示一个数字，然后闪跳到另一个。

**根因**：`scan.totals.skills` 被过期的诊断缓存覆盖。fast-scan 返回的是实时值，但诊断缓存的旧值后来写入，覆盖了新值。

**修复**：`scan.totals.skills` 只取 fast-scan 的值，诊断结果不覆盖它。

---

### `/api/targets` 返回格式变化导致前端报错

**现象**：切换目标或刷新后控制台报 `data.map is not a function` 或 `data.forEach is not a function`。

**根因**：`/api/targets` 从返回数组改为返回 `{targets, groups}` 对象，但部分消费方仍按数组处理。

**修复**：所有 `fetch('/api/targets')` 消费方统一处理：
```js
const td = await fetch('/api/targets').then(r=>r.json());
const ts = td.targets || td;  // 兼容数组和对象
```

---

### btoa 中文路径崩溃导致技能库显示 0

**现象**：技能库页面有来源但展开后显示 0 个 skill，控制台报 `Failed to execute 'btoa'` 或 `InvalidCharacterError`。

**根因**：用 `btoa()` 把路径转成 DOM ID，但 `btoa` 不支持非 ASCII 字符（中文路径如 `~/项目/skills`）。转码失败 → ID 生成失败 → DOM 找不到容器 → 内容不渲染。

**修复**：用 `encodeURIComponent()` 替代 `btoa()`：
```js
// 错误
const safeId = 'src-body-' + btoa(s.path).replace(/[^a-zA-Z0-9]/g,'').slice(0,20);
// 正确
const safeId = 'src-body-' + encodeURIComponent(s.path).replace(/[^a-zA-Z0-9]/g,'').slice(0,20);
```

**适配风险**：任何把用户路径当 DOM ID 的地方，中文/日文/韩文/emoji 路径都会触发。其他用户机器上 `~/下载/`、`~/桌面/` 等中文路径是常见场景。

---

### 技能库页面无数据（来源列表空白）

**现象**：技能库页面显示"无来源数据"或空白，但 `/api/targets` 能正常返回目录列表。

**根因**：`scan.sources` 为空。在无 skill-mgr 的环境下，`/api/fast-scan` 不返回 `sources` 字段，而前端直接用 `scan.sources` 渲染，空就显示空白。

**修复**：`loadData()` 和 `switchTarget()` 都加 fallback——`scan.sources` 为空时从 `/api/targets` 补数据：
```js
if(!scan?.sources?.length){
  const ts = await fetch('/api/targets').then(r=>r.json());
  scan.sources = ts.map(t=>({...}));
  renderSources();
}
```

**适配风险**：任何新安装、无 skill-mgr 的环境下必现。这是独立模式的核心兜底逻辑。

---

### 删除 skill 后列表不刷新 / 删错 skill

**现象**：删除一个 skill 后列表还显示它，或者删的是另一个 skill。

**根因**：删除 API 的路径拼接错误，或删除后没有重新调用 `loadData()` 列表。前端用 `name` 定位 skill，但如果同名 skill 存在于不同目录可能删错。

**修复**：
- 后端：`_validate_skill_name()` 白名单校验 `[a-zA-Z0-9._@+\-]+`
- 后端：删除前验证路径在当前 target 目录内（防路径穿越）
- 前端：删除成功后 `await loadData()` 强制刷新

---

## 适配性风险（跨用户环境）

### 路径中的特殊字符

用户机器上可能出现：
- **中文路径**：`~/下载/skills`、`~/桌面/`、`~/项目/xxx/` → `btoa` 崩溃、URL 编码问题
- **空格路径**：`~/My Projects/skills` → shell 命令拼接需引号包裹
- **emoji 路径**：macOS 允许目录名含 emoji → JSON 序列化/反序列化正常但 DOM ID 可能异常
- **符号链接**：`~` 展开为 `/Users/xxx`，`$HOME` 环境变量可能不一致 → 路径比较用 `realpath` 归一化

### 权限问题

- **只读目录**：`/api/copy-skill` 写入目标目录时权限不足 → 需要在后端捕获 `PermissionError` 返回友好错误
- **symlink 指向不存在的目标**：`broken_symlink` 检测在 `_fast_scan` 中已处理，但某些 agent（如 CC-Switch 的备份目录）可能存在循环链接

### GitHub API 限流

- **未认证 60 次/小时**：频繁安装/更新 skill 时容易触发。后端有 5 分钟 TTL 缓存 + 熔断
- **企业/学校网络**：GitHub API 可能被代理/防火墙拦截 → `ConnectionError` → 需要给用户明确提示而非静默失败

### 字符编码

- **SKILL.md 用 GBK/GB2312 编码**：Python `open()` 默认 UTF-8 会报 `UnicodeDecodeError`。目前用 `errors='replace'` 容错，但内容可能乱码
- **Windows 换行符 `\r\n`**：YAML frontmatter 解析时 `\r` 残留会导致匹配失败。已用 `.strip()` 处理但极端情况可能遗漏

---

## 排查方法论

1. **先确认是数据问题还是渲染问题**：用 `curl` 拉 API 看原始数据，数据正常 → 前端问题
2. **模板字符串注入**：`grep '`' desc` 或检查 `safeDesc` 是否覆盖
3. **变量未定义 / 格式变化**：浏览器 F12 控制台看报错，定位行号
4. **缓存过期**：检查 `.data/cache/` 和 `.data/state/` 的时间戳
5. **浏览器缓存**：Cmd+Shift+R 强制刷新，排除旧 JS 干扰
6. **中文路径**：搜索代码中所有 `btoa` 调用，确认已替换为 `encodeURIComponent`
7. **API 格式兼容**：搜索所有 `fetch('/api/targets')`，确认 `td.targets||td` 兜底
