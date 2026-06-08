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

## 排查方法论

1. **先确认是数据问题还是渲染问题**：用 `curl` 拉 API 看原始数据，数据正常 → 前端问题
2. **模板字符串注入**：`grep '`' desc` 或检查 `safeDesc` 是否覆盖
3. **变量未定义 / 格式变化**：浏览器 F12 控制台看报错，定位行号
4. **缓存过期**：检查 `.data/cache/` 和 `.data/state/` 的时间戳
5. **浏览器缓存**：Cmd+Shift+R 强制刷新，排除旧 JS 干扰
