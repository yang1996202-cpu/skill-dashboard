// Issue view state
let _issueTypeTab='same-name'; // 'same-name' | 'upstream' | 'changes' | 'broken'

// layer 安全边界文档：每个层级的含义 + 能不能动。用户要"大大展示"的安全边界全景。
// key 是 layer 英文名（后端 action.layer），前端据此渲染详细解释。
const LAYER_DOC={
  'active-root':{boundary:'保护',explain:'Agent 的活跃技能根目录（如 ~/.codex/skills）。Agent 运行时直接读取这里，删目录会让 Agent 丢技能。只能在单个 skill 级别整理，不做整目录清理。'},
  'user-installed':{boundary:'保护',explain:'你主动安装管理的技能库。归你所有，可日常增删单个 skill，但不做整目录一键清理。'},
  'app-local-library':{boundary:'复核·可清理',explain:'某个 App 的本地技能库。通常是 App 自己管理的副本，确认无用、且有其他保留副本时，可进垃圾站。'},
  'downloaded-package':{boundary:'复核·可清理',explain:'从网上下载或解包得到的技能目录，多为临时产物。hash 与保留副本一致时可进垃圾站。'},
  'project-local':{boundary:'复核',explain:'绑定到某个项目的技能（~/projects/xxx/skills）。是否清理取决于该项目还要不要，不自动删。'},
  'imported-copy':{boundary:'复核·可清理',explain:'从一个 Agent 复制到另一个的技能副本。原版还在的前提下，重复副本可进垃圾站。'},
  'backup-snapshot':{boundary:'复核·可清理',explain:'备份或快照目录里的技能。通常有原版可恢复，重复的可进垃圾站。'},
  'package-cache':{boundary:'隐藏',explain:'包管理器（npm/bun 等）的安装缓存。不该用本工具删，交给对应包管理器清理。'},
  'plugin-cache':{boundary:'只观察',explain:'插件运行/下载产生的缓存工件。只解释来源，不作为清理目标。'},
  'plugin-package':{boundary:'只观察',explain:'宿主已启用插件包内的技能。由宿主管理，本工具只展示，不清理。'},
  'plugin-marketplace':{boundary:'只观察',explain:'市场/货架目录里的技能。数量大但只是货架，不等于已加载进上下文。不清理。'},
  'vendor-bundled':{boundary:'只观察',explain:'App/宿主自带的技能，随 App 更新。不由用户管理，不清理。'},
  'fixture-example':{boundary:'隐藏·可清理',explain:'测试 fixture 或示例技能，不是真实技能库。断舍离策略下可进垃圾站。'},
};
const boundaryTone=(b)=>{
  if(/保护/.test(b))return 'var(--green)';
  if(/可清理/.test(b))return 'var(--red)';
  if(/复核/.test(b))return 'var(--amber)';
  if(/隐藏|观察/.test(b))return 'var(--text-muted)';
  return 'var(--accent)';
};
const boundaryLabel=(a)=>{
  const p=a?.policy||'';
  if(p==='manage')return '保护';
  if(p==='review')return '复核·可清理';
  if(p==='observe')return '只观察';
  if(p==='hidden')return '隐藏';
  return '待定';
};
const fmtScanTime=(t)=>t?t.replace('T',' ').slice(0,16):'';
let _execShowAll=false; // executionPlan 各阶段目录的展开状态（独立于同名 tab 的 _issueShowAll）

// Scan scope persisted across sessions
let _scanScope=(()=>{
  try{
    const saved=localStorage.getItem('sd-scan-scope');
    if(saved) return saved;
  }catch{}
  return 'deep';
})();
function setScanScope(scope){
  _scanScope=scope==='daily'?'daily':'deep';
  localStorage.setItem('sd-scan-scope',_scanScope);
  renderScanConfig();
}

// Scan check types persisted across sessions
let _scanChecks=(()=>{
  try{
    const saved=localStorage.getItem('sd-scan-checks');
    if(saved) return JSON.parse(saved);
  }catch{}
  return ['same-name','upstream','content-changes'];
})();
function toggleScanCheck(key,checked){
  const set=new Set(_scanChecks);
  if(checked) set.add(key); else set.delete(key);
  _scanChecks=Array.from(set);
  localStorage.setItem('sd-scan-checks',JSON.stringify(_scanChecks));
  renderScanConfig();
}

// Map a directory path to its category using cached targets
function _dirTarget(dirPath){
  if(!dirPath) return 'unknown';
  const norm=String(dirPath).replace(/\/+$/,'');
  const exact=targets.find(t=>String(t.path).replace(/\/+$/,'')===norm);
  if(exact)return exact;
  let best=null;
  targets.forEach(t=>{
    const p=String(t.path||'').replace(/\/+$/,'');
    if(!p)return;
    if(norm.startsWith(p+'/')||p.startsWith(norm+'/')){
      if(!best||p.length>String(best.path||'').length)best=t;
    }
  });
  return best;
}
function _dirCategory(dirPath){
  const t=_dirTarget(dirPath);
  return t?.category||'unknown';
}

// Category metadata
const CAT_META={
  user:{emoji:'⭐',label:'用户自建',color:'#f59e0b'},
  marketplace:{emoji:'📦',label:'生态/Marketplace',color:'#3b82f6'},
  cache:{emoji:'🗑️',label:'缓存/备份',color:'#6b7280'},
  'cross-copy':{emoji:'🔁',label:'跨Agent副本',color:'#8b5cf6'},
  project:{emoji:'📁',label:'项目级',color:'#10b981'},
  commands:{emoji:'⌨️',label:'命令',color:'#f97316'},
  unknown:{emoji:'❓',label:'未知',color:'#9ca3af'},
};
const POLICY_META={
  manage:{emoji:'⭐',label:'用户/项目',desc:'用户自建或项目级技能库，可作为日常整理对象'},
  review:{emoji:'🔁',label:'导入/副本',desc:'跨 Agent 副本或导入目录，先看内容再处理'},
  observe:{emoji:'📦',label:'生态目录',desc:'marketplace 或内置包，默认不做删除动作'},
  hidden:{emoji:'🚫',label:'缓存/内置',desc:'缓存、备份或测试样例，只在来源库存或全部视图里看'},
};
const LAYER_FALLBACK={
  user:'用户技能库',
  marketplace:'插件市场/目录',
  cache:'缓存/备份',
  'cross-copy':'导入/跨 Agent 副本',
  project:'项目内技能',
  commands:'命令',
  unknown:'未知来源',
};
function sourcePolicy(t){
  if(!t)return 'review';
  if(t.policy)return t.policy;
  const c=t.category||'unknown';
  if(c==='user')return 'manage';
  if(c==='cross-copy'||c==='project')return 'review';
  if(c==='marketplace')return 'observe';
  if(c==='cache')return 'hidden';
  return 'review';
}
function sourceLayerLabel(t){
  return t?.layer_label||LAYER_FALLBACK[t?.category||'unknown']||'未知来源';
}
function sourceIsDaily(t){
  if(t?.is_current) return true;
  const c=t?.category||'unknown';
  return c==='user'||c==='project';
}
function sourceIsActive(t){
  return ['active-user','active-system','active-plugin','active-connector','commands'].includes(sourceCapabilityBucket(t));
}
function sourceIsInventory(t){
  return ['source-cache','source-catalog','installed-disabled'].includes(sourceCapabilityBucket(t));
}
function sourceIsReview(t){
  return ['review-copy','project-local','unknown'].includes(sourceCapabilityBucket(t));
}
function issueDirBadge(dirPath){
  const t=_dirTarget(dirPath);
  const key=sourceCapabilityBucket(t);
  const meta=capabilityMeta(key);
  return `<span style="font-size:10px" title="${esc(meta.label)}">${meta.emoji}</span>`;
}
function sourceCanDelete(t){
  return sourcePolicy(t)==='manage'&&t?.is_deletable!==false;
}
function sourcePolicyBadge(t){
  const p=sourcePolicy(t);
  const meta=POLICY_META[p]||POLICY_META.review;
  return `<span style="font-size:9px;color:var(--text-muted);background:var(--bg-card-alt);border:1px solid var(--border-subtle);padding:1px 5px;border-radius:999px;white-space:nowrap" title="${esc(meta.desc)}">${meta.emoji} ${meta.label}</span>`;
}

// Shared directory list abstraction
function filterGroupsByView(groups,viewMode){
  const predicate={
    active:sourceIsActive,
    inventory:sourceIsInventory,
    review:sourceIsReview,
    'all':()=>true,
  }[viewMode]||sourceIsActive;
  const filtered=groups.map(g=>{
    const dirs=g.dirs.filter(predicate);
    return {...g,dirs,total_skills:dirs.reduce((s,d)=>s+(d.count||0),0)};
  }).filter(g=>g.dirs.length);
  return filtered;
}
function sortGroupsByCurrentAndSize(groups){
  return [...groups].sort((a,b)=>{
    const aCur=a.dirs.some(t=>t.is_current);
    const bCur=b.dirs.some(t=>t.is_current);
    if(aCur!==bCur)return aCur?-1:1;
    return b.total_skills-a.total_skills;
  });
}
function getVisibleSourceTargets(){
  return filterGroupsByView(targetGroups,_sourceViewMode).flatMap(g=>g.dirs);
}
function getVisibleSourceGroups(){
  return filterGroupsByView(targetGroups,_sourceViewMode);
}
function getDailyScanTargets(){
  return targetGroups.flatMap(g=>g.dirs.filter(sourceIsDaily));
}
function sourceMatchesView(t,viewMode){
  if(viewMode==='all')return true;
  if(viewMode==='inventory')return sourceIsInventory(t);
  if(viewMode==='review')return sourceIsReview(t);
  return sourceIsActive(t);
}
function setSourceViewMode(mode){
  _sourceViewMode=['active','inventory','review','all'].includes(mode)?mode:'active';
  localStorage.setItem('sd-source-view',_sourceViewMode);
  _sourcesShowAll=false;
  if($('view-sources')?.classList.contains('active')){
    renderSources();
  }
  if(typeof updateTargetSelector==='function'){
    updateTargetSelector(false,'dropdown');
  }
}

function renderScanConfig(){
  const el=$('scan-config');
  if(!el) return;
  let statusHtml='';
  if(scanResult){
    const scopeLabel=scanResult.scope==='daily'?'重点扫描':'全量扫描';
    const tokenOk=scanResult.github_token_configured;
    statusHtml=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;font-size:11px;color:var(--text-muted)">
      <span>扫描：${scopeLabel} · ${scanResult.scanned_dirs} 目录 · ${(scanResult.duration_ms/1000).toFixed(1)}s</span>
      ${tokenOk?`<span style="color:var(--green);margin-left:8px" title="已配置 GITHUB_TOKEN，额度 5000 次/小时">🔐 Token 已配置</span>`:`<span style="color:var(--amber);margin-left:8px" title="未配置 GITHUB_TOKEN，GitHub API 未认证额度 60 次/小时">⚠ 未配置 Token</span>`}
      ${scanResult.lint?.warnings?.length?`<span style="color:var(--red);margin-left:8px">${scanResult.lint.warnings.length} 个数据异常</span>`:''}
    </div>`;
  }
  const scopeBtn=(scope,label)=>{
    const active=_scanScope===scope;
    return `<button class="btn btn-sm ${active?'btn-primary':''}" onclick="setScanScope('${scope}')" style="${active?'':'background:var(--bg-card-alt);color:var(--text-muted)'}">${label}</button>`;
  };
  const checkBox=(key,label,title)=>{
    const checked=_scanChecks.includes(key);
    return `<label title="${esc(title)}" style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-muted);cursor:pointer;user-select:none">
      <input type="checkbox" ${checked?'checked':''} onchange="toggleScanCheck('${key}',this.checked)" style="cursor:pointer">
      <span>${label}</span>
    </label>`;
  };
  el.innerHTML=`<div class="card" style="border-left:3px solid var(--accent)">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px">
      <button class="btn btn-primary" id="cleanup-start-btn" onclick="startCleanupFlow()">开始整理</button>
      <span style="font-size:11px;color:var(--text-muted)">勾选检查项后点开始，自动扫描并生成处理建议。</span>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;align-items:center">
        <div style="display:flex;gap:4px;align-items:center;padding-right:8px;border-right:1px solid var(--border-subtle)">
          ${scopeBtn('daily','重点扫描')}
          ${scopeBtn('deep','全量扫描')}
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          ${checkBox('same-name','同名','跨目录同名 skill')}
          ${checkBox('upstream','上游','检查是否有上游新版本')}
          ${checkBox('content-changes','变更','检测本地内容改动')}
        </div>
      </div>
    </div>
    ${statusHtml}
  </div>`;
}

function renderCleanupLoading(step=1){
  const steps=[
    {n:1,label:'扫描目录与检查项'},
    {n:2,label:'生成处理建议'}
  ];
  return `<div class="card cleanup-loading" style="border-left:3px solid var(--accent)">
    <div class="cleanup-spinner"></div>
    <div class="cleanup-loading-title">正在整理 · 步骤 ${step}/2</div>
    <div class="cleanup-loading-steps">
      ${steps.map(s=>`<div class="cleanup-step ${s.n===step?'active':s.n<step?'done':''}"><span class="cleanup-step-num">${s.n}</span><span>${s.label}</span></div>`).join('')}
    </div>
    <div class="cleanup-loading-note">目录较多时可能需要 10–30 秒，请勿重复点击。</div>
  </div>`;
}

function setCleanupLoading(active,step=1){
  const startBtn=$('cleanup-start-btn');
  if(startBtn){startBtn.disabled=active;startBtn.textContent=active?'整理中...':'开始整理'}
  document.querySelectorAll('#scan-config input[type=checkbox]').forEach(cb=>cb.disabled=active);
  document.querySelectorAll('#scan-config button').forEach(b=>{if(b.id!=='cleanup-start-btn')b.disabled=active;});
  const list=$('issues-list');
  if(active&&list)list.innerHTML=renderCleanupLoading(step);
}

async function startCleanupFlow(){
  setCleanupLoading(true,1);
  try{
    const scope=_scanScope||'deep';
    const checks=[..._scanChecks];
    if(checks.length){
      await runScan(scope,{silent:true,deferRender:true,checks});
    }
    setCleanupLoading(true,2);
    await runCleanupPlan(scope,{deferRender:true});
    await runExecutionPlan('declutter',{silent:true});
    toast('整理完成：已生成可处理建议');
  }catch(e){
    if(cleanupPlan)renderIssues();
    toast('整理失败: '+e.message,'error');
  }finally{
    setCleanupLoading(false);
  }
}

async function runScan(scope='deep',opts={}){
  try{
    const scanTargets=scope==='deep'?targets:getDailyScanTargets();
    const directories=targets.length?scanTargets.map(t=>t.path):[];
    const checks=(opts.checks&&opts.checks.length)?opts.checks:['same-name'];
    const r=await fetch('/api/scan-run',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({directories,scope,checks})
    }).then(r=>r.json());
    if(r.error){toast(r.error,'error');return}
    scanResult=r;
    // Map scan result into health/globalOverlap for renderIssues
    health={
      upstream_sources:r.upstream_sources||[],
      content_changes:r.content_changes,
      structure_issues:r.structure_issues||[],
      cleanup_candidates:[],
      generated_at:r.scanned_at,
    };
    globalOverlap={
      duplicates_same_name:r.duplicates_same_name||[],
      duplicates_identical:r.duplicates_identical||[],
      total_identical:(r.duplicates_identical||[]).length,
    };
    if(!opts.preserveIssueView){
      _issueTypeTab='same-name';
      _issueShowAll=false;
    }
    if(!opts.deferRender)renderIssues();
    updateDiagBadges();
    if(!opts.silent)toast(`扫描完成: ${r.scanned_dirs} 目录 · ${r.duration_ms}ms`);
  }catch(e){if(!opts.silent)toast('扫描请求失败（可能超时或服务未响应）：'+e.message+'。后续预案会基于旧缓存继续。','error')}
}

async function runCleanupPlan(scope='daily',opts={}){
  const btn=$('cleanup-plan-btn');
  const deepBtn=$('cleanup-plan-deep-btn');
  if(btn)btn.disabled=true;
  if(deepBtn)deepBtn.disabled=true;
  if(btn)btn.textContent=scope==='deep'?'⏳ 全量计划中...':'⏳ 生成计划中...';
  try{
    const r=await fetch(`/api/cleanup-plan?scope=${encodeURIComponent(scope)}`).then(r=>r.json());
    if(r.error){toast(r.error,'error');return}
    cleanupPlan=r;
    executionPlan=null;
    cleanupExcludedActions.clear();
    if(!opts.deferRender)renderIssues();
    if(!opts.silent)toast(`${scope==='deep'?'全量目录审计':'重点治理计划'}完成: ${r.summary?.directories||0} 目录`);
  }catch(e){toast('清理计划失败: '+e.message,'error')}
  finally{
    if(btn){btn.disabled=false;btn.textContent='目录依据'}
    if(deepBtn)deepBtn.disabled=false;
  }
}

async function runExecutionPlan(strategy='conservative',opts={}){
  const scope=cleanupPlan?.scope||'daily';
  const btn=$('execution-plan-btn');
  const declutterBtn=$('execution-plan-declutter-btn');
  if(btn)btn.disabled=true;
  if(declutterBtn)declutterBtn.disabled=true;
  if(btn)btn.textContent=strategy==='declutter'?'⏳ 生成断舍离预案...':'⏳ 生成执行预案...';
  try{
    const r=await fetch(`/api/cleanup-execution-plan?scope=${encodeURIComponent(scope)}&strategy=${encodeURIComponent(strategy)}`).then(r=>r.json());
    if(r.error){toast(r.error,'error');return}
    executionPlan=r;
    cleanupExcludedActions.clear();
    renderIssues();
    if(!opts.silent)toast(`${strategy==='declutter'?'断舍离预案':'执行预案'}完成: ${r.summary?.actions||0} 个动作`);
  }catch(e){toast('执行预案失败: '+e.message,'error')}
  finally{
    if(btn){btn.disabled=false;btn.textContent='生成执行预案'}
    if(declutterBtn)declutterBtn.disabled=false;
  }
}

function renderCleanupPlan(){
  if(!cleanupPlan)return '';
  const s=cleanupPlan.summary||{};
  const compact=!!executionPlan;
  const scopeLabel=cleanupPlan.scope==='deep'?'全量清理审计':'重点清理计划';
  const groupTone={protect:'var(--green)',review:'var(--amber)',observe:'var(--accent)',hide:'var(--text-muted)'};
  const groupIcon={protect:'🛡️',review:'🔎',observe:'👁️',hide:'🚫'};
  const riskLabel={low:'低风险',medium:'中风险',high:'高风险'};
  const ruleHtml=(cleanupPlan.rules||[]).map(r=>`<div style="display:flex;gap:8px;align-items:flex-start;padding:6px 0;border-bottom:1px solid var(--border-subtle)">
    <span style="font-size:11px;color:var(--accent);font-weight:700;min-width:72px">${escapeHtml(r.name)}</span>
    <span style="font-size:11px;color:var(--text-muted);line-height:1.5">${escapeHtml(r.text)}</span>
  </div>`).join('');
  const groupHtml=(cleanupPlan.groups||[]).map(g=>{
    const items=(g.items||[]).slice(0,_issueShowAll?999:8);
    const hidden=Math.max(0,(g.items||[]).length-items.length);
    return `<div class="card" style="min-width:320px;flex:1;border-left:3px solid ${groupTone[g.key]||'var(--border)'}">
      <div class="card-head" style="align-items:flex-start">
        <div>
          <h3>${groupIcon[g.key]||'📁'} ${escapeHtml(g.label)}</h3>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5">${escapeHtml(g.intent||'')}</div>
        </div>
        <span class="sub">${g.directory_count} 目录 · ${g.skill_count} skills</span>
      </div>
      <div style="display:grid;gap:8px">
        ${items.map(item=>`<div style="border:1px solid var(--border-subtle);border-radius:8px;padding:8px;background:var(--bg-card-alt)">
          <div style="display:flex;align-items:center;gap:8px;min-width:0">
            <span class="skill-tag">${escapeHtml(item.policy_label||item.policy)}</span>
            <span class="skill-tag">${escapeHtml(item.layer_label||item.layer)}</span>
            <span class="skill-tag" style="color:${item.risk==='high'?'var(--red)':item.risk==='medium'?'var(--amber)':'var(--green)'}">${riskLabel[item.risk]||item.risk}</span>
            <span style="flex:1"></span>
            <span style="font-size:11px;color:var(--text-muted)">${item.count} skills</span>
          </div>
          <div style="font-size:12px;font-weight:600;color:var(--text);margin-top:6px">${escapeHtml(item.agent)} · ${escapeHtml(item.decision)}</div>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5;margin-top:4px">去向：${escapeHtml(item.next_state)}</div>
          ${renderIssuePath(item.path)}
          ${item.reasons?.length?`<div style="font-size:10px;color:var(--text-dim);line-height:1.5;margin-top:5px">证据：${item.reasons.map(escapeHtml).join(' / ')}</div>`:''}
          ${item.sample_skills?.length?`<div class="skill-tags" style="margin-top:6px">${item.sample_skills.slice(0,5).map(n=>`<span class="skill-tag">${escapeHtml(n)}</span>`).join('')}</div>`:''}
        </div>`).join('')}
        ${hidden?`<div style="font-size:11px;color:var(--text-muted);padding:4px 0">还有 ${hidden} 个目录未展开，点击“显示全量”查看。</div>`:''}
      </div>
    </div>`;
  }).join('');
  return `<div style="margin-bottom:16px">
    <div class="card" style="border-left:3px solid var(--green)">
      <div class="card-head">
        <div>
          <h3>🧭 ${scopeLabel}</h3>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5">高级依据 · 不执行删除 · ${cleanupPlan.duration_ms||0}ms · ${cleanupPlan.generated_at||''}</div>
        </div>
        <button class="btn btn-sm" onclick="cleanupPlan=null;executionPlan=null;renderIssues()">收起计划</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:8px;margin-bottom:10px">
        <div class="scope-card primary"><div class="scope-name"><span>目录</span><b>${s.directories||0}</b></div><div class="scope-desc">${s.skills||0} skills</div></div>
        <div class="scope-card"><div class="scope-name"><span>保护</span><b>${s.protect||0}</b></div><div class="scope-desc">不做目录级删除</div></div>
        <div class="scope-card warn"><div class="scope-name"><span>复核</span><b>${s.review||0}</b></div><div class="scope-desc">${s.review_skills||0} skills</div></div>
        <div class="scope-card muted"><div class="scope-name"><span>直删</span><b>${s.direct_delete||0}</b></div><div class="scope-desc">当前版本保持 0</div></div>
      </div>
      <details><summary style="font-size:12px;color:var(--text-muted);cursor:pointer">查看清理准则</summary><div style="margin-top:8px">${ruleHtml}</div></details>
    </div>
    ${compact
      ? `<details><summary style="font-size:12px;color:var(--text-muted);cursor:pointer;margin:8px 0 0 4px">查看完整治理明细</summary><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;margin-top:8px">${groupHtml}</div></details>`
      : `<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">${groupHtml}</div>`}
  </div>`;
}

function renderExecutionPlan(){
  if(!executionPlan)return '';
  const s=executionPlan.summary||{};
  const phaseTone={protect:'var(--green)',review:'var(--amber)',organize:'var(--accent)',deploy:'var(--green)',candidate:'var(--red)'};
  const phaseIcon={protect:'锁定',review:'复核',organize:'收纳',deploy:'部署',candidate:'候选'};
  const strategyLabel=executionPlan.strategy==='declutter'?'断舍离策略':'保守策略';
  const candidateActions=cleanupCandidateActions();
  const executableActions=candidateActions.filter(a=>!cleanupExcludedActions.has(a.id));
  const excludedCount=candidateActions.length-executableActions.length;
  const executableSkillCount=executableActions.reduce((sum,a)=>sum+(a.count||0),0);
  const directoryCandidateCount=executableActions.filter(a=>a.operation==='move_skills_to_trash').length;
  const exactDuplicateCount=executableActions.filter(a=>a.operation==='move_skill_to_trash').length;
  const deployCount=(executionPlan.phases||[]).find(p=>p.key==='deploy')?.action_count||0;
  const phaseOrder=executionPlan.strategy==='declutter'?{candidate:0,deploy:1,review:2,organize:3,protect:4}:{protect:0,review:1,organize:2,deploy:3,candidate:4};
  const sortedPhases=[...(executionPlan.phases||[])].sort((a,b)=>(phaseOrder[a.key]??9)-(phaseOrder[b.key]??9));

  const renderActionCard=(a)=> {
    const executable=cleanupIsCandidateAction(a);
    const doc=LAYER_DOC[a.layer]||{};
    const boundary=doc.boundary||boundaryLabel(a);
    const bTone=boundaryTone(boundary);
    const layerText=a.layer_label||a.from_state||'未知层级';
    const title=a.operation==='mark_multi_agent_deploy'
      ? `${a.skill_name||''} · 多端部署`
      : a.skill_name
        ? `${a.skill_name} → 垃圾站`
        : `${a.agent||''} → 垃圾站`;
    return `<div style="border:1px solid var(--border-subtle);border-radius:10px;padding:0;background:var(--bg-card-alt);overflow:hidden">
      <div style="padding:8px 10px;background:${bTone}22;border-bottom:1px solid ${bTone}66;display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <span style="font-size:13px;font-weight:700;color:${bTone}">📦 ${escapeHtml(layerText)}</span>
        <span style="font-size:10px;font-weight:700;color:#fff;background:${bTone};padding:2px 8px;border-radius:10px">${escapeHtml(boundary)}</span>
        <span style="flex:1"></span>
        ${a.policy_label?`<span style="font-size:10px;color:var(--text-muted)">策略：${escapeHtml(a.policy_label)}</span>`:''}
      </div>
      ${doc.explain?`<div style="font-size:11px;color:var(--text-muted);line-height:1.6;padding:6px 10px;background:var(--bg-card);border-bottom:1px solid var(--border-subtle)">${escapeHtml(doc.explain)}</div>`:''}
      <div style="padding:8px 10px">
        <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
          ${executable?`<label class="skill-tag" style="cursor:pointer;display:inline-flex;align-items:center;gap:4px"><input type="checkbox" ${cleanupExcludedActions.has(a.id)?'':'checked'} onchange="toggleCleanupExclude('${esc(a.id)}')" style="margin:0">${cleanupExcludedActions.has(a.id)?'已排除':`纳入清理 · ${a.count||0} skills`}</label>`:''}
          ${a.operation==='mark_multi_agent_deploy'?`<button class="btn btn-sm" onclick="markMultiAgentDeployment('${esc(a.skill_name||'')}','${esc(a.content_hash||'')}','${esc(a.path||'')}','${esc(a.duplicate_of||'')}')" style="font-size:9px;padding:1px 6px">标记多端部署</button>`:''}
          <span class="skill-tag">${escapeHtml(a.label)}</span>
          ${a.operation==='mark_multi_agent_deploy'?'<span class="skill-tag">不进垃圾站</span>':a.skill_name?'<span class="skill-tag">单个重复 skill</span>':'<span class="skill-tag">目录候选</span>'}
          ${a.destructive?'<span class="skill-tag" style="color:var(--red)">会移动文件</span>':'<span class="skill-tag" style="color:var(--green)">不动文件</span>'}
          ${a.requires_confirmation?'<span class="skill-tag" style="color:var(--amber)">需二次确认</span>':''}
          ${cleanupExcludedActions.has(a.id)?'<span class="skill-tag">已排除</span>':''}
          <span style="flex:1"></span>
          <span style="font-size:11px;color:var(--text-muted)">${a.count||0} skills</span>
        </div>
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-top:6px">${escapeHtml(title)}</div>
        <div style="font-size:11px;color:var(--text-muted);line-height:1.5;margin-top:4px">原因：${escapeHtml(a.why||'')}</div>
        <div style="font-size:11px;color:var(--text-muted);line-height:1.5">回滚：${escapeHtml(a.rollback||'')}</div>
        ${renderIssuePath(a.path)}
        ${a.duplicate_of?`<div style="font-size:10px;color:var(--text-dim);margin-top:5px">保留副本</div>${renderIssuePath(a.duplicate_of)}`:''}
        ${a.evidence?.length?`<div style="font-size:10px;color:var(--text-dim);line-height:1.5;margin-top:5px">证据：${a.evidence.map(escapeHtml).join(' / ')}</div>`:''}
        ${a.sample_skills?.length?`<div class="skill-tags" style="margin-top:6px">${a.sample_skills.slice(0,5).map(n=>`<span class="skill-tag">${escapeHtml(n)}</span>`).join('')}</div>`:''}
      </div>
    </div>`;
  };

  const renderPhaseCard=(phase,limit=12)=>{
    const all=phase.actions||[];
    const actions=_execShowAll?all:all.slice(0,limit);
    const showToggle=all.length>limit;
    return `<div class="card" style="min-width:320px;flex:1;border-left:3px solid ${phaseTone[phase.key]||'var(--border)'}">
      <div class="card-head" style="align-items:flex-start">
        <div>
          <h3>${phaseIcon[phase.key]||'▶'} ${escapeHtml(phase.label)}</h3>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5">${escapeHtml(phase.intent||'')}</div>
        </div>
        <span class="sub">${phase.action_count} 动作 · ${phase.skill_count} skills</span>
      </div>
      <div style="display:grid;gap:8px">
        ${actions.map(renderActionCard).join('')}
        ${showToggle?(_execShowAll
          ?`<div class="notice-line"><span>显示全部 ${all.length} 个目录</span><button class="btn btn-sm" onclick="_execShowAll=false;renderIssues()">只看前 ${limit}</button></div>`
          :`<div class="notice-line"><span>显示前 ${limit} / 共 ${all.length} 个目录</span><button class="btn btn-sm btn-primary" onclick="_execShowAll=true;renderIssues()">显示全部 ${all.length}</button></div>`):''}
      </div>
    </div>`;
  };
  const candidatePhases=sortedPhases.filter(p=>['candidate','deploy'].includes(p.key));
  const evidencePhases=sortedPhases.filter(p=>!['candidate','deploy'].includes(p.key));
  const candidateHtml=candidatePhases.length
    ? candidatePhases.map(p=>renderPhaseCard(p,16)).join('')
    : '<div class="empty" style="padding:18px 0">当前没有推荐移入垃圾站的候选。</div>';
  const evidenceCount=evidencePhases.reduce((sum,p)=>sum+(p.action_count||0),0);
  const evidenceHtml=evidencePhases.length
    ? `<details open style="margin-top:10px"><summary style="font-size:12px;color:var(--text-muted);cursor:pointer">🛡️ 未列入清理的目录（${evidenceCount} 个 · 按 layer 安全边界保留，已展开查看每个目录层级，可点击收起）</summary><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;margin-top:8px">${evidencePhases.map(p=>renderPhaseCard(p,12)).join('')}</div></details>`
    : '';
  const rules=(executionPlan.rules||[]).map(r=>`<div style="font-size:11px;color:var(--text-muted);line-height:1.5;padding:3px 0">${escapeHtml(r)}</div>`).join('');
  return `<div style="margin-bottom:16px">
    <div class="card" style="border-left:3px solid var(--red)">
      <div class="card-head">
        <div>
          <h3>人工处理区</h3>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5">${strategyLabel} · 线索先给推荐，人再勾选；只移入垃圾站，可恢复 · 预案生成 ${fmtScanTime(executionPlan.generated_at)}</div>
        </div>
        <button class="btn btn-sm" onclick="showDuplicateDecisions()" title="查看本机记录的运行状态，不随 Git 提交">本地决策</button>
        <button class="btn btn-sm" onclick="executionPlan=null;renderIssues()">收起推荐</button>
        ${excludedCount?`<button class="btn btn-sm" onclick="restoreAllCleanupCandidates()">恢复全部推荐</button>`:''}
        <button class="btn btn-sm btn-danger" id="cleanup-execute-btn" onclick="executeRecommendedCleanupActions()" ${executableActions.length?'':'disabled'} title="只把推荐候选移入垃圾站，不永久删除。数字前半是动作项，后半是实际 skill 数。">移入垃圾站 ${executableActions.length} 项 / ${executableSkillCount} skills</button>
      </div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:10px">
        <div class="scope-card primary"><div class="scope-name"><span>可勾选处理</span><b>${executableActions.length}</b></div><div class="scope-desc">${directoryCandidateCount} 目录 · ${exactDuplicateCount} 重复 skill</div></div>
        <div class="scope-card warn"><div class="scope-name"><span>涉及内容</span><b>${executableSkillCount}</b></div><div class="scope-desc">skills</div></div>
        <div class="scope-card"><div class="scope-name"><span>多端部署</span><b>${deployCount}</b></div><div class="scope-desc">默认保留</div></div>
        <div class="scope-card"><div class="scope-name"><span>已排除</span><b>${excludedCount}</b></div><div class="scope-desc">不会处理</div></div>
      </div>
      <details><summary style="font-size:12px;color:var(--text-muted);cursor:pointer">查看执行规则</summary><div style="margin-top:8px">${rules}</div></details>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start">${candidateHtml}</div>
    ${evidenceHtml}
  </div>`;
}

function cleanupIsCandidateAction(a){
  return a&&['move_skills_to_trash','move_skill_to_trash'].includes(a.operation);
}

async function markMultiAgentDeployment(skillName,contentHash,path,duplicateOf){
  if(!skillName||!contentHash)return;
  try{
    const r=await fetch('/api/duplicate-decision',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        decision:'multi_agent_deployment',
        skill_name:skillName,
        content_hash:contentHash,
        path,
        duplicate_of:duplicateOf,
      })
    });
    const d=await r.json();
    if(!d.ok){toast(d.error||'标记失败','error');return}
    toast('已标记为多端部署：同一内容不再重复提醒');
    await runExecutionPlan(executionPlan?.strategy||'declutter',{silent:true});
  }catch(e){toast('标记失败: '+e.message,'error')}
}

async function showDuplicateDecisions(){
  $('modal-title').textContent='本地决策';
  $('modal-body').innerHTML='<div style="padding:12px;color:var(--text-muted)">加载中...</div>';
  $('modal').classList.remove('hidden');
  try{
    const d=await fetch('/api/duplicate-decisions').then(r=>r.json());
    const rows=d.decisions||[];
    const intro=`<div style="font-family:-apple-system,sans-serif;font-size:12px;color:var(--text-muted);line-height:1.6;margin-bottom:10px">
      这些记录只保存在本机 <code>.data/state/duplicate-decisions.json</code>，用于隐藏已确认的多端部署重复提醒；不会提交到 Git。内容 hash 变化后会重新出现。
    </div>`;
    if(!rows.length){
      $('modal-body').innerHTML=intro+'<div class="empty" style="font-family:-apple-system,sans-serif">暂无本地决策。你在“多端部署”里点击标记后，这里会出现可撤销记录。</div>';
      return;
    }
    $('modal-body').innerHTML=intro+`<div style="display:grid;gap:8px;font-family:-apple-system,sans-serif">
      ${rows.map(row=>`<div style="border:1px solid var(--border-subtle);border-radius:8px;padding:10px;background:var(--bg-card-alt)">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <b style="font-size:13px;color:var(--text)">${escapeHtml(row.skill_name||'')}</b>
          <span class="skill-tag">多端部署</span>
          <span style="font-size:11px;color:var(--text-muted)">${escapeHtml(row.decided_at||'')}</span>
          <span style="flex:1"></span>
          <button class="btn btn-sm" onclick="removeDuplicateDecision('${esc(row.key||'')}')" style="font-size:10px;padding:2px 8px">撤销</button>
        </div>
        <div style="font-family:'SF Mono','Fira Code',monospace;font-size:10px;color:var(--text-dim);line-height:1.5;margin-top:6px">
          hash: ${escapeHtml(row.content_hash||'')}<br>
          path: ${escapeHtml(row.path||'')}<br>
          kept: ${escapeHtml(row.duplicate_of||'')}
        </div>
      </div>`).join('')}
    </div>`;
  }catch(e){
    $('modal-body').innerHTML='<div style="color:var(--red)">加载失败：'+escapeHtml(e.message)+'</div>';
  }
}

async function removeDuplicateDecision(key){
  if(!key)return;
  try{
    const d=await fetch('/api/duplicate-decision?key='+encodeURIComponent(key),{method:'DELETE'}).then(r=>r.json());
    if(!d.ok){toast(d.error||'撤销失败','error');return}
    toast('已撤销本地决策');
    await showDuplicateDecisions();
    if(executionPlan)await runExecutionPlan(executionPlan.strategy||'declutter',{silent:true});
  }catch(e){toast('撤销失败: '+e.message,'error')}
}

function cleanupCandidateActions(){
  const actions=[];
  (executionPlan?.phases||[]).forEach(p=>(p.actions||[]).forEach(a=>{
    if(cleanupIsCandidateAction(a))actions.push(a);
  }));
  return actions;
}
function toggleCleanupExclude(id){
  if(cleanupExcludedActions.has(id))cleanupExcludedActions.delete(id);
  else cleanupExcludedActions.add(id);
  renderIssues();
}
function restoreAllCleanupCandidates(){
  cleanupExcludedActions.clear();
  renderIssues();
}

async function refreshIssuesAfterDelete(changedPaths=[],opts={}){
  const scope=opts.scope||scanResult?.scope||cleanupPlan?.scope||executionPlan?.scope||'daily';
  const strategy=opts.strategy||executionPlan?.strategy||'declutter';
  const hadCleanupPlan=!!cleanupPlan||!!executionPlan;
  const hadExecutionPlan=!!executionPlan;
  const tabBefore=_issueTypeTab;
  const showAllBefore=_issueShowAll;
  await loadTrash();
  await refreshAfterDelete(changedPaths||[]);
  if(hadCleanupPlan){
    await runCleanupPlan(scope,{silent:true,deferRender:true});
  }
  await runScan(scope,{silent:true,deferRender:true,preserveIssueView:true});
  _issueTypeTab=tabBefore;
  _issueShowAll=showAllBefore;
  if(hadExecutionPlan){
    await runExecutionPlan(strategy,{silent:true});
  }else{
    renderIssues();
  }
  updateDiagBadges();
}

async function executeRecommendedCleanupActions(){
  const selected=cleanupCandidateActions().filter(a=>!cleanupExcludedActions.has(a.id));
  if(!selected.length)return;
  const totalSkills=selected.reduce((s,a)=>s+(a.count||0),0);
  const excluded=cleanupCandidateActions().length-selected.length;
  const dirCount=selected.filter(a=>a.operation==='move_skills_to_trash').length;
  const dupCount=selected.filter(a=>a.operation==='move_skill_to_trash').length;
  if(!confirm(`将 ${selected.length} 个清理项移入垃圾站。\n\n其中包含 ${dirCount} 个候选目录、${dupCount} 个完全重复 skill，共 ${totalSkills} 个 skills。\n${excluded?`已排除 ${excluded} 项。\\n`:''}\n不会永久删除，可在垃圾站恢复。确认执行？`))return;
  const btn=$('cleanup-execute-btn');
  if(btn){btn.disabled=true;btn.textContent='执行中...'}
  const scopeBefore=scanResult?.scope||cleanupPlan?.scope||executionPlan?.scope||'daily';
  const strategyBefore=executionPlan?.strategy||'declutter';
  try{
    const r=await fetch('/api/cleanup-execute',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({actions:selected.map(a=>({
        id:a.id,
        path:a.path,
        operation:a.operation,
        skill_name:a.skill_name,
        duplicate_of:a.duplicate_of,
        content_hash:a.content_hash,
      }))})
    });
    const d=await r.json();
    if(!d.ok&&d.error){toast(d.error,'error');return}
    cleanupExcludedActions.clear();
    await refreshIssuesAfterDelete(d.changed_paths||[],{scope:scopeBefore,strategy:strategyBefore});
    toast(`已移入垃圾站 ${d.moved||0} 个 skill（${selected.length} 项）${d.failed?`，${d.failed} 个失败`:''}`);
  }catch(e){toast('执行失败: '+e.message,'error')}
  finally{
    if(btn){btn.disabled=false;btn.textContent='移入垃圾站'}
  }
}

/* ── Issues with fix actions (redesigned) ── */
function renderIssues(){
  renderScanConfig();

  const issues=health?.structure_issues||[];
  const changes=health?.content_changes;
  const upstreams=health?.upstream_sources||[];
  const sameName=globalOverlap?.duplicates_same_name||[];
  const executionHtml=renderExecutionPlan();
  const planHtml=executionPlan?'':renderCleanupPlan();

  // No scan yet
  if(!scanResult&&(!health||(!upstreams.length&&!issues.length))){
    $('issues-list').innerHTML=executionHtml+planHtml+'<div class="empty" style="padding:30px 0">点击「开始整理」扫描目录并生成处理建议。</div>';
    return;
  }

  // ── 内容类型计数：按问题类型分，不再按运行态 view 过滤 ──
  const sameNameGroups=sameName.filter(dup=>dup.locations.length>=2);
  const upstreamAll=upstreams.filter(s=>s.repo);
  const changedSkills=changes?.changed||[];
  const brokenLinks=issues.filter(i=>i.kind==='broken_symlink'||i.kind==='broken_skill_link');

  const TYPE_TABS=[
    {key:'same-name',emoji:'📛',label:'同名',count:sameNameGroups.length},
    {key:'upstream',emoji:'🔗',label:'上游',count:upstreamAll.length},
    {key:'changes',emoji:'🔄',label:'变更',count:changedSkills.length},
    {key:'broken',emoji:'🔴',label:'损坏',count:brokenLinks.length},
  ];
  // 当前 tab 无数据时落到第一个有数据的，避免空页
  const curDef=TYPE_TABS.find(t=>t.key===_issueTypeTab);
  if(!curDef||curDef.count===0){
    _issueTypeTab=TYPE_TABS.find(t=>t.count>0)?.key||'same-name';
  }

  let tabHtml='<div class="issue-tabs">';
  TYPE_TABS.forEach(t=>{
    const isActive=_issueTypeTab===t.key;
    tabHtml+=`<button class="issue-tab ${isActive?'active':''}" onclick="_issueTypeTab='${t.key}';_issueShowAll=false;renderIssues()"><span>${t.emoji}</span><span>${t.label}</span>${t.count?`<b>${t.count}</b>`:''}</button>`;
  });
  tabHtml+='</div>';

  // 截断：默认前 LIMIT 条，显式标注「显示前 N / 共 M」，消除隐性截断
  const LIMIT=12;
  const slc=(a)=>_issueShowAll?a:a.slice(0,LIMIT);
  const visibleSameName=slc(sameNameGroups);
  const visibleUpstreams=slc(upstreamAll);
  const visibleChanges=changes?{...changes,changed:slc(changedSkills)}:null;

  // 缓存新鲜度：区分"扫描数据时间"和"预案生成时间"；两者不同日说明扫描失败了用旧缓存
  const lastScan=scanResult?.scanned_at;
  const planTime=executionPlan?.generated_at||'';
  const scanStale=lastScan&&planTime&&lastScan.slice(0,10)!==planTime.slice(0,10);
  const freshnessHtml=lastScan?`<div class="notice-line"><span>⏱ 扫描数据时间 ${fmtScanTime(lastScan)}${scanStale?' · ⚠️ 与本次预案不同日，扫描可能失败、用了旧缓存':''}（点「开始整理」刷新）</span></div>`:'';
  let h=executionHtml+planHtml+freshnessHtml+tabHtml;

  const curTab=TYPE_TABS.find(t=>t.key===_issueTypeTab);
  const totalForTab=curTab?.count||0;
  if(totalForTab===0){
    h+=`<div class="empty" style="padding:30px 0">✅ 没有发现问题</div>`;
    $('issues-list').innerHTML=h;return;
  }
  if(totalForTab>LIMIT){
    if(_issueShowAll){
      h+=`<div class="notice-line"><span>显示全部 ${totalForTab} 条</span><button class="btn btn-sm" onclick="_issueShowAll=false;renderIssues()">只看前 ${LIMIT}</button></div>`;
    }else{
      h+=`<div class="notice-line"><span>显示前 ${LIMIT} / 共 ${totalForTab} 条</span><button class="btn btn-sm btn-primary" onclick="_issueShowAll=true;renderIssues()">显示全部 ${totalForTab}</button></div>`;
    }
  }

  // ── Upstream section ──
  // 本地独立检测（.git remote / .skill-source.env / lock）不依赖 GitHub API，
  // 未配 token 时为 status=unknown 但带 repo。这里一并展示，让没配 token 的用户
  // 也能看到"哪些 skill 有可追踪来源"；只有 status=outdated 才标"过时"。
  const upstreamDetected=visibleUpstreams;
  if(_issueTypeTab==='upstream'&&upstreamDetected.length){
    const outdated=upstreamDetected.filter(s=>s.status==='outdated');
    const pendingCompare=upstreamDetected.filter(s=>s.status!=='outdated');
    const headTag=outdated.length?`${outdated.length} 个过时`:`${pendingCompare.length} 个待比对`;
    h+=`<section class="issue-section"><div class="issue-section-head"><div><h3>🔗 上游追踪</h3><p>只提示可复核更新，不自动改文件。未配置 token 时仅展示检测到的来源。</p></div><span>${headTag}</span></div>`;
    h+=`<div class="card issue-list-card">`;
    if(outdated.length){
      const SOURCE_LABEL={
        'steal-meta':['Steal安装','通过 Skill Dashboard 从 GitHub 安装'],
        'git-remote':['Git仓库','目录本身是一个 Git 仓库，可 git pull'],
        'vercel-lock':['NPX/Vercel','通过 npx skills add 安装，记录在 ~/.agents/.skill-lock.json'],
        'unknown':['未知','无法识别上游来源']
      };
      // Group symlink copies that point to the same canonical copy so we don't show N identical update buttons.
      const upstreamGroups={};
      outdated.forEach(s=>{
        const key=s.canonical_dir||s.dir;
        if(!upstreamGroups[key]){
          upstreamGroups[key]={...s, copies:[]};
        }
        upstreamGroups[key].copies.push({dir:s.dir,is_symlink:s.is_symlink,link_target:s.link_target});
      });
      Object.values(upstreamGroups).forEach(s=>{
        const cat=_dirCategory(s.dir);
        const cm=CAT_META[cat]||CAT_META.unknown;
        const [sourceLabel,sourceTitle]=SOURCE_LABEL[s.source||'unknown']||SOURCE_LABEL['unknown'];
        const canonical=s.canonical_dir||s.dir;
        const updateDir=canonical;
        const updateLabel=s.source==='vercel-lock'?'NPX 更新':s.source==='git-remote'?'Git 更新':'更新';
        const copyCount=s.copies.length;
        const copyHint=copyCount>1?`&#10;共 ${copyCount} 个副本: ${s.copies.map(c=>c.dir.replace(/^\/Users\/[^/]+/,'~')).join(', ')}`:'';
        h+=`<div class="issue-row">
          ${issueDirBadge(canonical)}
          <div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px"><span style="font-size:13px;font-weight:500">${s.name}</span>${copyCount>1?`<span style="font-size:10px;color:var(--text-muted);background:var(--bg-card-alt);padding:1px 5px;border-radius:999px" title="${esc(copyHint)}">+${copyCount-1} 副本</span>`:''}</div><div style="font-size:11px;color:var(--text-muted)">${s.repo}</div><div style="font-size:10px;color:var(--text-muted);font-family:monospace" title="当前版本 → 上游最新版本">${s.installed_commit?.slice(0,8)||'?'} → ${s.latest_commit?.slice(0,8)||'?'}</div>${renderIssuePath(canonical)}</div>
          <span style="font-size:11px;color:var(--red)">⚠ 过时</span>
          <span style="font-size:10px;color:var(--text-muted);white-space:nowrap" title="${esc(sourceTitle)}${copyHint}">${sourceLabel}</span>
          <button class="btn btn-sm" onclick="updateUpstream('${esc(s.name)}',{target:this},'${esc(updateDir)}')">${updateLabel}</button></div>`;
      });
    }else if(pendingCompare.length){
      h+=`<div style="font-size:12px;color:var(--text-muted);padding:6px 0">检测到 ${pendingCompare.length} 个 GitHub 来源，未配置 token 暂无法比对版本。</div>`;
      pendingCompare.slice(0,12).forEach(s=>{
        const canonical=s.canonical_dir||s.dir;
        h+=`<div class="issue-row">${issueDirBadge(canonical)}<div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px"><span style="font-size:13px;font-weight:500">${s.name}</span></div><div style="font-size:11px;color:var(--text-muted)">${s.repo}</div>${renderIssuePath(canonical)}</div><span style="font-size:11px;color:var(--text-muted)">版本待比对</span></div>`;
      });
    }else{
      h+=`<div style="font-size:12px;color:var(--text-muted);padding:8px 0">${upstreamDetected.length} 个 skill 追踪到上游仓库，均无过时版本</div>`;
    }
    h+=`</div></section>`;
  }

  // ── Same-name section ──
  if(_issueTypeTab==='same-name'&&visibleSameName.length){
    h+=`<section class="issue-section"><div class="issue-section-head"><div><h3>📛 同名分析</h3><p>同名 ≠ 内容相同 ≠ 可删；跨 Agent 重复多为正常多端部署，单 Agent 内重复才需重点核查。</p></div><div style="display:flex;align-items:center;gap:8px"><span>${visibleSameName.length} 组</span><button class="btn btn-sm btn-danger" onclick="deleteSelectedIssues()" style="font-size:10px;padding:2px 8px">删除选中</button></div></div>
      <div class="issue-card-grid">`;

    // Flat: one card per duplicate name. Cross-agent AND within-agent
    // locations render together; each row's dir badge already shows the agent,
    // so we no longer collapse into per-agent buckets (that used to hide
    // cross-agent duplicates when no single agent held 2+ copies).
    visibleSameName.forEach(dup=>{
      const locs=dup.locations;
      const uid='sn-'+Math.random().toString(36).slice(2,8);
      _compareData[uid]=locs.map(l=>({name:l.name||dup.name,dir:l.dir}));
      const crossAgent=dup.agent_count>=2?`<span style="font-size:10px;color:var(--amber);background:var(--bg-card-alt);padding:1px 6px;border-radius:999px">跨 ${dup.agent_count} Agent</span>`:'';
      h+=`<div style="border:1px solid var(--border-subtle);border-radius:8px;overflow:hidden">
        <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-card-alt);cursor:pointer" onclick="var b=this.parentElement.querySelector('.issue-group-body');b.style.display=b.style.display==='none'?'block':'none'">
          <span style="font-size:10px;color:var(--text-muted)">▶</span>
          <span style="flex:1;font-size:12px;font-weight:600">${dup.name} · ${locs.length} 个目录</span>
          ${crossAgent}
          <button class="btn btn-sm btn-primary" onclick="event.stopPropagation();compareSkills(this,'${uid}')" style="font-size:9px;padding:2px 8px">并排对比</button>
        </div>
        <div id="${uid}" class="issue-group-body" style="display:none;padding:6px 12px 10px">
          ${locs.map(loc=>{
            const sn=loc.name||dup.name;
            const sKey=sn+'|'+loc.dir;
            return `<div style="display:grid;grid-template-columns:auto auto minmax(0,1fr) auto auto;gap:6px;padding:5px 0;border-bottom:1px solid var(--border-subtle);align-items:center">
              ${issueDirBadge(loc.dir)}
              <input type="checkbox" class="issue-check" data-skey="${esc(sKey)}" data-sname="${esc(sn)}" data-sdir="${esc(loc.dir)}" ${_issueSelected.has(sKey)?'checked':''} onchange="toggleIssueSelect(this)" style="cursor:pointer">
              <div style="min-width:0">
                <span style="font-size:12px;font-weight:500;color:var(--indigo);cursor:pointer" onclick="showSkill('${esc(sn)}','${esc(loc.dir)}')">${sn}</span>
                ${renderIssuePath(loc.dir)}
              </div>
              <button class="btn btn-sm" onclick="showSkill('${esc(sn)}','${esc(loc.dir)}')" style="font-size:9px;padding:2px 6px">查看</button>
              <button class="btn btn-sm btn-danger" onclick="deleteSkill('${esc(sn)}',this,'${esc(loc.dir)}')" style="font-size:9px;padding:2px 6px">删</button>
            </div>`;
          }).join('')}
        </div>
      </div>`;
    });

    h+=`</div></section>`;
  }

  // ── Broken symlinks section ──
  if (_issueTypeTab==='broken' && brokenLinks.length) {
    h += `<section class="issue-section"><div class="issue-section-head"><div><h3>🔴 损坏链接</h3><p>这些 symlink 或 SKILL.md 链接指向的目标已不存在。</p></div><span>${brokenLinks.length} 个</span></div>
      <div class="card issue-list-card">`;
    brokenLinks.forEach(issue => {
      const kindLabel=issue.kind==='broken_skill_link'?'目录壳':'断链';
      h += `<div class="issue-row">
        <div style="flex:1"><div style="font-size:13px;font-weight:500">${issue.name}</div>
          <div style="font-size:11px;color:var(--text-muted)">${kindLabel} · ${issue.dir ? issue.dir.replace(/^\/Users\/[^/]+/, '~') : ''}</div></div>
        <button class="btn btn-sm btn-danger" onclick="deleteSkill('${esc(issue.name)}',this)">删除</button></div>`;
    });
    h += `</div></section>`;
  }

  // ── Content changes section ──
  if(_issueTypeTab==='changes'&&visibleChanges?.changed?.length){
    const changeNames=visibleChanges.changed.map(c=>c.name);
    h+=`<section class="issue-section"><div class="issue-section-head"><div><h3>🔄 内容变更</h3><p>SKILL.md 内容与安装时记录的哈希不同。</p></div><span>${visibleChanges.changed.length} 个</span></div>`;
    h+=`<div class="card issue-list-card"><div style="display:flex;justify-content:flex-end;margin-bottom:8px"><button class="btn btn-sm btn-primary" onclick="batchRehash([${changeNames.map(n=>`'${esc(n)}'`).join(',')}],'内容变更')" style="font-size:10px;padding:2px 8px">全部重新记录</button></div>`;
    visibleChanges.changed.forEach(c=>{
      h+=`<div class="issue-row">
        <div style="flex:1"><div style="font-size:13px;font-weight:500">${c.name}</div>
          <div style="font-size:11px;color:var(--text-muted)">上次记录: ${c.last_recorded||'未知'}</div></div>
        <button class="btn btn-sm" onclick="rehashSkill('${esc(c.name)}',this)">重新记录</button></div>`;
    });
    h+=`</div></section>`;
  }

  $('issues-list').innerHTML=h;
}

async function fixSkill(name,action,btn){
  btn.disabled=true;btn.textContent='修复中...';
  try{const r=await fetch(`/api/skill/${name}/fix`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    const d=await r.json();if(d.ok){toast(`${name} 已修复`);invalidateTargetsCache();await loadData()}else toast(d.error||'修复失败','error')}
  catch(e){toast('修复失败','error')}finally{btn.disabled=false;btn.textContent='修复'}
}

async function promptAddDesc(name,btn){
  const desc=prompt(`为 "${name}" 添加简短描述:`);
  if(!desc)return;
  btn.disabled=true;btn.textContent='保存中...';
  try{const r=await fetch(`/api/skill/${name}/fix`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'add_description',description:desc})});
    const d=await r.json();if(d.ok){toast(`${name} 描述已添加`);invalidateTargetsCache();await loadData()}else toast(d.error||'添加失败','error')}
  catch(e){toast('添加失败','error')}finally{btn.disabled=false;btn.textContent='补描述'}
}

async function rehashSkill(name,btn){
  btn.disabled=true;btn.textContent='记录中...';
  try{const r=await fetch(`/api/skill/${name}/rehash`,{method:'POST'});const d=await r.json();
    if(d.ok){toast(`${name} 哈希已更新`);invalidateTargetsCache();await loadData()}else toast(d.error||'更新失败','error')}
  catch(e){toast('更新失败','error')}finally{btn.disabled=false;btn.textContent='重新记录'}
}

async function deleteSkill(name,btn,target){
  if(!confirm(`确认删除 skill "${name}"？`))return;
  if(btn){btn.disabled=true;btn.textContent='删除中...';}
  const url=target?`/api/skill/${name}?target=${encodeURIComponent(target)}`:`/api/skill/${name}`;
  try{const r=await fetch(url,{method:'DELETE'});const d=await r.json();
    if(d.ok){
      toast(`已删除 ${name}`);
      if(target&&typeof refreshIssuesAfterDelete==='function'&&document.querySelector('#view-issues')?.style.display!=='none'){
        await refreshIssuesAfterDelete([target]);
      }else{
        invalidateTargetsCache();
        clearGlobalSearchCache();
        await loadData();
      }
    }else toast(d.error||'删除失败','error')}
  catch(e){toast('删除失败','error')}finally{if(btn){btn.disabled=false;btn.textContent='删除';}}
}

async function batchDeleteNames(names,label,targets){
  if(!names||!names.length)return;
  if(!confirm(`确认批量删除 ${names.length} 个${label?`「${label}」`:''} skill？\n\n将移到回收站：\n${names.join(', ')}`))return;
  let ok=0,fail=0;
  for(let i=0;i<names.length;i++){
    const name=names[i];
    const t=targets&&targets[i]?targets[i]:null;
    const url=t?`/api/skill/${name}?target=${encodeURIComponent(t)}`:`/api/skill/${name}`;
    try{const r=await fetch(url,{method:'DELETE'});const d=await r.json();d.ok?ok++:fail++;}
    catch{fail++;}
  }
  toast(`${label||'批量删除'}: 已删 ${ok} 个${fail>0?`，${fail} 个失败`:''}`);
  const changedDirs=[...new Set((targets||[]).filter(Boolean))];
  if(changedDirs.length&&typeof refreshIssuesAfterDelete==='function'&&document.querySelector('#view-issues')?.style.display!=='none'){
    await refreshIssuesAfterDelete(changedDirs);
  }else{
    invalidateTargetsCache();
    clearGlobalSearchCache();
    await loadData();
  }
}
async function batchRehash(names,label){
  if(!names||!names.length)return;
  if(!confirm(`确认批量重新记录 ${names.length} 个${label?`「${label}」`:''} skill 的哈希？`))return;
  let ok=0,fail=0;
  for(const name of names){
    try{const r=await fetch(`/api/skill/${name}/rehash`,{method:'POST'});const d=await r.json();d.ok?ok++:fail++;}
    catch{fail++;}
  }
  toast(`${label||'重新记录'}: ${ok} 个成功${fail>0?`，${fail} 个失败`:''}`);
  invalidateTargetsCache();
  await loadData();
}

async function cleanupAll(){
  const cleanups=health?.cleanup_candidates||[];
  if(!cleanups.length)return;
  if(!confirm(`确认删除 ${cleanups.length} 个清理候选？`))return;
  let ok=0,fail=0;
  for(const name of cleanups){
    try{const r=await fetch(`/api/skill/${name}`,{method:'DELETE'});const d=await r.json();d.ok?ok++:fail++}
    catch{fail++}
  }
  toast(`已删除 ${ok} 个${fail?`，${fail} 个失败`:''}`);
  invalidateTargetsCache();
  clearGlobalSearchCache();
  await loadData();
}

/* ── Trash (垃圾站) ── */
let trashItems=[];
async function loadTrash(){
  try{
    const d=await fetch('/api/trash').then(r=>r.json());
    trashItems=d.items||[];
    $('badge-trash').textContent=trashItems.length;
    renderTrash();
  }catch{}
}
function renderTrash(){
  if(!$('trash-list'))return;
  let h=`<div style="display:flex;gap:8px;margin-bottom:12px;align-items:center">
    <h3 style="font-size:14px;font-weight:600">🗑 垃圾站</h3>
    <span style="font-size:11px;color:var(--text-muted)">${trashItems.length} 个已删除项</span>
    <span style="flex:1"></span>
    <button class="btn btn-sm btn-danger" onclick="emptyTrash()" ${trashItems.length?'':'disabled'} style="font-size:10px">清空全部</button>
  </div>`;
  if(!trashItems.length){h+='<div class="empty">垃圾站为空</div>';$('trash-list').innerHTML=h;return}
  trashItems.forEach(t=>{
    const dateStr=t.trashed_at?t.trashed_at.replace(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/,'$1-$2-$3 $4:$5:$6'):'';
    const countLabel=t.kind==='symlink'?'链接入口':`${t.skill_count||1} skill${(t.skill_count||1)>1?'s':''}`;
    h+=`<div class="card" style="margin-bottom:8px">
      <div style="display:flex;align-items:center;gap:10px">
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:13px">${t.name}</div>
          <div style="font-size:11px;color:var(--text-muted)">原路径: ${t.original_path||'未知'} · ${countLabel} · ${dateStr}</div>
        </div>
        <button class="btn btn-sm btn-primary" onclick="restoreTrash('${t.id}')" style="font-size:10px">恢复</button>
        <button class="btn btn-sm btn-danger" onclick="permanentDeleteTrash('${t.id}','${esc(t.name)}')" style="font-size:10px">永久删除</button>
      </div>
    </div>`;
  });
  $('trash-list').innerHTML=h;
}
async function restoreTrash(id){
  try{
    const r=await fetch(`/api/trash/${encodeURIComponent(id)}/restore`,{method:'POST',headers:{'Content-Type':'application/json'}});
    const d=await r.json();
    if(d.ok){toast(`已恢复: ${d.restored_to}`);await loadTrash();invalidateTargetsCache();clearGlobalSearchCache();await loadData()}
    else{toast(d.error||'恢复失败','error')}
  }catch{toast('恢复失败','error')}
}
async function permanentDeleteTrash(id,name){
  if(!confirm(`永久删除 ${name}？不可恢复！`))return;
  try{
    const r=await fetch(`/api/trash/${encodeURIComponent(id)}`,{method:'DELETE'});
    const d=await r.json();
    if(d.ok){toast('已永久删除');await loadTrash()}
    else{toast(d.error||'删除失败','error')}
  }catch{toast('删除失败','error')}
}
async function emptyTrash(){
  if(!trashItems.length)return;
  if(!confirm(`清空垃圾站中的 ${trashItems.length} 个已删除项？\n\n这一步不可恢复。`))return;
  try{
    const r=await fetch('/api/trash',{method:'DELETE'});
    const d=await r.json();
    if(d.ok){
      toast(`已清空垃圾站：${d.deleted||0} 项${d.failed?`，${d.failed} 项失败`:''}`);
      await loadTrash();
    }else{toast(d.error||'清空失败','error')}
  }catch(e){toast('清空失败: '+e.message,'error')}
}
