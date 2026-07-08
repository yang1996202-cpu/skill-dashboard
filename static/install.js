/* ── 技能安装 ── */

/* ── Install page render ── */
function renderInstallPage(){
  const el = $('install-list');
  if (!el) return;

  const cur = targets.find(t => t.is_current);
  const curName = cur ? cur.name : '未选择目录';
  const curRel = cur ? cur.rel : '';

  el.innerHTML = `
    <div class="install-page">
      <div class="card" style="margin-bottom:14px">
        <div class="card-head">
          <h3>安装目标</h3>
          <span class="sub">选择技能安装到哪个目录</span>
        </div>
        <div style="padding:4px 0">
          <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
            <div class="target-bar" style="position:relative;flex:1;min-width:200px;max-width:420px">
              <div class="target-current" id="install-target-btn" onclick="toggleInstallTargetDropdown()" style="border:1px solid var(--border);border-radius:8px">
                <div class="t-info">
                  <div class="t-name" id="install-t-name">${esc(curName)}</div>
                  <div class="t-meta"><span id="install-t-count">${cur?cur.count:'-'}</span> skills · ${esc(curRel)}</div>
                </div>
                <span class="t-arrow">▼</span>
              </div>
              <div class="target-dropdown" id="install-target-dropdown"></div>
            </div>
            <span style="font-size:11px;color:var(--text-muted)">安装到此目录后可在对应 Agent 中使用</span>
          </div>
        </div>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:14px">
        <!-- Steal: GitHub 安装 -->
        <div class="card">
          <div class="card-head">
            <h3>从 GitHub 安装</h3>
            <span class="sub">粘贴 GitHub 仓库 URL，自动识别并安装 skill</span>
          </div>
          <div style="padding:4px 0">
            <label style="display:block;margin-bottom:6px;font-size:12px;font-weight:500;color:var(--text)">来源 URL</label>
            <div style="display:flex;gap:8px">
              <input id="install-steal-source" type="text" placeholder="https://github.com/user/repo 或 skill 名称" style="flex:1;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;font-family:inherit">
              <button class="btn btn-primary" id="install-steal-btn" onclick="doInstallSteal()">安装</button>
            </div>
            <div id="install-steal-result" style="display:none;padding:10px;border-radius:8px;margin-top:10px;font-size:12px"></div>
            <div style="margin-top:8px;font-size:11px;color:var(--text-muted)">
              支持格式：<code style="font-family:var(--mono);padding:1px 4px;background:var(--bg-card-alt);border-radius:3px">owner/repo</code>、完整 GitHub URL、tree/blob 子目录
            </div>
          </div>
        </div>

        <!-- npx 安装 -->
        <div class="card">
          <div class="card-head">
            <h3>npx 安装</h3>
            <span class="sub">通过 <code style="font-family:var(--mono);padding:1px 4px;background:var(--bg-card-alt);border-radius:3px">npx -y skills add</code> 安装器安装</span>
          </div>
          <div style="padding:4px 0">
            <label style="display:block;margin-bottom:6px;font-size:12px;font-weight:500;color:var(--text)">包名</label>
            <div style="display:flex;gap:8px">
              <input id="install-npx-source" type="text" placeholder="vercel-labs/agent-skills 或 GitHub URL" style="flex:1;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;font-family:inherit">
              <button class="btn btn-primary" id="install-npx-btn" onclick="doInstallNpxProbe()">检测</button>
            </div>
            <div id="install-npx-result" style="display:none;padding:10px;border-radius:8px;margin-top:10px;font-size:12px"></div>
            <div style="margin-top:8px;font-size:11px;color:var(--text-muted)">
              先「检测」列出仓库中的 skill，勾选后批量安装。走全局 <code style="font-family:var(--mono);padding:1px 4px;background:var(--bg-card-alt);border-radius:3px">-g</code> 模式。
            </div>
          </div>
        </div>
      </div>

      <!-- Import: 拖拽 zip -->
      <div class="card" style="margin-top:14px">
        <div class="card-head">
          <h3>导入 Skill 包</h3>
          <span class="sub">拖拽 .zip 文件到这里，或点击选择文件。zip 内每个含 SKILL.md 的目录会自动安装。</span>
        </div>
        <div class="install-drop-zone" id="install-drop-zone">
          <div style="text-align:center;pointer-events:none">
            <div style="font-size:28px;margin-bottom:6px">📦</div>
            <div style="font-size:13px;font-weight:500;color:var(--text)">拖拽 .zip 文件到这里</div>
            <div style="font-size:11px;color:var(--text-muted);margin-top:4px">或点击选择文件</div>
          </div>
          <input type="file" id="install-file-input" accept=".zip" style="display:none" onchange="handleInstallFile(this.files)">
        </div>
        <div id="install-import-result" style="display:none;padding:10px;border-radius:8px;margin-top:10px;font-size:12px"></div>
      </div>
    </div>
  `;

  // Pre-fill the target dropdown for later toggle
  renderInstallTargetDropdown();

  // Bind drop zone events
  setTimeout(() => {
    const zone = $('install-drop-zone');
    if (!zone) return;
    zone.addEventListener('click', () => $('install-file-input')?.click());
    zone.addEventListener('dragover', e => { e.preventDefault(); zone.classList.add('drag-over'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));
    zone.addEventListener('drop', e => {
      e.preventDefault();
      zone.classList.remove('drag-over');
      handleInstallFile(e.dataTransfer.files);
    });
  }, 0);
}

/* ── Install target selector (独立于 sidebar,但切换的是同一个全局 current target) ── */
function toggleInstallTargetDropdown(){
  const dd = $('install-target-dropdown');
  if (!dd) return;
  const isOpen = dd.classList.contains('open');
  // Close first, then toggle
  document.querySelectorAll('.target-dropdown').forEach(d => d.classList.remove('open'));
  if (!isOpen) {
    renderInstallTargetDropdown();
    dd.classList.add('open');
  }
}

function renderInstallTargetDropdown(){
  const dd = $('install-target-dropdown');
  if (!dd) return;
  const displayGroups = sortGroupsByCurrentAndSize(filterGroupsByView(targetGroups, 'all'));
  dd.innerHTML = displayGroups.map(g => {
    const isCurGroup = g.dirs.some(t => t.is_current);
    const visibleDirs = [...g.dirs].sort((a, b) => {
      const aCur = a.is_current ? 1 : 0;
      const bCur = b.is_current ? 1 : 0;
      if (aCur !== bCur) return bCur - aCur;
      return b.count - a.count;
    });
    const gId = 'ig-' + g.agent.replace(/[^a-zA-Z0-9]/g, '');
    return `<div class="tg-wrap${isCurGroup ? ' tg-active' : ''}" style="border-bottom:1px solid var(--border-subtle)">
      <div class="target-opt" onclick="toggleInstallGroup('${gId}',this)" style="padding:8px 10px">
        <span style="font-size:11px;transition:transform .15s" id="${gId}-arrow">${isCurGroup ? '▼' : '▶'}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:12px;display:flex;align-items:center;gap:5px">${esc(g.agent)}${isCurGroup ? '<span style="font-size:9px;color:var(--accent);font-weight:400">当前</span>' : ''}</div>
          <div style="font-size:10px;color:var(--text-muted)">${g.dirs.length} 个目录 · ${g.total_skills} skills</div>
        </div>
      </div>
      <div id="${gId}" style="display:${isCurGroup ? 'block' : 'none'};background:var(--bg-card-alt)">
        ${visibleDirs.map(t => {
          return `<div class="target-opt${t.is_current ? ' active' : ''}" onclick="event.stopPropagation();installSwitchTarget('${esc(t.path)}')" style="padding:6px 10px 6px 14px" title="${esc(t.rel)}">
            <span style="flex:1;font-size:11px">${esc(t.rel)}</span>
            <span class="to-count" style="font-size:10px">${t.count}</span>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }).join('');

  // Close dropdown on outside click
  setTimeout(() => {
    const handler = (e) => {
      if (!e.target.closest('#install-target-btn') && !e.target.closest('#install-target-dropdown')) {
        dd.classList.remove('open');
        document.removeEventListener('click', handler);
      }
    };
    document.addEventListener('click', handler);
  }, 0);
}

function toggleInstallGroup(id, head) {
  const sub = document.getElementById(id);
  const arrow = document.getElementById(id + '-arrow');
  if (!sub) return;
  const isOpen = sub.style.display !== 'none';
  sub.style.display = isOpen ? 'none' : 'block';
  if (arrow) arrow.textContent = isOpen ? '▶' : '▼';
}

async function installSwitchTarget(path) {
  $('install-target-dropdown').classList.remove('open');
  if (!path) return;
  // Use the same switchTarget logic from app-bootstrap.js
  await switchTarget(path);
  // Refresh the install page header to reflect new target
  renderInstallPage();
}

/* ── Steal install (from GitHub URL) ── */
async function doInstallSteal(){
  const sourceEl = $('install-steal-source');
  const source = sourceEl ? sourceEl.value.trim() : '';
  if (!source) { toast('请输入来源 URL', 'error'); return; }

  const cur = targets.find(t => t.is_current);
  if (!cur) { toast('请先选择安装目标目录', 'error'); return; }
  if (!(await confirmInstallGlobal(cur))) return;

  const btn = $('install-steal-btn');
  const result = $('install-steal-result');
  if (btn) { btn.disabled = true; btn.textContent = '安装中...'; }
  if (result) { result.style.display = 'block'; result.style.background = 'var(--bg-card-alt)'; result.style.color = 'var(--text-muted)'; result.textContent = '正在检测仓库...'; }

  try {
    const r = await fetch('/api/steal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source }) });
    const d = await r.json();
    if (d.multi === true) {
      // Multi-candidate → show picker modal
      window.__installStealSource = source;
      renderInstallStealPicker(d);
    } else if (d.ok) {
      if (result) { result.style.background = 'var(--green-bg)'; result.style.color = 'var(--green)'; result.textContent = '✅ 安装成功'; }
      toast('Skill 已安装');
      invalidateTargetsCache();
      clearGlobalSearchCache();
      await loadData();
      renderInstallPage();
    } else {
      if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = '❌ ' + (d.error || '安装失败'); }
    }
  } catch (e) {
    if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = '❌ ' + e.message; }
  }
  if (btn) { btn.disabled = false; btn.textContent = '安装'; }
}

function renderInstallStealPicker(d) {
  const cands = d.candidates || [];
  const repo = d.repo || '';
  const installedNames = new Set((skills || []).map(function(s) { return s.name; }));
  $('modal-title').textContent = '选择要安装的 Skill';
  $('modal-body').innerHTML = `
    <div style="font-family:-apple-system,sans-serif;font-size:13px;color:var(--text)">
      <div style="margin-bottom:6px;color:var(--text-muted)">仓库 <strong style="color:var(--indigo)">${esc(repo)}</strong> 有 ${cands.length} 个 skill，勾选要安装的：</div>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <button class="btn" style="font-size:12px;padding:4px 10px" onclick="installStealPickerToggle(true)">全选</button>
        <button class="btn" style="font-size:12px;padding:4px 10px" onclick="installStealPickerToggle(false)">全不选</button>
      </div>
      <div id="install-steal-pick-list" style="max-height:260px;overflow:auto;border:1px solid var(--border);border-radius:8px;background:var(--bg-card);padding:4px 0;margin-bottom:12px">
        ${cands.map(function(c) {
          const installed = installedNames.has(c);
          return `<label style="display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border)">
            <input type="checkbox" class="install-steal-pick-cb" value="${esc(c)}" ${installed ? '' : 'checked'} style="accent-color:var(--indigo)">
            <span style="font-family:var(--mono);font-size:12px">${esc(c)}</span>
            ${installed ? '<span style="font-size:11px;color:var(--text-muted);margin-left:auto">已装</span>' : ''}
          </label>`;
        }).join('')}
      </div>
      <div id="install-steal-modal-result" style="display:none;padding:10px;border-radius:8px;margin-bottom:8px;font-size:12px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn" onclick="$('modal').classList.add('hidden')">取消</button>
        <button class="btn btn-primary" id="install-steal-install-btn" onclick="doInstallStealBatch()">装勾选的</button>
      </div>
    </div>`;
  $('modal').classList.remove('hidden');
}

function installStealPickerToggle(on) {
  document.querySelectorAll('.install-steal-pick-cb').forEach(function(cb) { cb.checked = on; });
}

async function doInstallStealBatch() {
  const picked = Array.from(document.querySelectorAll('.install-steal-pick-cb:checked')).map(function(cb) { return cb.value; });
  if (picked.length === 0) { toast('请至少勾选一个', 'error'); return; }
  if (!(await confirmInstallGlobal(targets.find(t => t.is_current) || targets[0]))) return;
  const source = window.__installStealSource || '';
  const btn = $('install-steal-install-btn');
  const result = $('install-steal-modal-result');
  if (btn) { btn.disabled = true; btn.textContent = '安装中...'; }
  if (result) { result.style.display = 'block'; result.style.background = 'var(--bg-card-alt)'; result.style.color = 'var(--text-muted)'; result.textContent = '正在安装 ' + picked.length + ' 个...'; }
  try {
    const r = await fetch('/api/steal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source, names: picked }) });
    const d = await r.json();
    if (d.ok) {
      const lines = (d.results || []).map(function(r) { return (r.ok ? '✓ ' : '✗ ') + r.name + (r.ok ? '' : ' (' + (r.error || '失败') + ')'); });
      if (result) {
        result.style.background = 'var(--green-bg)'; result.style.color = 'var(--green)';
        result.textContent = lines.join('\n') + '\n' + (d.output || '');
        result.style.whiteSpace = 'pre-wrap';
      }
      toast(d.output || ('已安装 ' + picked.length + ' 个'));
      invalidateTargetsCache();
      clearGlobalSearchCache();
      await loadData();
      renderInstallPage();
      if (btn) { btn.disabled = false; btn.textContent = '装勾选的'; }
      setTimeout(function() { $('modal').classList.add('hidden'); }, 1200);
    } else {
      if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = '❌ ' + (d.error || '安装失败'); }
      if (btn) { btn.disabled = false; btn.textContent = '装勾选的'; }
    }
  } catch (e) {
    if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = '❌ ' + e.message; }
    if (btn) { btn.disabled = false; btn.textContent = '装勾选的'; }
  }
}

/* ── npx install ── */
async function doInstallNpxProbe() {
  const sourceEl = $('install-npx-source');
  const pkg = sourceEl ? sourceEl.value.trim() : '';
  if (!pkg) { toast('请输入包名', 'error'); return; }

  const cur = targets.find(t => t.is_current);
  if (!cur) { toast('请先选择安装目标目录', 'error'); return; }

  const btn = $('install-npx-btn');
  const result = $('install-npx-result');
  if (btn) { btn.disabled = true; btn.textContent = '检测中...'; }
  if (result) { result.style.display = 'block'; result.style.background = 'var(--bg-card-alt)'; result.style.color = 'var(--text-muted)'; result.textContent = '正在调用 skills CLI 探测仓库...'; }

  try {
    const r = await fetch('/api/steal-npx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ package: pkg }) });
    const d = await r.json();
    if (d.multi === true) {
      window.__installNpxPackage = pkg;
      renderInstallNpxPicker(d);
    } else if (d.ok) {
      if (result) { result.style.background = 'var(--green-bg)'; result.style.color = 'var(--green)'; result.textContent = '✅ 安装成功'; }
      toast('Skill 已安装');
      invalidateTargetsCache();
      clearGlobalSearchCache();
      await loadData();
      renderInstallPage();
    } else {
      if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = (d.error || '探测失败'); }
    }
  } catch (e) {
    if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = e.message; }
  }
  if (btn) { btn.disabled = false; btn.textContent = '检测'; }
}

function renderInstallNpxPicker(d) {
  const cands = d.candidates || [];
  const pkg = d.package || window.__installNpxPackage || '';
  const installedNames = new Set((skills || []).map(function(s) { return s.name; }));
  $('modal-title').textContent = '选择要安装的 Skill';
  $('modal-body').innerHTML = `
    <div style="font-family:-apple-system,sans-serif;font-size:13px;color:var(--text)">
      <div style="margin-bottom:6px;color:var(--text-muted)"><code style="font-family:var(--mono);padding:1px 5px;background:var(--bg-card-alt);border-radius:4px">${esc(pkg)}</code> 有 ${cands.length} 个 skill，勾选要安装的：</div>
      <div style="display:flex;gap:8px;margin-bottom:8px">
        <button class="btn" style="font-size:12px;padding:4px 10px" onclick="installNpxPickerToggle(true)">全选</button>
        <button class="btn" style="font-size:12px;padding:4px 10px" onclick="installNpxPickerToggle(false)">全不选</button>
      </div>
      <div id="install-npx-pick-list" style="max-height:260px;overflow:auto;border:1px solid var(--border);border-radius:8px;background:var(--bg-card);padding:4px 0;margin-bottom:12px">
        ${cands.map(function(c) {
          const installed = installedNames.has(c);
          return `<label style="display:flex;align-items:center;gap:8px;padding:6px 12px;cursor:pointer;border-bottom:1px solid var(--border)">
            <input type="checkbox" class="install-npx-pick-cb" value="${esc(c)}" ${installed ? '' : 'checked'} style="accent-color:var(--indigo)">
            <span style="font-family:var(--mono);font-size:12px">${esc(c)}</span>
            ${installed ? '<span style="font-size:11px;color:var(--text-muted);margin-left:auto">已装</span>' : ''}
          </label>`;
        }).join('')}
      </div>
      <div id="install-npx-modal-result" style="display:none;padding:10px;border-radius:8px;margin-bottom:8px;font-size:12px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn" onclick="$('modal').classList.add('hidden')">取消</button>
        <button class="btn btn-primary" id="install-npx-install-btn" onclick="doInstallNpxBatch()">装勾选的</button>
      </div>
    </div>`;
  $('modal').classList.remove('hidden');
}

function installNpxPickerToggle(on) {
  document.querySelectorAll('.install-npx-pick-cb').forEach(function(cb) { cb.checked = on; });
}

async function doInstallNpxBatch() {
  const picked = Array.from(document.querySelectorAll('.install-npx-pick-cb:checked')).map(function(cb) { return cb.value; });
  if (picked.length === 0) { toast('请至少勾选一个', 'error'); return; }
  if (!(await confirmInstallGlobal(targets.find(t => t.is_current) || targets[0]))) return;
  const pkg = window.__installNpxPackage || '';
  const btn = $('install-npx-install-btn');
  const result = $('install-npx-modal-result');
  if (btn) { btn.disabled = true; btn.textContent = '安装中...'; }
  if (result) { result.style.display = 'block'; result.style.background = 'var(--bg-card-alt)'; result.style.color = 'var(--text-muted)'; result.textContent = '正在通过 skills CLI 安装 ' + picked.length + ' 个...'; }
  try {
    const r = await fetch('/api/steal-npx', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ package: pkg, names: picked }) });
    const d = await r.json();
    if (d.ok) {
      const lines = (d.results || []).map(function(r) { return (r.ok ? '✓ ' : '✗ ') + r.name + (r.ok ? '' : ' (' + (r.error || '失败') + ')'); });
      if (result) {
        result.style.background = 'var(--green-bg)'; result.style.color = 'var(--green)';
        result.textContent = lines.join('\n') + '\n' + (d.output || '');
        result.style.whiteSpace = 'pre-wrap';
      }
      toast(d.output || ('已安装 ' + picked.length + ' 个'));
      invalidateTargetsCache();
      clearGlobalSearchCache();
      await loadData();
      renderInstallPage();
      if (btn) { btn.disabled = false; btn.textContent = '装勾选的'; }
      setTimeout(function() { $('modal').classList.add('hidden'); }, 1200);
    } else {
      if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = (d.error || '安装失败'); }
      if (btn) { btn.disabled = false; btn.textContent = '装勾选的'; }
    }
  } catch (e) {
    if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = e.message; }
    if (btn) { btn.disabled = false; btn.textContent = '装勾选的'; }
  }
}

/* ── Import zip ── */
async function handleInstallFile(files) {
  if (!files || !files.length) return;
  const file = files[0];
  if (!file.name.endsWith('.zip')) { toast('请选择 .zip 文件', 'error'); return; }

  const cur = targets.find(t => t.is_current);
  if (!cur) { toast('请先选择安装目标目录', 'error'); return; }
  if (!(await confirmInstallGlobal(cur))) return;

  const result = $('install-import-result');
  if (result) { result.style.display = 'block'; result.style.background = 'var(--bg-card-alt)'; result.style.color = 'var(--text-muted)'; result.textContent = '正在读取文件...'; }

  try {
    // Read file as base64
    const buf = await file.arrayBuffer();
    const bytes = new Uint8Array(buf);
    let binary = '';
    for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
    const b64 = btoa(binary);

    if (result) { result.textContent = '正在上传并解压...'; }

    const r = await fetch('/api/install/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data: b64, filename: file.name })
    });
    const d = await r.json();

    if (d.ok) {
      const names = d.installed || [];
      if (result) {
        result.style.background = 'var(--green-bg)'; result.style.color = 'var(--green)';
        result.textContent = `✅ 已导入 ${names.length} 个 skill: ${names.join(', ')}` + (d.errors && d.errors.length ? '\n⚠ ' + d.errors.join('\n') : '');
        result.style.whiteSpace = 'pre-wrap';
      }
      toast(`已导入 ${names.length} 个 skill`);
      invalidateTargetsCache();
      clearGlobalSearchCache();
      await loadData();
      renderInstallPage();
    } else {
      if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = '❌ ' + (d.error || '导入失败'); }
    }
  } catch (e) {
    if (result) { result.style.background = 'var(--red-bg)'; result.style.color = 'var(--red)'; result.textContent = '❌ ' + e.message; }
  }
}
