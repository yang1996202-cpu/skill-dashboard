// Category tab state
let _issueCategoryTab='all'; // 'all' | 'user' | 'marketplace' | 'cache' | 'cross-copy' | 'project'

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
function _dirCategory(dirPath){
  if(!dirPath) return 'unknown';
  const t=targets.find(t=>t.path===dirPath);
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
  hidden:{emoji:'🚫',label:'缓存/内置',desc:'缓存、备份或测试样例，只在来源市场或全部视图里看'},
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
function sourceIsMine(t){ return sourceIsDaily(t); }
function sourceIsSourceMarket(t){
  const c=t?.category||'unknown';
  return c==='marketplace'||c==='cache'||c==='cross-copy'||c==='commands';
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
    'mine':sourceIsMine,
    'source-market':sourceIsSourceMarket,
    'all':()=>true,
  }[viewMode]||sourceIsMine;
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
function setSourceViewMode(mode){
  _sourceViewMode=['mine','source-market','all'].includes(mode)?mode:'mine';
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
    const sn=scanResult.duplicates_same_name?.length||0;
    const up=scanResult.upstream_sources?.length||0;
    const cc=scanResult.content_changes?.total_changed||0;
    const scopeLabel=scanResult.scope==='daily'?'我的目录':'全部目录';
    const tokenOk=scanResult.github_token_configured;
    statusHtml=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;font-size:11px;color:var(--text-muted)">
      <span>扫描：${scopeLabel} · ${scanResult.scanned_dirs} 目录 · ${(scanResult.duration_ms/1000).toFixed(1)}s</span>
      <span>同名 ${sn} · 上游 ${up} · 变更 ${cc}</span>
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
          ${scopeBtn('deep','全部目录')}
          ${scopeBtn('daily','我的目录')}
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

async function runEvidenceBundle(scope='daily',opts={}){
  // Kept for compatibility; now a thin wrapper around the unified cleanup flow.
  _scanScope=scope;
  localStorage.setItem('sd-scan-scope',_scanScope);
  renderScanConfig();
  await startCleanupFlow();
}

async function runScan(scope='deep',opts={}){
  try{
    const scanTargets=scope==='deep'?targets:getVisibleSourceTargets();
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
      _issueCategoryTab='all';
      _issueShowAll=false;
    }
    if(!opts.deferRender)renderIssues();
    updateDiagBadges();
    if(!opts.silent)toast(`扫描完成: ${r.scanned_dirs} 目录 · ${r.duration_ms}ms`);
  }catch(e){toast('扫描失败: '+e.message,'error')}
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
    if(!opts.silent)toast(`${scope==='deep'?'全量目录审计':'日常治理计划'}完成: ${r.summary?.directories||0} 目录`);
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
  const scopeLabel=cleanupPlan.scope==='deep'?'全量清理审计':'日常清理计划';
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
    const title=a.operation==='mark_multi_agent_deploy'
      ? `${a.skill_name||''} · ${a.from_state||''} · 多端部署`
      : a.skill_name
        ? `${a.skill_name} · ${a.from_state||''} → 垃圾站`
        : `${a.agent||''} · ${a.from_state||''} → 垃圾站`;
    return `<div style="border:1px solid var(--border-subtle);border-radius:8px;padding:8px;background:var(--bg-card-alt)">
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
    </div>`;
  };

  const renderPhaseCard=(phase,limit=10)=>{
    const actions=(phase.actions||[]).slice(0,_issueShowAll?999:limit);
    const hidden=Math.max(0,(phase.actions||[]).length-actions.length);
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
        ${hidden?`<div style="font-size:11px;color:var(--text-muted);padding:4px 0">还有 ${hidden} 个动作未展开，点击“显示全量”查看。</div>`:''}
      </div>
    </div>`;
  };
  const candidatePhases=sortedPhases.filter(p=>p.key==='candidate');
  const evidencePhases=sortedPhases.filter(p=>p.key!=='candidate');
  const candidateHtml=candidatePhases.length
    ? candidatePhases.map(p=>renderPhaseCard(p,16)).join('')
    : '<div class="empty" style="padding:18px 0">当前没有推荐移入垃圾站的候选。</div>';
  const evidenceCount=evidencePhases.reduce((sum,p)=>sum+(p.action_count||0),0);
  const evidenceHtml=evidencePhases.length
    ? `<details style="margin-top:10px"><summary style="font-size:12px;color:var(--text-muted);cursor:pointer">查看未处理原因和保护规则（${evidenceCount} 项）</summary><div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;margin-top:8px">${evidencePhases.map(p=>renderPhaseCard(p,8)).join('')}</div></details>`
    : '';
  const rules=(executionPlan.rules||[]).map(r=>`<div style="font-size:11px;color:var(--text-muted);line-height:1.5;padding:3px 0">${escapeHtml(r)}</div>`).join('');
  return `<div style="margin-bottom:16px">
    <div class="card" style="border-left:3px solid var(--red)">
      <div class="card-head">
        <div>
          <h3>人工处理区</h3>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5">${strategyLabel} · 线索先给推荐，人再勾选；只移入垃圾站，可恢复 · ${executionPlan.generated_at||''}</div>
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
        <div class="scope-card muted"><div class="scope-name"><span>直接删除</span><b>0</b></div><div class="scope-desc">不永久删除</div></div>
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
  const tabBefore=_issueCategoryTab;
  const showAllBefore=_issueShowAll;
  await loadTrash();
  await refreshAfterDelete(changedPaths||[]);
  if(hadCleanupPlan){
    await runCleanupPlan(scope,{silent:true,deferRender:true});
  }
  await runScan(scope,{silent:true,deferRender:true,preserveIssueView:true});
  _issueCategoryTab=tabBefore;
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

  // ── Build per-category counts ──
  const catKeys=['user','marketplace','cache','cross-copy','project','unknown'];
  const catCounts={}; catKeys.forEach(k=>catCounts[k]=0);
  catCounts['all']=0;

  // Count same-name issues per category (count GROUPS, not locations)
  sameName.forEach(dup=>{
    if(dup.locations.length<2) return;
    const cats=new Set();
    dup.locations.forEach(loc=>{cats.add(_dirCategory(loc.dir))});
    cats.forEach(c=>{catCounts[c]=(catCounts[c]||0)+1});
    catCounts['all']++;
  });

  // Count actionable upstream issues only
  upstreams.filter(s=>s.status==='outdated').forEach(s=>{
    const c=_dirCategory(s.dir);
    catCounts[c]=(catCounts[c]||0)+1;
    catCounts['all']++;
  });

  // Count content changes
  const changedSkills=changes?.changed||[];
  changedSkills.forEach(c=>{
    // content changes come from scan, dir may not be in item; count generically
    catCounts['all']++;
  });

  // ── Render category tabs ──
  const tabOrder=['all','user','marketplace','cache','cross-copy','project'];
  let tabHtml='<div style="display:flex;gap:2px;margin-bottom:12px;flex-wrap:wrap;border-bottom:2px solid var(--border);padding-bottom:0">';
  tabOrder.forEach(key=>{
    const isActive=_issueCategoryTab===key;
    const meta=key==='all'?{emoji:'📋',label:'全部'}:CAT_META[key]||{emoji:'❓',label:key};
    const count=catCounts[key]||0;
    const bg=isActive?'var(--accent)':'transparent';
    const fg=isActive?'#fff':'var(--text-muted)';
    tabHtml+=`<button onclick="_issueCategoryTab='${key}';_issueShowAll=false;renderIssues()" style="display:flex;align-items:center;gap:4px;padding:6px 12px;font-size:12px;font-weight:${isActive?600:400};color:${fg};background:${bg};border:none;border-radius:6px 6px 0 0;cursor:pointer;transition:all .15s;${isActive?'border-bottom:2px solid var(--accent);margin-bottom:-2px':''}">${meta.emoji} ${meta.label}${count?` <span style="font-size:10px;opacity:.8">${count}</span>`:''}</button>`;
  });
  tabHtml+='</div>';

  // ── Filter data by selected tab ──
  const tab=_issueCategoryTab;
  const matchCat=tab==='all'?()=>true:(dir)=>_dirCategory(dir)===tab;

  // Filter same-name duplicates: scoped to selected category
  // On "all" tab: show everything (cross-agent + within-agent)
  // On specific tab: only groups whose locations ALL belong to this category
  const filteredSameName=tab==='all'?sameName:sameName.filter(dup=>
    dup.locations.length>=2 && dup.locations.every(l=>matchCat(l.dir))
  );

  // Filter upstreams
  const filteredUpstreams=tab==='all'?upstreams:upstreams.filter(s=>matchCat(s.dir));

  // Filter content changes
  const filteredChanges=tab==='all'?changes:(changes?{...changes,changed:(changes.changed||[]).filter(c=>{
    // content changes may not have dir; for filtered view, just show all or skip
    return true; // content changes are per-current-dir, keep as-is for now
  })}:null);

  const visibleUpstreams=_issueShowAll?filteredUpstreams:filteredUpstreams.filter(s=>s.status==='outdated').slice(0,8);
  const visibleSameName=_issueShowAll?filteredSameName:filteredSameName.slice(0,24);
  const visibleChanges=_issueShowAll?filteredChanges:(filteredChanges?{...filteredChanges,changed:(filteredChanges.changed||[]).slice(0,8)}:null);
  const originalActionable=filteredSameName.length+filteredUpstreams.filter(s=>s.status==='outdated').length+(filteredChanges?.changed?.length||0);
  const visibleActionable=visibleSameName.length+visibleUpstreams.filter(s=>s.status==='outdated').length+(visibleChanges?.changed?.length||0);
  const hiddenActionable=Math.max(0,originalActionable-visibleActionable);

  // ── Build HTML ──
  let h=executionHtml+planHtml+tabHtml;

  const hasFilteredData=originalActionable>0;
  if(!hasFilteredData){
    const meta=tab==='all'?{emoji:'📋',label:'全部'}:CAT_META[tab]||{emoji:'❓',label:tab};
    h+=`<div class="empty">✅ ${meta.emoji} ${meta.label} 分类下未发现问题</div>`;
    $('issues-list').innerHTML=h;return;
  }

  // Summary
  const totalFiltered=originalActionable;
  h+=`<div class="card" style="border-left:3px solid var(--accent)">
    <div style="display:flex;align-items:center;gap:12px;padding:4px 0">
      <div style="font-size:28px;font-weight:700;color:var(--accent)">${totalFiltered}</div>
      <div style="flex:1">
        <div style="font-size:13px;font-weight:600">人工判断线索</div>
        <div style="font-size:11px;color:var(--text-muted)">${scanResult?scanResult.scanned_dirs+' 个目录':''} · ${_issueShowAll?'全量展示':'重点预览'} · 初筛不等于可删</div>
      </div>
      ${hiddenActionable?`<button class="btn btn-sm" onclick="_issueShowAll=true;renderIssues()">显示全量 ${totalFiltered}</button>`:`${_issueShowAll?'<button class="btn btn-sm" onclick="_issueShowAll=false;renderIssues()">回到重点</button>':''}`}
    </div>
  </div>`;
  if(hiddenActionable&&!_issueShowAll){
    h+=`<div class="notice-line"><span>当前仅渲染 ${visibleActionable} 个重点线索，其余 ${hiddenActionable} 个留在全量视图。相似和同名不等于可删除。</span><button class="btn btn-sm" onclick="_issueShowAll=true;renderIssues()">显示全部</button></div>`;
  }

  // ── Upstream section ──
  if(visibleUpstreams.length){
    const outdated=visibleUpstreams.filter(s=>s.status==='outdated');
    h+=`<div style="margin-bottom:16px"><div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:8px;padding-left:4px">🔗 上游追踪</div>`;
    h+=`<div class="card" style="min-width:280px;flex:1"><div class="card-head"><h3>上游追踪</h3><span class="sub">${outdated.length} 个过时</span></div>`;
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
        h+=`<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent)">
          <span style="font-size:12px" title="${cm.label}">${cm.emoji}</span>
          <div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px"><span style="font-size:13px;font-weight:500">${s.name}</span>${copyCount>1?`<span style="font-size:10px;color:var(--text-muted);background:var(--bg-card-alt);padding:1px 5px;border-radius:999px" title="${esc(copyHint)}">+${copyCount-1} 副本</span>`:''}</div><div style="font-size:11px;color:var(--text-muted)">${s.repo}</div><div style="font-size:10px;color:var(--text-muted);font-family:monospace" title="当前版本 → 上游最新版本">${s.installed_commit?.slice(0,8)||'?'} → ${s.latest_commit?.slice(0,8)||'?'}</div>${renderIssuePath(canonical)}</div>
          <span style="font-size:11px;color:var(--red)">⚠ 过时</span>
          <span style="font-size:10px;color:var(--text-muted);white-space:nowrap" title="${esc(sourceTitle)}${copyHint}">${sourceLabel}</span>
          <button class="btn btn-sm" onclick="updateUpstream('${esc(s.name)}',{target:this},'${esc(updateDir)}')">${updateLabel}</button></div>`;
      });
    }else{
      h+=`<div style="font-size:12px;color:var(--text-muted);padding:8px 0">${visibleUpstreams.length} 个 skill 追踪到上游仓库，均无过时版本</div>`;
    }
    h+=`</div></div>`;
  }

  // ── Same-name section ──
  if(visibleSameName.length){
    h+=`<div style="margin-bottom:16px"><div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:8px;padding-left:4px">📛 同名分析</div>
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-start;padding-bottom:4px">`;

    // Within-agent same-name: groups with 2+ locations for the SAME agent
    const snByAgent={};
    visibleSameName.forEach(dup=>{
      if(dup.locations.length<2) return;
      dup.locations.forEach(loc=>{
        const a=loc.agent||'其他';
        if(!snByAgent[a]) snByAgent[a]=new Set();
        snByAgent[a].add(dup);
      });
    });
    const snAgentList=Object.entries(snByAgent)
      .map(([agent,dupSet])=>{
        const validDups=[...dupSet].filter(dup=>{
          const agentLocs=dup.locations.filter(l=>(l.agent||'其他')===agent);
          return agentLocs.length>=2;
        });
        return [agent,validDups];
      })
      .filter(([,dups])=>dups.length>0)
      .sort((a,b)=>b[1].length-a[1].length);
    snAgentList.forEach(([agent,dups])=>{
      h+=`<div class="card" style="min-width:320px;flex:1"><div class="card-head" style="display:flex;align-items:center;gap:8px"><h3>📛 ${agent} 内同名</h3><span class="sub">${dups.length} 组</span><span style="flex:1"></span><button class="btn btn-sm btn-danger" onclick="deleteSelectedIssues()" style="font-size:10px;padding:2px 8px">删除选中</button></div>
        <div style="font-size:11px;color:var(--text-muted);padding-bottom:8px">${agent} 下同名 skill 分析</div>`;
      dups.forEach(dup=>{
        const agentLocs=dup.locations.filter(l=>(l.agent||'其他')===agent);
        const uid='sn-'+Math.random().toString(36).slice(2,8);
        _compareData[uid]=agentLocs.map(l=>({name:l.name||dup.name,dir:l.dir}));
        h+=`<div style="border:1px solid var(--border-subtle);border-radius:8px;margin-bottom:8px;overflow:hidden">
          <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-card-alt);cursor:pointer" onclick="var b=this.parentElement.querySelector('.issue-group-body');b.style.display=b.style.display==='none'?'block':'none'">
            <span style="font-size:10px;color:var(--text-muted)">▶</span>
            <span style="flex:1;font-size:12px;font-weight:600">${dup.name} · ${agentLocs.length} 个目录</span>
            <button class="btn btn-sm btn-primary" onclick="event.stopPropagation();compareSkills(this,'${uid}')" style="font-size:9px;padding:2px 8px">并排对比</button>
          </div>
          <div style="font-size:11px;color:var(--text-muted);line-height:1.5;padding:6px 10px;background:var(--bg-card-alt);border-top:1px solid var(--border-subtle)">
            同名原因：目录中存在相同 skill 文件夹名。<span style="color:var(--amber)">同名不代表内容相同，也不代表可删除。</span>
          </div>
          <div id="${uid}" class="issue-group-body" style="display:none;padding:6px 12px 10px">
            ${agentLocs.map(loc=>{
              const sn=loc.name||dup.name;
              const sKey=sn+'|'+loc.dir;
              const sCat=_dirCategory(loc.dir);
              const sCm=CAT_META[sCat]||CAT_META.unknown;
              return `<div style="display:grid;grid-template-columns:auto auto minmax(0,1fr) auto auto;gap:6px;padding:5px 0;border-bottom:1px solid var(--border-subtle);align-items:center">
                <span style="font-size:10px" title="${sCm.label}">${sCm.emoji}</span>
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
      h+=`</div>`;
    });

    h+=`</div></div>`;
  }

  // ── Broken symlinks section ──
  const brokenLinks = issues.filter(i => i.kind === 'broken_symlink' || i.kind === 'broken_skill_link');
  if (brokenLinks.length) {
    h += `<div style="margin-bottom:16px"><div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:8px;padding-left:4px">🔴 损坏链接</div>
      <div class="card" style="min-width:280px;flex:1"><div class="card-head" style="display:flex;align-items:center;gap:8px"><h3>损坏的 skill 入口</h3><span class="sub">${brokenLinks.length} 个</span></div>
      <div style="font-size:11px;color:var(--text-muted);padding-bottom:8px">这些 symlink 或 SKILL.md 链接指向的目标已不存在，可移入垃圾站。</div>`;
    brokenLinks.forEach(issue => {
      const kindLabel=issue.kind==='broken_skill_link'?'目录壳':'断链';
      h += `<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent)">
        <div style="flex:1"><div style="font-size:13px;font-weight:500">${issue.name}</div>
          <div style="font-size:11px;color:var(--text-muted)">${kindLabel} · ${issue.dir ? issue.dir.replace(/^\/Users\/[^/]+/, '~') : ''}</div></div>
        <button class="btn btn-sm btn-danger" onclick="deleteSkill('${esc(issue.name)}',this)">删除</button></div>`;
    });
    h += `</div></div>`;
  }

  // ── Content changes section ──
  if(visibleChanges?.changed?.length){
    const changeNames=visibleChanges.changed.map(c=>c.name);
    h+=`<div style="margin-bottom:16px"><div style="font-size:12px;font-weight:600;color:var(--text-muted);margin-bottom:8px;padding-left:4px">🔄 内容变更</div>`;
    h+=`<div class="card" style="min-width:280px;flex:1"><div class="card-head" style="display:flex;align-items:center;gap:8px"><h3>内容变更</h3><span class="sub">${visibleChanges.changed.length} 个已变更</span><span style="flex:1"></span><button class="btn btn-sm btn-primary" onclick="batchRehash([${changeNames.map(n=>`'${esc(n)}'`).join(',')}],'内容变更')" style="font-size:10px;padding:2px 8px">全部重新记录</button></div>
      <div style="font-size:11px;color:var(--text-muted);padding-bottom:8px">SKILL.md 内容与安装时记录的哈希不同</div>`;
    visibleChanges.changed.forEach(c=>{
      h+=`<div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 50%,transparent)">
        <div style="flex:1"><div style="font-size:13px;font-weight:500">${c.name}</div>
          <div style="font-size:11px;color:var(--text-muted)">上次记录: ${c.last_recorded||'未知'}</div></div>
        <button class="btn btn-sm" onclick="rehashSkill('${esc(c.name)}',this)">重新记录</button></div>`;
    });
    h+=`</div></div>`;
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