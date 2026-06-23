
/* ── Sources (穿透浏览 + 来源目录) ── */
let sourceSkillsCache={};
let _sourceSortMode=localStorage.getItem('sd-source-sort')||'default'; // default | skills | dirs
function setSourceSortMode(mode){
  _sourceSortMode=mode||'default';
  localStorage.setItem('sd-source-sort',_sourceSortMode);
  renderSources();
}

function sourceRuntimeBadge(t){
  const state=t?.runtime_state;
  if(!state)return '';
  const meta={
    'user-root':{label:'用户自建 Skill',cls:'loaded',desc:'宿主用户技能根目录，通常进入当前能力面。'},
    builtin:{label:'系统内置',cls:'loaded',desc:'宿主 App 自带能力包。'},
    enabled:{label:'已启用插件',cls:'loaded',desc:'当前 Codex config.toml 启用了这个插件包。'},
    connector:{label:'连接器包',cls:'catalog',desc:'由宿主 app/connector 运行时提供，可能按需暴露技能或工具。'},
    loaded:{label:'已加载',cls:'loaded',desc:'当前宿主 enabledPlugins 已启用，且匹配 installed_plugins 安装路径。'},
    installed:{label:'已安装未启用',cls:'installed',desc:'installed_plugins 里有记录，但当前没有启用。'},
    catalog:{label:t?.loaded_elsewhere?'市场目录 · 同名已启用':'市场目录',cls:'catalog',desc:'marketplace 货架目录，不等于当前上下文已加载。'},
    orphaned:{label:'旧包缓存',cls:'orphaned',desc:'同一插件的旧版本缓存，通常不是当前加载对象。'},
    stale:{label:'非当前安装包',cls:'orphaned',desc:'同名插件另有当前安装路径，此目录只是遗留副本。'},
    cache:{label:'插件包缓存',cls:'cache',desc:'位于插件缓存区，未匹配到当前安装记录。'},
  }[state];
  if(!meta)return '';
  return `<span class="source-status ${meta.cls}" title="${esc(t.runtime_reason||meta.desc)}">${escapeHtml(t.runtime_label||meta.label)}</span>`;
}

function sourceReadinessBadge(g){
  const r=g?.readiness;
  if(!r)return '';
  const label=g?.readiness_label||r;
  const meta={
    'heavy':{cls:'loaded',icon:'🔥',desc:'10+ skill,或连接器/插件/已启用 MCP 较多'},
    'light':{cls:'catalog',icon:'🌱',desc:'1-9 个 skill,轻度使用'},
    'builtin-only':{cls:'installed',icon:'📦',desc:'只有宿主自带 skill'},
    'configured-empty':{cls:'orphaned',icon:'⚙️',desc:'有 skills/ 或 mcp.json,但 0 skill'},
    'uninitialized':{cls:'cache',icon:'∅',desc:'Agent 目录不存在或完全空'},
  }[r]||{cls:'cache',icon:'',desc:label};
  return `<span class="source-status ${meta.cls}" style="font-size:9px" title="就绪度 · ${esc(meta.desc)}">${meta.icon?meta.icon+' ':''}${esc(label)}</span>`;
}

function sourceMiniChip(label,title){
  if(!label)return '';
  return `<span class="source-mini-chip" title="${esc(title||label)}">${label}</span>`;
}

function sourceDisplayTitle(t){
  if(t?.plugin_id)return t.plugin_id;
  return t?.rel||t?.path||'未知目录';
}

function sourceDisplaySub(t){
  const ev=(t?.evidence||[]).slice(-2).join(' · ');
  if(t?.plugin_id){
    const version=t.plugin_version?` · ${t.plugin_version}`:'';
    return `${t.rel||t.path}${version}${ev?` · ${ev}`:''}`;
  }
  return ev||sourceLayerLabel(t);
}

function sourceSubLabel(t){
  const rel=t?.rel||'';
  if(rel.includes('/backups/'))return '备份';
  if(rel.includes('/connectors/'))return '连接器';
  if(rel.includes('/hermes-agent/'))return 'hermes-agent';
  if(rel.includes('/skills-marketplace/'))return '商店';
  if(rel.includes('/builtin-skills'))return '内置';
  if(rel.includes('/builtin-plugins/'))return '内置插件';
  if(rel.includes('/extensions/'))return '扩展';
  if(rel.includes('/skill-backups/'))return '旧备份';
  if(rel.includes('/workspaces/'))return 'workspace';
  if(rel.includes('/openclaw-imports'))return 'imports';
  if(rel.includes('gstack'))return 'gstack';
  return '';
}

function formatSourceCounts(dirs){
  const total=dirs.reduce((s,d)=>s+(d.count||0),0);
  const cmdCount=dirs.filter(d=>d.type==='commands').reduce((s,d)=>s+(d.count||0),0);
  const skillCount=total-cmdCount;
  const roleCounts={};
  dirs.forEach(d=>mergeSkillRoleCounts(roleCounts,d.skill_role_counts));
  const roleText=skillRoleSummaryText(roleCounts);
  const parts=[];
  if(skillCount)parts.push(`${skillCount} skills`);
  if(cmdCount)parts.push(`${cmdCount} commands`);
  if(roleText)parts.push(roleText);
  return parts.join(' · ')||'0 项';
}

function sourceGroupProfileHint(g){
  const p=g?.profile_summary;
  if(!p)return '';
  const parts=[];
  if(p.family)parts.push(p.family);
  if(p.source_root_count)parts.push(`来源根 ${p.source_root_count}`);
  if(p.mcp_runtime_server_count)parts.push(`运行 MCP ${p.mcp_runtime_server_count}`);
  if(p.mcp_catalog_server_count)parts.push(`市场 MCP ${p.mcp_catalog_server_count}`);
  else if(p.mcp_server_count)parts.push(`MCP ${p.mcp_server_count}`);
  return parts.join(' · ');
}

function sourceCategoryHint(catDirs,cat){
  const s=summarizeCapabilityDirs(catDirs);
  const roles=skillRoleSummaryText(s.roleCounts);
  if(cat==='active-user')return `${s.userSkills} skills${roles?` · ${roles}`:''}`;
  if(cat==='active-system')return `${s.systemSkills} skills${roles?` · ${roles}`:''}`;
  if(cat==='active-plugin')return `${s.pluginDirs} 插件 · ${s.pluginSkills} skills${roles?` · ${roles}`:''}`;
  if(cat==='active-connector')return `${s.connectorDirs} 连接器 · ${s.connectorSkills} skills${s.duplicateRuntimeSkills?` · ${s.duplicateRuntimeSkills} 同名已启用`:''}${roles?` · ${roles}`:''}`;
  if(cat==='source-cache')return `${s.cacheSkills} skills · 不等于上下文${roles?` · ${roles}`:''}`;
  if(cat==='source-catalog')return `${s.catalogSkills} skills · 只作来源${roles?` · ${roles}`:''}`;
  if(cat==='installed-disabled')return '已安装但未启用';
  if(cat==='commands')return `${s.commandCount} commands`;
  return '';
}

function renderSourceDirRow(t,safeId,padLeft){
  const subLabel=sourceSubLabel(t);
  const layerLabel=sourceLayerLabel(t);
  const runtime=sourceRuntimeBadge(t);
  const roleText=skillRoleSummaryText(t.skill_role_counts);
  const title=sourceDisplayTitle(t);
  const sub=sourceDisplaySub(t);
  const isCommands=t.type==='commands';
  const statusBits=[
    runtime,
    runtime?sourceMiniChip(layerLabel,(t.evidence||[]).join(' · ')||layerLabel):sourceMiniChip(layerLabel,(t.evidence||[]).join(' · ')||layerLabel),
    roleText?sourceMiniChip(roleText,'静态解析该目录下 SKILL.md 的角色：路由、工作流、指南、辅助等。'):'',
    t.loaded_elsewhere?sourceMiniChip('同名已启用','这份目录是市场目录，实际启用来自 installed plugin cache。'):'',
    subLabel?sourceMiniChip(subLabel):'',
  ].filter(Boolean).join('');
  return `<div style="border-top:1px solid var(--border-subtle)">
    <div class="target-opt source-dir-row${t.is_current?' active':''}" onclick="browseSourceDir('${safeId}','${esc(t.path)}',this)" style="padding:8px 14px 8px ${padLeft}px" title="${esc(t.path)}">
      <span class="to-scope ${t.scope==='global'?'to-global':'to-project'}">${isCommands?'⌨️':(t.scope==='global'?'🌐':'📁')}</span>
      <div class="source-dir-main">
        <div class="source-dir-titleline">
          <span class="source-dir-title">${esc(title)}</span>
          <span class="source-dir-badges">${statusBits}</span>
        </div>
        <div class="source-dir-sub">${esc(sub)}</div>
      </div>
      <span class="to-count">${t.count}</span>
      ${!t.is_current&&!isCommands?`<button class="btn btn-sm btn-primary" onclick="event.stopPropagation();switchTarget('${esc(t.path)}')" style="font-size:9px;padding:2px 6px;margin-left:4px;flex-shrink:0">切换为当前目录</button>`:''}
      <span class="src-arrow" style="font-size:8px;color:var(--text-muted);margin-left:4px">▶</span>
    </div>
    <div id="${safeId}" style="display:none;padding:4px 14px 8px ${padLeft+20}px;font-size:11px;color:var(--text-muted)">加载中...</div>
  </div>`;
}

// Lightweight refresh after delete: re-fetch targets, update counts in-place, preserve expand state
async function refreshAfterDelete(changedPaths){
  // Clear skill caches for changed paths
  (changedPaths||[]).forEach(p=>delete sourceSkillsCache[p]);
  // Re-fetch targets
  try{
    const d=await fetchTargets(true);
    const newTargets=d.targets||[];
    targets.length=0;
    targets.push(...newTargets);
    targetGroups.length=0;
    if(d.groups) targetGroups.push(...d.groups);
  }catch(e){console.error('refreshAfterDelete targets fail',e)}
  // Save current expand state
  const expandedAgents=new Set();
  document.querySelectorAll('.src-card').forEach(card=>{
    const arrow=card.querySelector('.src-arrow');
    if(arrow&&arrow.style.transform.includes('rotate')) expandedAgents.add(card.dataset.agent);
  });
  // Re-render
  renderSources();
  // Restore expand state
  document.querySelectorAll('.src-card').forEach(card=>{
    if(expandedAgents.has(card.dataset.agent)){
      const body=card.querySelector('.src-arrow').closest('div').nextElementSibling;
      if(body){body.style.display='block';card.querySelector('.src-arrow').style.transform='rotate(90deg)'}
    }
  });
}

function renderSources(){
  if(!targets.length){$('sources-list').innerHTML='';return}
  try{
  const curTarget=targets.find(t=>t.is_current);
  const visibleTargets=getVisibleSourceTargets();
  let h=`<div style="margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      <h3 style="font-size:15px;font-weight:600">📚 能力来源地图</h3>
      <span style="font-size:11px;color:var(--text-muted)">${targetGroups.length||'?'} 个应用 · ${visibleTargets.length}/${targets.length} 个目录</span>
      <span style="flex:1"></span>
      <button class="btn btn-sm btn-primary" onclick="showAddSourceDialog()">＋ 添加来源</button>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
      <div class="search-wrap" style="flex:1;min-width:200px;display:flex;gap:6px">
        <input class="search" id="global-skill-search" value="${escapeHtml(_globalSearchQuery)}" placeholder="跨所有目录搜索 skill 名称（回车搜索）..." oninput="onGlobalSearchInput(this.value)" onkeydown="if(event.key==='Enter'){event.preventDefault();doGlobalSearch(this.value)}" autocomplete="off" style="flex:1;min-width:0">
        <button class="btn btn-sm btn-primary" onclick="doGlobalSearch(document.getElementById('global-skill-search').value)" title="搜索">搜索</button>
      </div>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-size:11px;color:var(--text-muted)">当前: ${curTarget?curTarget.name:'-'}</span>
      <span style="flex:1"></span>
      <div class="segmented-control">
        <button class="btn btn-sm ${_sourceSortMode==='default'?'btn-primary':''}" onclick="setSourceSortMode('default')" title="按拖拽自定义顺序">默认排序</button>
        <button class="btn btn-sm ${_sourceSortMode==='skills'?'btn-primary':''}" onclick="setSourceSortMode('skills')" title="按 skill 数量降序">按 skills</button>
      </div>
      <div class="segmented-control">
        <button class="btn btn-sm ${_sourceViewMode==='active'?'btn-primary':''}" onclick="setSourceViewMode('active')" title="用户自建、系统内置、已启用插件、连接器和命令">当前可用</button>
        <button class="btn btn-sm ${_sourceViewMode==='inventory'?'btn-primary':''}" onclick="setSourceViewMode('inventory')" title="marketplace、缓存、旧包和已安装未启用包">来源库存</button>
        <button class="btn btn-sm ${_sourceViewMode==='review'?'btn-primary':''}" onclick="setSourceViewMode('review')" title="项目级、导入/副本和未知运行态目录">待复核</button>
        <button class="btn btn-sm ${_sourceViewMode==='all'?'btn-primary':''}" onclick="setSourceViewMode('all')" title="显示全部目录">全部</button>
      </div>
    </div>
  </div>`;
  if(_globalSearchQuery.length>=2){
    h+=`<div id="global-search-results">${renderGlobalSearchResultsHtml()}</div>`;
    $('sources-list').innerHTML=h;
    return;
  }
  const visibleGroups=getVisibleSourceGroups();
  if(visibleGroups.length){
    // Apply sort: default uses saved drag order; skills/dirs override it
    if(_sourceSortMode==='skills'){
      visibleGroups.sort((a,b)=>b.total_skills-a.total_skills);
    }else if(_sourceSortMode==='dirs'){
      visibleGroups.sort((a,b)=>b.dirs.length-a.dirs.length);
    }else if(categoryOrder.length){
      const ordered=[...visibleGroups];
      ordered.sort((a,b)=>{
        const ia=categoryOrder.indexOf(a.agent);
        const ib=categoryOrder.indexOf(b.agent);
        if(ia===-1&&ib===-1)return 0;
        if(ia===-1)return 1;
        if(ib===-1)return -1;
        return ia-ib;
      });
      visibleGroups.splice(0,visibleGroups.length,...ordered);
    }
    let groupsToRender=visibleGroups;
    if(!_sourcesShowAll&&visibleGroups.length>12){
      const priority=visibleGroups.filter(g=>g.dirs.some(t=>t.is_current));
      const used=new Set(priority.map(g=>g.agent));
      const rest=visibleGroups.filter(g=>!used.has(g.agent));
      groupsToRender=[...priority,...rest].slice(0,12);
      const hidden=visibleGroups.length-groupsToRender.length;
      h+=`<div class="notice-line"><span>默认显示当前和高频应用，共 ${groupsToRender.length} 个；其余 ${hidden} 个应用先收起。</span><button class="btn btn-sm" onclick="_sourcesShowAll=true;renderSources()">显示全部</button></div>`;
    }else if(_sourcesShowAll&&visibleGroups.length>12){
      h+=`<div class="notice-line"><span>已显示全部 ${visibleGroups.length} 个应用分组。</span><button class="btn btn-sm" onclick="_sourcesShowAll=false;renderSources()">回到重点</button></div>`;
    }
    groupsToRender.forEach(g=>{
      const isCurGroup=g.dirs.some(t=>t.is_current);
      const isExpanded=_expandedSourceAgent===g.agent;
      const gCap=summarizeCapabilityDirs(g.dirs);
      const gBits=[];
      if(gCap.activeSkills)gBits.push(`当前能力 ${gCap.activeSkills}`);
      if(gCap.sourceOnlySkills)gBits.push(`仅库存 ${gCap.sourceOnlySkills}`);
      const roleBits=skillRoleSummaryText(gCap.roleCounts);
      if(roleBits)gBits.push(roleBits);
      const profileHint=sourceGroupProfileHint(g);
      const gSub=`${g.dirs.length} 个目录 · ${formatSourceCounts(g.dirs)}${gBits.length?` · ${gBits.join(' · ')}`:''}${profileHint?` · ${profileHint}`:''}`;
      h+=`<div class="src-card" data-agent="${esc(g.agent)}" style="border:1px solid ${isCurGroup?'var(--accent)':'var(--border)'};border-radius:10px;margin-bottom:10px;background:var(--bg-card);overflow:hidden">
        <div style="display:flex;align-items:center;gap:8px;padding:12px 14px;transition:background .12s;${isCurGroup?'background:var(--accent-bg)':''}">
          <span class="src-arrow" style="font-size:10px;color:var(--text-muted);transition:transform .15s;cursor:pointer;${isExpanded?'transform:rotate(90deg)':''}" onclick="toggleSrcCard(this.closest('.src-card'))">▶</span>
          <span class="drag-handle" draggable="true" title="拖拽排序">⋮⋮</span>
          <div style="flex:1;min-width:0;cursor:pointer" onclick="toggleSrcCard(this.closest('.src-card'))">
            <div style="font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px">
              ${g.agent}
              ${isCurGroup?'<span class="to-scope to-global" style="font-size:9px">当前</span>':''}
              ${sourceReadinessBadge(g)}
            </div>
            <div style="font-size:10px;color:var(--text-muted)">${esc(gSub)}</div>
          </div>
        </div>
        <div style="display:${isExpanded?'block':'none'};border-top:1px solid var(--border)">`;
      if(!isExpanded){
        h+=`<div style="padding:10px 14px 10px 36px;font-size:11px;color:var(--text-muted);background:var(--bg-card-alt)">展开后加载 ${g.dirs.length} 个目录</div></div></div>`;
        return;
      }
      // Group dirs by runtime capability bucket within this agent.
      const catOrder=['active-user','active-system','active-plugin','active-connector','commands','installed-disabled','source-catalog','source-cache','review-copy','project-local','unknown'];
      const dirsByCat={};
      g.dirs.forEach(t=>{
        const c=sourceCapabilityBucket(t);
        if(!dirsByCat[c]) dirsByCat[c]=[];
        dirsByCat[c].push(t);
      });
      // Render category sub-groups
      const catKeys=catOrder.filter(c=>dirsByCat[c]);
      if(catKeys.length<=1){
        // Single or no category — show one collapsible category header + dirs
        const cat=catKeys[0]||'unknown';
        const cm=capabilityMeta(cat);
        const catCount=g.dirs.reduce((s,d)=>s+d.count,0);
        const catFoldId='cf-'+Math.random().toString(36).slice(2,8);
        const catHint=sourceCategoryHint(g.dirs,cat);
        h+=`<div style="border-top:1px solid var(--border-subtle)">
          <div style="padding:5px 14px 5px 36px;font-size:10px;font-weight:600;color:var(--text-muted);display:flex;align-items:center;gap:4px;background:var(--bg-card-alt);cursor:pointer;user-select:none" onclick="var b=document.getElementById('${catFoldId}');var s=b.style.display;b.style.display=s==='none'?'':'none';this.querySelector('.cat-arrow').style.transform=s==='none'?'rotate(90deg)':''">
            <span class="cat-arrow" style="font-size:8px;transition:transform .15s;transform:rotate(90deg)">▶</span>
            <span>${cm.emoji}</span><span>${cm.label}</span><span style="font-weight:400">(${g.dirs.length} 目录 · ${formatSourceCounts(g.dirs)})</span>
            ${catHint?`<span class="source-cat-hint">${esc(catHint)}</span>`:''}
          </div>
          <div id="${catFoldId}">`;
        g.dirs.forEach((t,di)=>{
          const safeId='sd-'+Math.random().toString(36).slice(2,8);
          h+=renderSourceDirRow(t,safeId,36);
        });
        h+=`</div></div>`;
      }else{
        // Multiple categories — render with category sub-headers
        catKeys.forEach(cat=>{
          const cm=capabilityMeta(cat);
          const catDirs=dirsByCat[cat];
          const catCount=catDirs.reduce((s,d)=>s+d.count,0);
          const catFoldId='cf-'+Math.random().toString(36).slice(2,8);
          const catHint=sourceCategoryHint(catDirs,cat);
          h+=`<div style="border-top:1px solid var(--border-subtle)">
            <div style="padding:5px 14px 5px 36px;font-size:10px;font-weight:600;color:var(--text-muted);display:flex;align-items:center;gap:4px;background:var(--bg-card-alt);cursor:pointer;user-select:none" onclick="var b=document.getElementById('${catFoldId}');var s=b.style.display;b.style.display=s==='none'?'':'none';this.querySelector('.cat-arrow').style.transform=s==='none'?'rotate(90deg)':''">
              <span class="cat-arrow" style="font-size:8px;transition:transform .15s">▶</span>
              <span>${cm.emoji}</span><span>${cm.label}</span><span style="font-weight:400">(${catDirs.length} 目录 · ${formatSourceCounts(catDirs)})</span>
              ${catHint?`<span class="source-cat-hint">${esc(catHint)}</span>`:''}
            </div>
            <div id="${catFoldId}">`;
          catDirs.forEach(t=>{
            const safeId='sd-'+Math.random().toString(36).slice(2,8);
            h+=renderSourceDirRow(t,safeId,52);
          });
          h+=`</div></div>`;
        });
      }
      h+=`</div></div>`;
    });
  }else{
    visibleTargets.forEach((t,di)=>{

      const safeId='fb-'+di;
      h+=renderSourceDirRow(t,safeId,10);
    });
  }
  $('sources-list').innerHTML=h;
  initSourceDrag();
  }catch(e){$('sources-list').innerHTML='<div class="empty">渲染出错: '+e.message+'</div>'}
}

/* ── Global skill search (explicit: Enter or 搜索 button) ── */
// Search runs only on Enter / 搜索 click. Typing never triggers a fetch or a
// re-render, so the input keeps focus. This fixes the old symptom where the
// 2nd keystroke lost focus (renderSources rebuilt the <input> node) and the
// user had to click back into the box to keep typing.
function updateSearchResultsPane(){
  const pane=document.getElementById('global-search-results');
  if(pane){pane.innerHTML=renderGlobalSearchResultsHtml();return true}
  return false;
}
function restoreSearchFocus(){
  // renderSources rebuilds the <input>; put focus back at the end so the user
  // can tweak the query and press Enter again without re-clicking the box.
  const inp=document.getElementById('global-skill-search');
  if(inp){inp.focus();const v=inp.value.length;try{inp.setSelectionRange(v,v)}catch(e){}}
}
function onGlobalSearchInput(value){
  // Explicit search only — just remember the value. No debounce, no render.
  _globalSearchQuery=value;
}
async function doGlobalSearch(q){
  q=(q||'').trim();
  if(q.length<2){toast('至少输入 2 个字符再搜索','error');restoreSearchFocus();return;}
  _globalSearchQuery=q;
  clearTimeout(_globalSearchTimer);
  const cached=_globalSearchCache[q];
  if(cached&&(Date.now()-cached.ts)<GLOBAL_SEARCH_CACHE_TTL){
    _globalSearchResults=cached.data;
    if(!updateSearchResultsPane()) renderSources();
    restoreSearchFocus();
    return;
  }
  _globalSearchResults=null;
  renderSources();  // switch to search view + show "搜索中..."
  try{
    const r=await fetch('/api/search-skills?q='+encodeURIComponent(q)+'&limit=50');
    const d=await r.json();
    _globalSearchResults=d;
    _globalSearchCache[q]={data:d,ts:Date.now()};
    updateSearchResultsPane();
  }catch(e){
    _globalSearchResults={error:'搜索失败',groups:[]};
    updateSearchResultsPane();
  }
  restoreSearchFocus();
}
function renderGlobalSearchResultsHtml(){
  if(!_globalSearchResults)return '<div style="padding:20px;text-align:center;color:var(--text-muted)">搜索中...</div>';
  if(_globalSearchResults.error){
    return '<div style="padding:20px;text-align:center;color:var(--red)">'+escapeHtml(_globalSearchResults.error)+'</div>';
  }
  const groups=_globalSearchResults.groups||[];
  if(!groups.length){
    return '<div style="padding:20px;text-align:center;color:var(--text-muted)">未找到匹配的 skill</div>';
  }
  let h=`<div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
    <span style="font-size:12px;color:var(--text-muted)">找到 ${_globalSearchResults.total_matches||0} 个匹配（显示 ${_globalSearchResults.returned||0} 个）</span>
    <span style="flex:1"></span>
    <button class="btn btn-sm" onclick="_globalSearchQuery='';_globalSearchResults=null;renderSources()">清除搜索</button>
  </div>`;
  groups.forEach(g=>{
    h+=`<div class="src-card" style="border:1px solid var(--border);border-radius:10px;margin-bottom:10px;background:var(--bg-card);overflow:hidden">
      <div style="padding:10px 14px;font-size:12px;font-weight:600;background:var(--bg-card-alt);border-bottom:1px solid var(--border-subtle)">
        ${esc(g.agent)} · ${g.skills.length} 个匹配
      </div>
      <div>`;
    g.skills.forEach(s=>{
      const dirPath=s.dir;
      const skillName=s.name;
      const role=s.skill_role_label?`<span class="source-mini-chip" title="${esc((s.skill_role_evidence||[]).join(' · ')||'静态角色识别')}">${esc(s.skill_role_label)}</span>`:'';
      h+=`<div style="display:flex;align-items:center;gap:8px;padding:8px 14px;border-bottom:1px solid var(--border-subtle);cursor:pointer" onclick="showSkill('${esc(skillName)}','${esc(dirPath)}')" title="${esc(s.rel+'/'+s.name)}">
        <span style="font-size:12px;font-weight:500;color:var(--accent);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.name)}</span>
        <span style="font-size:10px;color:var(--text-muted);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.rel)}</span>
        ${role}
        <span style="font-size:9px;color:var(--text-dim);text-transform:uppercase">${esc(s.category||'')}</span>
      </div>`;
    });
    h+=`</div></div>`;
  });
  return h;
}

// --- Source card toggle + drag-and-drop ---
let categoryOrder=[];
async function loadCategoryOrder(){
  try{categoryOrder=await fetch('/api/category-order').then(r=>r.json())}catch{categoryOrder=[]}
  if(!Array.isArray(categoryOrder))categoryOrder=[];
}
loadCategoryOrder();

// Smart folding: hide extra dirs per agent card, show top N + current + favorites
function applySmartFolding(){
  const MAX_SHOW=3;
  document.querySelectorAll('.src-card').forEach(card=>{
    const body=card.querySelector('.src-arrow').closest('div').nextElementSibling;
    if(!body||body.dataset.folded)return;
    // Collect all direct directory rows (target-opt or border-top dividers)
    const rows=[];
    body.querySelectorAll(':scope > div').forEach(row=>{
      // Skip if it's our own toggle button
      if(row.dataset.moreToggle)return;
      // Category sub-headers count as a row but we keep them
      const isDirRow=row.querySelector('.target-opt');
      const isCatHeader=!isDirRow&&row.querySelector('div[style*="font-weight:600"]');
      if(isDirRow||isCatHeader) rows.push({el:row,isDir:!!isDirRow});
    });
    if(rows.length<=MAX_SHOW+1){body.dataset.folded='1';return}
    // Determine which to keep visible: current target, favorites, first N
    let shown=0;
    const visible=[],hidden=[];
    rows.forEach(r=>{
      if(!r.isDir){visible.push(r);return} // keep category headers
      const opt=r.el.querySelector('.target-opt');
      const isCurrent=opt&&opt.classList.contains('active');
      if(isCurrent||shown<MAX_SHOW){visible.push(r);shown++}
      else hidden.push(r);
    });
    if(!hidden.length){body.dataset.folded='1';return}
    // Hide extras
    hidden.forEach(r=>{r.el.style.display='none'});
    // Insert toggle button
    const totalSkills=hidden.reduce((s,r)=>{
      const cnt=r.el.querySelector('.to-count');
      return s+(cnt?parseInt(cnt.textContent)||0:0);
    },0);
    const btn=document.createElement('div');
    btn.dataset.moreToggle='1';
    btn.style.cssText='border-top:1px solid var(--border-subtle);padding:6px 14px 6px 36px;cursor:pointer;font-size:11px;color:var(--text-muted);display:flex;align-items:center;gap:4px;user-select:none';
    btn.innerHTML='<span style="font-size:8px;transition:transform .15s">▶</span><span>还有 '+hidden.length+' 个目录</span><span style="font-size:9px;color:var(--text-dim)">('+totalSkills+' items)</span>';
    btn.onclick=function(){
      const expanded=hidden[0].el.style.display!=='none';
      hidden.forEach(r=>{r.el.style.display=expanded?'none':''});
      this.children[0].style.transform=expanded?'':'rotate(90deg)';
      this.children[1].textContent=expanded?'还有 '+hidden.length+' 个目录':'收起';
    };
    body.appendChild(btn);
    body.dataset.folded='1';
  });
}

function toggleSrcCard(card){
  const agent=card.dataset.agent;
  _expandedSourceAgent=_expandedSourceAgent===agent?null:agent;
  renderSources();
}

function initSourceDrag(){
  const container=$('sources-list');
  if(!container)return;
  container.querySelectorAll('.src-card').forEach(card=>{
    // Drag only from handle, not from card body (fixes text selection conflict)
    const handle=card.querySelector('.drag-handle');
    if(handle){
      handle.addEventListener('dragstart',e=>{
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain',card.dataset.agent||'');
      card.classList.add('dragging');
      requestAnimationFrame(()=>card.style.opacity='0.4');
    });
    }
    card.addEventListener('dragover',e=>{
      e.preventDefault();
      e.dataTransfer.dropEffect='move';
      if(!card.classList.contains('dragging'))card.classList.add('drag-over');
    });
    card.addEventListener('dragleave',()=>{
      card.classList.remove('drag-over');
    });
    card.addEventListener('drop',e=>{
      e.preventDefault();
      const dragging=document.querySelector('.src-card.dragging');
      if(dragging&&card!==dragging){
        // Determine position: insert before or after based on mouse Y
        const rect=card.getBoundingClientRect();
        const midY=rect.top+rect.height/2;
        if(e.clientY<midY){
          card.parentNode.insertBefore(dragging,card);
        }else{
          card.parentNode.insertBefore(dragging,card.nextSibling);
        }
        saveCategoryOrder();
      }
      card.classList.remove('drag-over');
    });
    card.addEventListener('dragend',()=>{
      card.classList.remove('dragging');
      card.style.opacity='';
      document.querySelectorAll('.src-card.drag-over').forEach(c=>c.classList.remove('drag-over'));
    });
  });
}

function saveCategoryOrder(){
  const cards=document.querySelectorAll('#sources-list .src-card');
  categoryOrder=Array.from(cards).map(c=>c.dataset.agent);
  fetch('/api/category-order',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(categoryOrder)
  });
}

async function browseSourceDir(safeId,path,head){
  const container=$(safeId);
  if(!container)return;
  const arrow=head.querySelector('.src-arrow');
  const isOpen=container.style.display==='block';
  if(isOpen){container.style.display='none';if(arrow)arrow.style.transform='';return}
  container.style.display='block';
  if(arrow)arrow.style.transform='rotate(90deg)';
  // Load and render skills in this directory
  if(sourceSkillsCache[path]){
    renderBrowseDir(path,sourceSkillsCache[path],container);
    return;
  }
  container.innerHTML='<div style="padding:6px;color:var(--text-muted)">加载中...</div>';
  try{
    const r=await fetch('/api/source/skills?path='+encodeURIComponent(path));
    const d=await r.json();
    const list=d.skills||d;
    sourceSkillsCache[path]=list;
    renderBrowseDir(path,list,container);
  }catch(e){container.innerHTML='<div style="padding:6px;color:var(--red)">加载失败</div>'}
}

let srcSelectedSkills=new Set();
let copyMode='symlink';
function setCopyMode(mode){copyMode=mode;}
function renderBrowseDir(path,itemList,container){
  const installed=new Set(skills.map(s=>s.name));
  const curTarget=targets.find(t=>t.is_current);
  const isCurrentTarget=curTarget&&path===curTarget.path;
  const isCommands=itemList.length>0&&itemList[0].kind==='command';
  let h='';
  if(!itemList.length){h='<div style="padding:6px;color:var(--text-muted)">空目录</div>';container.innerHTML=h;return}
  // Batch actions bar
  const bId=esc(path).replace(/[^a-z0-9]/gi,'');
  h+=`<div style="display:flex;gap:6px;align-items:center;margin-bottom:6px;padding:2px 0;flex-wrap:wrap">
    <label style="font-size:10px;color:var(--text-dim);display:flex;align-items:center;gap:3px;cursor:pointer">
      <input type="checkbox" id="src-selall-${bId}" onchange="toggleAllSrcSkills(this.checked)" style="cursor:pointer"> 全选
    </label>
    <select id="src-copy-mode-${bId}" onchange="setCopyMode(this.value)" title="同步方式：链接保持单一真相源，复制生成独立副本" style="font-size:10px;padding:1px 4px;border-radius:4px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
      <option value="symlink" ${copyMode==='symlink'?'selected':''}>🔗 链接</option>
      <option value="copy" ${copyMode==='copy'?'selected':''}>📄 复制</option>
    </select>
    ${!isCurrentTarget&&!isCommands?`<button class="btn btn-sm btn-primary" id="src-batch-sync-${bId}" onclick="batchSyncSrcSkills()" disabled style="font-size:9px;padding:2px 6px">批量同步到当前目录</button>`:''}
    ${!isCommands?`<button class="btn btn-sm btn-danger" id="src-batch-del-${bId}" onclick="batchDeleteSrcSkills()" disabled style="font-size:9px;padding:2px 6px">批量删除</button>`:''}
    <span style="font-size:10px;color:var(--text-muted)" id="src-sel-count-${bId}"></span>
  </div>`;
  // Local search within this directory
  h+=`<div style="margin:4px 0 8px">
    <div class="search-wrap" style="flex:1">
      <input class="search" id="local-search-${bId}" placeholder="在此目录内搜索 skill..." oninput="filterBrowseDir('${esc(path)}',this.value)" autocomplete="off" style="max-width:100%;font-size:12px;padding:5px 10px 5px 28px">
    </div>
  </div>`;
  h+=`<div id="browse-list-${bId}">`;
  itemList.forEach(s=>{
    const isInstalled=installed.has(s.name);
    const selKey=path+'::'+s.name;
    const isBroken=s.kind==='broken_symlink'||s.kind==='broken_skill_link';
    const kindLabel=s.kind==='broken_symlink'?'断链':(s.kind==='broken_skill_link'?'目录壳':'');
    const roleLabel=!isCommands&&s.skill_role_label?sourceMiniChip(s.skill_role_label,(s.skill_role_evidence||[]).join(' · ')||'静态角色识别'):'';
    const itemDesc=s.description?`<div style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(s.description)}</div>`:'';
    h+=`<div style="display:flex;align-items:center;gap:6px;padding:3px 0;${isInstalled?'color:var(--green)':''}">
      <input type="checkbox" class="src-skill-check" data-path="${esc(path)}" data-name="${esc(s.name)}" ${srcSelectedSkills.has(selKey)?'checked':''} onchange="toggleSrcSkill(this)" style="cursor:pointer">
      <span style="flex:1;min-width:0;overflow:hidden;${isBroken?'color:var(--text-muted)':'cursor:pointer;color:var(--accent)'}" onclick="${isCommands||isBroken?'':`showSkill('${esc(s.name)}','${esc(path)}')`}"><span style="display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.name}</span>${itemDesc}</span>
      ${roleLabel}
      ${kindLabel?`<span style="font-size:9px;color:var(--red);border:1px solid color-mix(in srgb,var(--red) 35%,transparent);border-radius:999px;padding:1px 5px">${kindLabel}</span>`:''}
      ${!isCurrentTarget&&!isInstalled&&!isCommands?`<button class="btn btn-sm" onclick="stealFromSource('${esc(path)}','${esc(s.name)}')" title="以 ${copyMode==='symlink'?'链接':'复制'} 方式同步到当前目录" style="font-size:9px;padding:2px 6px">同步到当前目录</button>`:''}
      ${isInstalled&&!isCommands?'<span style="font-size:9px;color:var(--text-muted)">已安装</span>':''}
      ${!isCommands?`<button class="btn btn-sm btn-danger" onclick="deleteSrcSkill('${esc(path)}','${esc(s.name)}')" style="font-size:9px;padding:2px 5px" title="删除此 skill">🗑</button>`:''}
    </div>`;
  });
  h+=`</div>`;
  container.innerHTML=h;
  updateSrcBatchUI();
}
function filterBrowseDir(path, query){
  const bId=esc(path).replace(/[^a-z0-9]/gi,'');
  const list=$(`browse-list-${bId}`);
  if(!list)return;
  const q=query.toLowerCase().trim();
  const rows=list.children;
  for(const row of rows){
    const checkbox=row.querySelector('.src-skill-check');
    if(!checkbox)continue;
    const name=(checkbox.dataset.name||'').toLowerCase();
    row.style.display=(!q||name.includes(q))?'flex':'none';
  }
}

function toggleSrcSkill(el){
  const path=el.dataset.path,name=el.dataset.name;
  const key=path+'::'+name;
  if(el.checked)srcSelectedSkills.add(key);else srcSelectedSkills.delete(key);
  updateSrcBatchUI();
}
function toggleAllSrcSkills(checked){
  document.querySelectorAll('.src-skill-check').forEach(c=>{
    c.checked=checked;
    const key=c.dataset.path+'::'+c.dataset.name;
    if(checked)srcSelectedSkills.add(key);else srcSelectedSkills.delete(key);
  });
  updateSrcBatchUI();
}
function updateSrcBatchUI(){
  const sel=[...srcSelectedSkills];
  // Find the batch buttons by looking for any src-batch-del id
  const first=document.querySelector('[id^="src-batch-del-"]');
  if(!first)return;
  const bId=first.id.replace('src-batch-del-','');
  const delBtn=document.getElementById('src-batch-del-'+bId);
  const syncBtn=document.getElementById('src-batch-sync-'+bId);
  const cnt=document.getElementById('src-sel-count-'+bId);
  if(delBtn)delBtn.disabled=sel.length===0;
  if(syncBtn)syncBtn.disabled=sel.length===0;
  if(cnt)cnt.textContent=sel.length?`${sel.length} 个已选`:'';
}

async function deleteSrcSkill(path,name){
  if(!confirm(`确认删除 ${name}？将移入垃圾站`))return;
  try{
    const r=await fetch(`/api/skill/${encodeURIComponent(name)}?target=${encodeURIComponent(path)}`,{method:'DELETE'});
    const d=await r.json();
    if(d.ok){toast(`${name} 已移入垃圾站`);delete sourceSkillsCache[path];clearGlobalSearchCache();refreshAfterDelete([path]);loadTrash()}
    else{toast(d.error||'删除失败','error')}
  }catch(e){toast('删除失败','error')}
}
async function batchDeleteSrcSkills(){
  const sel=[...srcSelectedSkills].map(k=>({path:k.split('::')[0],name:k.split('::').slice(1).join('::')}));
  if(!sel.length)return;
  if(!confirm(`确认删除 ${sel.length} 个 skill？将移入垃圾站`))return;
  let ok=0,fail=0;
  for(const{name,path}of sel){
    try{const r=await fetch(`/api/skill/${encodeURIComponent(name)}?target=${encodeURIComponent(path)}`,{method:'DELETE'});const d=await r.json();d.ok?ok++:fail++}
    catch{fail++}
    srcSelectedSkills.delete(path+'::'+name);
  }
  toast(`${ok} 个已移入垃圾站${fail?`，${fail} 个失败`:''}`);
  const _paths=new Set(sel.map(s=>s.path));
  _paths.forEach(p=>delete sourceSkillsCache[p]);
  clearGlobalSearchCache();
  refreshAfterDelete([..._paths]);loadTrash();
}
async function batchSyncSrcSkills(){
  const sel=[...srcSelectedSkills].map(k=>({path:k.split('::')[0],name:k.split('::').slice(1).join('::')}));
  if(!sel.length)return;
  const curTarget=targets.find(t=>t.is_current);
  if(!curTarget)return toast('未选择目标目录','error');
  const modeLabel=copyMode==='symlink'?'链接':'复制';
  if(!confirm(`确认以「${modeLabel}」方式将 ${sel.length} 个 skill 同步到当前目录？\n目标: ${curTarget.name}\n\n${sel.map(s=>s.name).join(', ')}`))return;
  let ok=0,fail=0;
  for(const{name,path}of sel){
    try{
      const r=await fetch('/api/copy-skill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({src:path+'/'+name,target:curTarget.path,name,mode:copyMode})});
      const d=await r.json();d.ok?ok++:fail++;
    }catch{fail++}
  }
  toast(`已${modeLabel}到当前目录 ${ok} 个${fail?`，${fail} 个失败`:''}`);
  srcSelectedSkills.clear();
  invalidateTargetsCache();
  clearGlobalSearchCache();
  await loadData();
}

async function stealFromSource(srcPath,skillName){
  const srcDir=srcPath+'/'+skillName;
  const curTarget=targets.find(t=>t.is_current);
  if(!curTarget)return toast('未选择目标库','error');
  try{
    const r=await fetch('/api/copy-skill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({src:srcDir,target:curTarget.path,name:skillName,mode:copyMode})});
    const d=await r.json();
    if(d.ok){toast(`${skillName} 已${d.mode==='symlink'?'链接':'复制'}到目标库`);invalidateTargetsCache();clearGlobalSearchCache();await loadData()}
    else{toast(d.error||'同步失败','error')}
  }catch(e){toast('同步失败','error')}
}

function toggleSourceCard(path,head){
  const body=head.nextElementSibling;
  const arrow=head.querySelector('.src-arrow');
  const isOpen=body.style.display==='block';
  if(isOpen){body.style.display='none';arrow.style.transform='';return}
  body.style.display='block';arrow.style.transform='rotate(90deg)';
  const safeId='src-body-'+encodeURIComponent(path).replace(/[^a-zA-Z0-9]/g,'').slice(0,20);
  const container=$(safeId);
  if(!container)return;
  // Load skills
  loadAndRenderSourceSkills(path,container);
}

async function loadAndRenderSourceSkills(path,container){
  if(sourceSkillsCache[path]){
    renderSourceSkillList(path,sourceSkillsCache[path],container);
    return;
  }
  container.innerHTML='<div style="padding:10px;color:var(--text-muted)">加载中...</div>';
  try{
    const r=await fetch('/api/source/skills?path='+encodeURIComponent(path));
    const d=await r.json();
    if(d.error){container.innerHTML='<div style="padding:10px;color:var(--red)">'+d.error+'</div>';return}
    sourceSkillsCache[path]=d.skills||[];
    renderSourceSkillList(path,d.skills||[],container);
  }catch(e){
    container.innerHTML='<div style="padding:10px;color:var(--red)">加载失败</div>';
  }
}

function renderSourceSkillList(path,sourceSkills,container){
  const installedNames=new Set(skills.map(s=>s.name));
  const safePath=path.replace(/'/g,"\\'");
  if(!sourceSkills.length){container.innerHTML='<div style="padding:10px;color:var(--text-muted)">无 skills</div>';return}
  let h='<div style="max-height:300px;overflow-y:auto">';
  sourceSkills.forEach(sk=>{
    const isInstalled=installedNames.has(sk.name);
    const summary=safeDesc(sk.understanding?.summary_zh||sk.description||'暂无中文理解');
    const tags=[...understandingLabels(sk.understanding?.scenarios,2),...understandingLabels(sk.understanding?.capabilities,2)].slice(0,3);
    const roleLabel=sk.skill_role_label?`<span class="source-mini-chip" title="${esc((sk.skill_role_evidence||[]).join(' · ')||'静态角色识别')}">${esc(sk.skill_role_label)}</span>`:'';
    h+=`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 30%,transparent)">
      <span style="font-size:11px;${isInstalled?'color:var(--green)':'color:var(--text-muted)'};min-width:60px">${isInstalled?'✓ 已安装':'○ 未安装'}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500;cursor:pointer;color:var(--indigo)" onclick="showSkill('${esc(sk.name)}','${esc(path)}')">${sk.name}</div>
        <div style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(summary)}">${escapeHtml(summary)}</div>
        ${tags.length?`<div class="skill-tags">${tags.map(t=>`<span class="skill-tag">${escapeHtml(t)}</span>`).join('')}</div>`:''}
      </div>
      ${roleLabel}
    </div>`;
  });
  h+='</div>';
  container.innerHTML=h;
}

let sourceSyncSelections=new Set();
function showSourceSkills(path,name){
  sourceSyncSelections.clear();
  $('modal-title').textContent=name+' — Skills';
  $('modal-body').innerHTML='<div style="padding:20px;text-align:center;color:var(--text-muted)">加载中...</div>';
  $('modal').classList.remove('hidden');
  loadSourceSkillsForSync(path);
}

async function loadSourceSkillsForSync(path){
  try{
    const r=await fetch('/api/source/skills?path='+encodeURIComponent(path));
    const d=await r.json();
    if(d.error){$('modal-body').innerHTML='<div style="color:var(--red)">'+d.error+'</div>';return}
    const installedNames=new Set(skills.map(s=>s.name));
    const sourceSkills=d.skills||[];
    if(!sourceSkills.length){$('modal-body').innerHTML='<div class="empty">无 skills</div>';return}

    let h=`<div style="font-family:-apple-system,sans-serif;font-size:13px">
      <div style="display:flex;gap:10px;margin-bottom:12px;align-items:center">
        <label style="font-size:12px;color:var(--text-dim);display:flex;align-items:center;gap:4px;cursor:pointer">
          <input type="checkbox" id="sync-select-all" onchange="toggleSyncSelectAll()" style="cursor:pointer">
          全选未安装
        </label>
        <button class="btn btn-sm btn-primary" id="sync-btn" onclick="syncSelected()" disabled>同步到目标库</button>
        <span id="sync-count" style="font-size:11px;color:var(--text-muted)">已选 0 个</span>
      </div>
      <div style="max-height:50vh;overflow-y:auto;border:1px solid var(--border);border-radius:8px">`;
    sourceSkills.forEach(sk=>{
      const isInstalled=installedNames.has(sk.name);
      const summary=safeDesc(sk.understanding?.summary_zh||sk.description||'暂无中文理解');
      h+=`<div style="display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:1px solid color-mix(in srgb,var(--border) 30%,transparent);${isInstalled?'opacity:.5':''}">
        <input type="checkbox" class="sync-check" data-name="${sk.name}" ${isInstalled?'disabled':''} onchange="toggleSyncSkill('${sk.name}')">
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:500">${sk.name} ${isInstalled?'<span style="font-size:10px;color:var(--green)">(已安装)</span>':''}</div>
          <div style="font-size:10px;color:var(--text-muted)">${escapeHtml(summary)}</div>
        </div>
      </div>`;
    });
    h+='</div></div>';
    $('modal-body').innerHTML=h;
    updateSyncUI();
  }catch(e){$('modal-body').innerHTML='<div style="color:var(--red)">加载失败</div>'}
}

function toggleSyncSkill(name){
  if(sourceSyncSelections.has(name))sourceSyncSelections.delete(name);else sourceSyncSelections.add(name);
  updateSyncUI();
}
function toggleSyncSelectAll(){
  const checked=$('sync-select-all').checked;
  document.querySelectorAll('.sync-check:not(:disabled)').forEach(cb=>{
    cb.checked=checked;
    const name=cb.dataset.name;
    checked?sourceSyncSelections.add(name):sourceSyncSelections.delete(name);
  });
  updateSyncUI();
}
function updateSyncUI(){
  const count=sourceSyncSelections.size;
  $('sync-count').textContent=`已选 ${count} 个`;
  const btn=$('sync-btn');if(btn)btn.disabled=count===0;
}

async function syncSelected(){
  const names=[...sourceSyncSelections];
  if(!names.length)return;
  if(!confirm(`确认同步 ${names.length} 个 skill 到目标库？`))return;
  // We need source URLs. For skills without upstream, we can't sync.
  // Try to find upstream for each, or use name as search key
  let ok=0,fail=0;
  const btn=$('sync-btn');if(btn){btn.disabled=true;btn.textContent='同步中...'}
  for(const name of names){
    // Try to find GitHub source from upstream_sources
    const upstream=(health?.upstream_sources||[]).find(u=>u.name===name);
    const source=upstream?`https://github.com/${upstream.repo}`:name;
    try{
      const r=await fetch('/api/steal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source})});
      const d=await r.json();
      d.ok?ok++:fail++;
    }catch{fail++}
  }
  if(btn){btn.disabled=false;btn.textContent='同步到目标库'}
  toast(`同步完成: ${ok} 成功${fail?`, ${fail} 失败`:''}`);
  sourceSyncSelections.clear();
  invalidateTargetsCache();
  clearGlobalSearchCache();
  await loadData();
  $('modal').classList.add('hidden');
}

/* ── Custom Sources ── */
function showAddSourceDialog(){
  $('modal-title').textContent='添加技能库来源';
  $('modal-body').innerHTML=`<div style="font-family:-apple-system,sans-serif;font-size:13px;color:var(--text)">
    <details style="margin-bottom:10px;border:1px solid var(--border-subtle);border-radius:8px;overflow:hidden">
      <summary style="padding:8px 10px;font-size:12px;font-weight:600;cursor:pointer;background:var(--bg-card-alt);color:var(--text)">怎么找到 skill 目录路径？</summary>
      <div style="padding:10px;font-size:11px;color:var(--text-muted);line-height:1.7">
        <div style="margin-bottom:10px">
          <b style="color:var(--text)">👤 你自己找</b><br>
          Agent 的 skill 目录通常在 <code style="background:var(--bg-card);padding:1px 4px;border-radius:3px">~/.xxx/skills/</code>，比如：<br>
          <code style="background:var(--bg-card);padding:1px 4px;border-radius:3px">~/.claude/skills</code>、
          <code style="background:var(--bg-card);padding:1px 4px;border-radius:3px">~/.augment/skills</code>、
          <code style="background:var(--bg-card);padding:1px 4px;border-radius:3px">~/.cursor/skills-cursor</code><br>
          在终端 <code style="background:var(--bg-card);padding:1px 4px;border-radius:3px">ls ~/.xxx/skills/</code> 看看有没有，有就复制路径粘贴到下面。
        </div>
        <div>
          <b style="color:var(--text)">🤖 让 Agent 帮你找</b><br>
          复制下面这段话发给你的 Agent：<br>
          <div style="margin:4px 0;padding:6px 8px;background:var(--bg-card);border-radius:6px;font-family:monospace;white-space:pre-wrap;user-select:text;cursor:text;border:1px solid var(--border-subtle)">请列出你所有的 skill 目录的完整路径，每行一个，只输出路径不要其他内容。格式示例：/Users/你的用户名/.claude/skills</div>
          把 Agent 返回的路径粘贴到下面即可。
        </div>
      </div>
    </details>
    <textarea id="add-source-path" rows="5" placeholder="粘贴路径，一行一个：\n~/.my-agent/skills\n/Users/you/custom-skills\n~/work/project-a/.claude/skills" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;font-family:monospace;margin-bottom:12px;resize:vertical"></textarea>
    <div id="add-source-result" style="display:none;padding:10px;border-radius:8px;margin-bottom:8px;font-size:12px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn" onclick="$('modal').classList.add('hidden')">取消</button>
      <button class="btn btn-primary" id="add-source-btn" onclick="addCustomSource()">批量添加</button>
    </div>
  </div>`;
  $('modal').classList.remove('hidden');
}
async function addCustomSource(){
  const btn=$('add-source-btn');const result=$('add-source-result');
  const raw=$('add-source-path').value.trim();
  if(!raw){toast('请输入路径','error');return}
  const paths=raw.split('\n').map(p=>p.trim()).filter(Boolean);
  if(!paths.length){toast('请输入路径','error');return}
  btn.disabled=true;btn.textContent='添加中...';
  result.style.display='block';result.style.background='var(--bg-card-alt)';result.style.color='var(--text-muted)';
  result.textContent=`正在验证 ${paths.length} 个路径...`;
  let ok=0,fail=0,details=[];
  for(const path of paths){
    try{
      const r=await fetch('/api/custom-sources',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
      const d=await r.json();
      if(d.ok){
        if(d.skipped){details.push('⏭️ '+path+' — 已存在');ok++}
        else{ok++;details.push('✅ '+path)}
      }
      else{fail++;details.push('❌ '+path+' — '+(d.error||'失败'))}
    }catch(e){fail++;details.push('❌ '+path+' — '+e.message)}
  }
  if(fail===0){
    result.style.background='var(--green-bg)';result.style.color='var(--green)';
    result.innerHTML=`全部添加成功 ✅（${ok} 个）`;
    toast(`已添加 ${ok} 个来源`);
    invalidateTargetsCache();
    clearGlobalSearchCache();
    await loadData();
    setTimeout(()=>$('modal').classList.add('hidden'),1200);
  }else{
    result.style.background='var(--bg-card-alt)';result.style.color='var(--text)';
    result.innerHTML=`<div>成功 ${ok} 个，失败 ${fail} 个</div><div style="margin-top:6px;font-size:11px;color:var(--text-muted)">${details.join('<br>')}</div>`;
    if(ok>0){invalidateTargetsCache();clearGlobalSearchCache();await loadData();}
  }
  btn.disabled=false;btn.textContent='批量添加';
}
async function removeCustomSource(path){
  if(!confirm(`确认移除来源 "${path}"？`))return;
  try{
    const r=await fetch('/api/custom-sources?path='+encodeURIComponent(path),{method:'DELETE'});
    const d=await r.json();
    if(d.ok){toast('来源已移除');invalidateTargetsCache();clearGlobalSearchCache();await loadData()}
    else toast(d.error||'移除失败','error');
  }catch(e){toast('移除失败','error')}
}
