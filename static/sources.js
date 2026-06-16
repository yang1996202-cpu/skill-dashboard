
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
    loaded:{label:'已加载',cls:'loaded',desc:'当前宿主 enabledPlugins 已启用，且匹配 installed_plugins 安装路径。'},
    installed:{label:'已安装未启用',cls:'installed',desc:'installed_plugins 里有记录，但当前没有启用。'},
    catalog:{label:t?.loaded_elsewhere?'市场目录 · 同名已启用':'市场目录',cls:'catalog',desc:'marketplace 货架目录，不等于当前上下文已加载。'},
    orphaned:{label:'旧包缓存',cls:'orphaned',desc:'同一插件的旧版本缓存，通常不是当前加载对象。'},
    stale:{label:'非当前安装包',cls:'orphaned',desc:'同名插件另有当前安装路径，此目录只是遗留副本。'},
    cache:{label:'插件包缓存',cls:'cache',desc:'位于插件缓存区，未匹配到当前安装记录。'},
  }[state];
  if(!meta)return '';
  return `<span class="source-status ${meta.cls}" title="${esc(t.runtime_reason||meta.desc)}">${meta.label}</span>`;
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
  const parts=[];
  if(skillCount)parts.push(`${skillCount} skills`);
  if(cmdCount)parts.push(`${cmdCount} commands`);
  return parts.join(' · ')||'0 项';
}

function sourceCategoryHint(catDirs,cat){
  const loaded=catDirs.filter(t=>t.runtime_state==='loaded').length;
  const installed=catDirs.filter(t=>t.runtime_state==='installed').length;
  const catalog=catDirs.filter(t=>t.runtime_state==='catalog').length;
  const stale=catDirs.filter(t=>['orphaned','stale','cache'].includes(t.runtime_state)).length;
  const parts=[];
  if(loaded)parts.push(`已加载 ${loaded}`);
  if(installed)parts.push(`已安装未启用 ${installed}`);
  if(catalog)parts.push(`市场目录 ${catalog}`);
  if(stale)parts.push(`缓存/旧包 ${stale}`);
  if(parts.length)return parts.join(' · ');
  if(cat==='marketplace')return '只解释来源，默认不删除';
  if(cat==='cache')return '缓存、备份和样例默认收起';
  return '';
}

function renderSourceDirRow(t,safeId,padLeft){
  const subLabel=sourceSubLabel(t);
  const layerLabel=sourceLayerLabel(t);
  const runtime=sourceRuntimeBadge(t);
  const title=sourceDisplayTitle(t);
  const sub=sourceDisplaySub(t);
  const isCommands=t.type==='commands';
  const statusBits=[
    runtime,
    runtime?sourceMiniChip(layerLabel,(t.evidence||[]).join(' · ')||layerLabel):sourceMiniChip(layerLabel,(t.evidence||[]).join(' · ')||layerLabel),
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
      <h3 style="font-size:15px;font-weight:600">📚 全部目录技能</h3>
      <span style="font-size:11px;color:var(--text-muted)">${targetGroups.length||'?'} 个应用 · ${visibleTargets.length}/${targets.length} 个目录</span>
      <span style="flex:1"></span>
      <button class="btn btn-sm btn-primary" onclick="showAddSourceDialog()">＋ 添加来源</button>
    </div>
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-size:11px;color:var(--text-muted)">当前: ${curTarget?curTarget.name:'-'}</span>
      <span style="flex:1"></span>
      <div class="segmented-control">
        <button class="btn btn-sm ${_sourceSortMode==='default'?'btn-primary':''}" onclick="setSourceSortMode('default')" title="按拖拽自定义顺序">默认排序</button>
        <button class="btn btn-sm ${_sourceSortMode==='skills'?'btn-primary':''}" onclick="setSourceSortMode('skills')" title="按 skill 数量降序">按 skills</button>
      </div>
      <div class="segmented-control">
        <button class="btn btn-sm ${_sourceViewMode==='daily'?'btn-primary':''}" onclick="setSourceViewMode('daily')" title="只显示日常整理目录">日常视图</button>
        <button class="btn btn-sm ${_sourceViewMode==='deep'?'btn-primary':''}" onclick="setSourceViewMode('deep')" title="显示 marketplace、缓存、内置包等全部目录">全量审计</button>
      </div>
    </div>
  </div>`;
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
      h+=`<div class="src-card" data-agent="${esc(g.agent)}" style="border:1px solid ${isCurGroup?'var(--accent)':'var(--border)'};border-radius:10px;margin-bottom:10px;background:var(--bg-card);overflow:hidden">
        <div style="display:flex;align-items:center;gap:8px;padding:12px 14px;transition:background .12s;${isCurGroup?'background:var(--accent-bg)':''}">
          <span class="src-arrow" style="font-size:10px;color:var(--text-muted);transition:transform .15s;cursor:pointer;${isExpanded?'transform:rotate(90deg)':''}" onclick="toggleSrcCard(this.closest('.src-card'))">▶</span>
          <span class="drag-handle" draggable="true" title="拖拽排序">⋮⋮</span>
          <div style="flex:1;min-width:0;cursor:pointer" onclick="toggleSrcCard(this.closest('.src-card'))">
            <div style="font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px">
              ${g.agent}
              ${isCurGroup?'<span class="to-scope to-global" style="font-size:9px">当前</span>':''}
            </div>
            <div style="font-size:10px;color:var(--text-muted)">${g.dirs.length} 个目录 · ${formatSourceCounts(g.dirs)}</div>
          </div>
        </div>
        <div style="display:${isExpanded?'block':'none'};border-top:1px solid var(--border)">`;
      if(!isExpanded){
        h+=`<div style="padding:10px 14px 10px 36px;font-size:11px;color:var(--text-muted);background:var(--bg-card-alt)">展开后加载 ${g.dirs.length} 个目录</div></div></div>`;
        return;
      }
      // Group dirs by category within this agent
      const catOrder=['user','marketplace','cache','cross-copy','project','commands','unknown'];
      const dirsByCat={};
      g.dirs.forEach(t=>{
        const c=t.category||'unknown';
        if(!dirsByCat[c]) dirsByCat[c]=[];
        dirsByCat[c].push(t);
      });
      // Render category sub-groups
      const catKeys=catOrder.filter(c=>dirsByCat[c]);
      if(catKeys.length<=1){
        // Single or no category — show one collapsible category header + dirs
        const cat=catKeys[0]||'unknown';
        const cm=CAT_META[cat]||CAT_META.unknown;
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
          const cm=CAT_META[cat]||CAT_META.unknown;
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
    ${!isCurrentTarget&&!isCommands?`<button class="btn btn-sm btn-primary" id="src-batch-sync-${bId}" onclick="batchSyncSrcSkills()" disabled style="font-size:9px;padding:2px 6px">批量同步到当前目录</button>`:''}
    ${!isCommands?`<button class="btn btn-sm btn-danger" id="src-batch-del-${bId}" onclick="batchDeleteSrcSkills()" disabled style="font-size:9px;padding:2px 6px">批量删除</button>`:''}
    <span style="font-size:10px;color:var(--text-muted)" id="src-sel-count-${bId}"></span>
  </div>`;
  itemList.forEach(s=>{
    const isInstalled=installed.has(s.name);
    const selKey=path+'::'+s.name;
    const isBroken=s.kind==='broken_symlink'||s.kind==='broken_skill_link';
    const kindLabel=s.kind==='broken_symlink'?'断链':(s.kind==='broken_skill_link'?'目录壳':'');
    h+=`<div style="display:flex;align-items:center;gap:6px;padding:3px 0;${isInstalled?'color:var(--green)':''}">
      <input type="checkbox" class="src-skill-check" data-path="${esc(path)}" data-name="${esc(s.name)}" ${srcSelectedSkills.has(selKey)?'checked':''} onchange="toggleSrcSkill(this)" style="cursor:pointer">
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;${isBroken?'color:var(--text-muted)':'cursor:pointer;color:var(--accent)'}" onclick="${isCommands||isBroken?'':`showSkill('${esc(s.name)}','${esc(path)}')`}">${s.name}</span>
      ${kindLabel?`<span style="font-size:9px;color:var(--red);border:1px solid color-mix(in srgb,var(--red) 35%,transparent);border-radius:999px;padding:1px 5px">${kindLabel}</span>`:''}
      ${!isCurrentTarget&&!isInstalled&&!isCommands?`<button class="btn btn-sm" onclick="stealFromSource('${esc(path)}','${esc(s.name)}')" style="font-size:9px;padding:2px 6px">复制到当前目录</button>`:''}
      ${isInstalled&&!isCommands?'<span style="font-size:9px;color:var(--text-muted)">已安装</span>':''}
      ${!isCommands?`<button class="btn btn-sm btn-danger" onclick="deleteSrcSkill('${esc(path)}','${esc(s.name)}')" style="font-size:9px;padding:2px 5px" title="删除此 skill">🗑</button>`:''}
    </div>`;
  });
  container.innerHTML=h;
  updateSrcBatchUI();
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
    if(d.ok){toast(`${name} 已移入垃圾站`);delete sourceSkillsCache[path];refreshAfterDelete([path]);loadTrash()}
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
  refreshAfterDelete([..._paths]);loadTrash();
}
async function batchSyncSrcSkills(){
  const sel=[...srcSelectedSkills].map(k=>({path:k.split('::')[0],name:k.split('::').slice(1).join('::')}));
  if(!sel.length)return;
  const curTarget=targets.find(t=>t.is_current);
  if(!curTarget)return toast('未选择目标目录','error');
  if(!confirm(`确认将 ${sel.length} 个 skill 复制到当前目录？\n目标: ${curTarget.name}\n\n${sel.map(s=>s.name).join(', ')}`))return;
  let ok=0,fail=0;
  for(const{name,path}of sel){
    try{
      const r=await fetch('/api/copy-skill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({src:path+'/'+name,target:curTarget.path,name})});
      const d=await r.json();d.ok?ok++:fail++;
    }catch{fail++}
  }
  toast(`已复制到当前目录 ${ok} 个${fail?`，${fail} 个失败`:''}`);
  srcSelectedSkills.clear();
  await loadData();
}

async function stealFromSource(srcPath,skillName){
  const srcDir=srcPath+'/'+skillName;
  const curTarget=targets.find(t=>t.is_current);
  if(!curTarget)return toast('未选择目标库','error');
  try{
    const r=await fetch('/api/copy-skill',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({src:srcDir,target:curTarget.path,name:skillName})});
    const d=await r.json();
    if(d.ok){toast(`${skillName} 已同步到目标库`);await loadData()}
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
    h+=`<div style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 30%,transparent)">
      <span style="font-size:11px;${isInstalled?'color:var(--green)':'color:var(--text-muted)'};min-width:60px">${isInstalled?'✓ 已安装':'○ 未安装'}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:12px;font-weight:500;cursor:pointer;color:var(--indigo)" onclick="showSkill('${esc(sk.name)}','${esc(path)}')">${sk.name}</div>
        <div style="font-size:10px;color:var(--text-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(summary)}">${escapeHtml(summary)}</div>
        ${tags.length?`<div class="skill-tags">${tags.map(t=>`<span class="skill-tag">${escapeHtml(t)}</span>`).join('')}</div>`:''}
      </div>
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
    await loadData();
    setTimeout(()=>$('modal').classList.add('hidden'),1200);
  }else{
    result.style.background='var(--bg-card-alt)';result.style.color='var(--text)';
    result.innerHTML=`<div>成功 ${ok} 个，失败 ${fail} 个</div><div style="margin-top:6px;font-size:11px;color:var(--text-muted)">${details.join('<br>')}</div>`;
    if(ok>0)await loadData();
  }
  btn.disabled=false;btn.textContent='批量添加';
}
async function removeCustomSource(path){
  if(!confirm(`确认移除来源 "${path}"？`))return;
  try{
    const r=await fetch('/api/custom-sources?path='+encodeURIComponent(path),{method:'DELETE'});
    const d=await r.json();
    if(d.ok){toast('来源已移除');await loadData()}
    else toast(d.error||'移除失败','error');
  }catch(e){toast('移除失败','error')}
}
