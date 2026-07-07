// Issue view state
let _issueTypeTab='same-name'; // 'same-name' | 'upstream' | 'changes' | 'broken'

// layer → 安全边界色。boundary 决定治理卡头部配色（保护/复核·可清理/只观察/隐藏）。
// 详细解释文案随卡片降噪移除；判定链在 discovery.py + cleanup.py，前端只取 boundary 上色。
const LAYER_DOC={
  'active-root':{boundary:'保护'},
  'user-installed':{boundary:'保护'},
  'project-local':{boundary:'复核'},
  'imported-copy':{boundary:'复核·可清理'},
  'backup-snapshot':{boundary:'复核·可清理'},
  'downloaded-package':{boundary:'复核·可清理'},
  'package-cache':{boundary:'隐藏'},
  'plugin-cache':{boundary:'只观察'},
  'plugin-package':{boundary:'只观察'},
  'plugin-marketplace':{boundary:'只观察'},
  'vendor-bundled':{boundary:'只观察'},
  'fixture-example':{boundary:'隐藏·可清理'},
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
let _upShowAll=false; // 上游 tab「待比对」列表展开状态（独立，避免与同名 tab 的 _issueShowAll 串）

// Scan scope persisted across sessions —— 多选 toggle(Set)。
// scope 跟能力来源页视图映照:current=只扫当前目录 / active=扫「当前可用」
// / inventory=扫「来源库存」/ review=扫「待复核」。all=隐藏全量档,默认不选。
// 老 daily/deep/mine 单值在启动时迁移成 new Set(['active'])。
// localStorage 存 JSON.stringify([...set]);读出若为 JSON 数组直接 new Set(arr),
// 若是老字符串(单值)迁移后清掉再写数组。
const _SCAN_SCOPE_VALID=['current','active','inventory','review','all'];
function _migrateScanScope(v){
  if(v==='daily'||v==='deep'||v==='mine')return 'active';
  return _SCAN_SCOPE_VALID.includes(v)?v:null;
}
const _SCAN_SCOPE_KEY='sd-scan-scope-v2'; // v2:默认 active+review(≈原 daily 的 user+project);v1 默认只 active 太窄,上来啥都没有
let _scanScope=(()=>{
  try{
    const saved=localStorage.getItem(_SCAN_SCOPE_KEY);
    if(!saved)return new Set(['active','review']);
    try{
      const arr=JSON.parse(saved);
      if(Array.isArray(arr)){
        const migrated=arr.map(_migrateScanScope).filter(Boolean);
        return new Set(migrated.length?migrated:['active','review']);
      }
    }catch{}
    const m=_migrateScanScope(saved);
    if(m) return new Set([m]);
  }catch{}
  return new Set(['active','review']);
})();
// toggle:点一下选中,再点取消。约束:至少留一个(删到最后一个不删)。
function setScanScope(scope){
  if(!_SCAN_SCOPE_VALID.includes(scope))return;
  if(_scanScope.has(scope)){
    if(_scanScope.size<=1)return; // 至少留一个
    _scanScope.delete(scope);
  }else{
    _scanScope.add(scope);
  }
  try{localStorage.setItem(_SCAN_SCOPE_KEY,JSON.stringify([..._scanScope]))}catch{}
  renderScanConfig();
}

// Scan check types persisted across sessions
// 默认不含 upstream:upstream 烧 GitHub API(未认证 60 次/小时),用户主动勾才查。
let _scanChecks=(()=>{
  try{
    const saved=localStorage.getItem('sd-scan-checks');
    if(saved) return JSON.parse(saved);
  }catch{}
  return ['same-name','content-changes'];
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
  review:{emoji:'🔁',label:'待核查',desc:'项目级、跨 Agent 副本或未知运行态目录，先看内容再处理'},
  observe:{emoji:'📦',label:'生态目录',desc:'marketplace 或内置包，默认不做删除动作'},
  hidden:{emoji:'🚫',label:'缓存/内置',desc:'缓存、备份或测试样例，只在库存或全部视图里看'},
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
// 扫描范围匹配:scope 跟能力来源页视图映照。
// current=只扫当前目录; active/inventory/review/all 复用对应 sourceIs* 谓词。
function sourceMatchesScanScope(t,scope){
  if(scope==='current')return !!t?.is_current;
  if(scope==='all')return true;
  if(scope==='inventory')return sourceIsInventory(t);
  if(scope==='review')return sourceIsReview(t);
  return sourceIsActive(t); // default = active
}
// 老入口(被外部 cleanup-plan/deep 按钮等引用),保留为 active + current 的合并口径,
// 等价于旧行 user/project/is_current 在新分类下的落点。不再叫 "daily"。
function sourceIsScanActive(t){
  return !!t?.is_current||sourceIsActive(t);
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
  return `<span style="display:inline-flex;align-items:center;gap:4px;font-size:10px;color:var(--text-muted);white-space:nowrap" title="${esc(meta.desc||'')}"><span style="width:7px;height:7px;border-radius:50%;background:${meta.color};display:inline-block;flex-shrink:0"></span>${esc(meta.label||'')}</span>`;
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
    // 当前目录始终保留:切换进来的 target 即便不在当前视图桶(如 project-local
    // 不在 active 视图)也可见,避免"切了却在能力来源页消失"的反直觉。
    const dirs=g.dirs.filter(t=>predicate(t)||t?.is_current);
    return {...g,dirs,total_skills:dirs.reduce((s,d)=>s+(d.count||0),0)};
  }).filter(g=>g.dirs.length);
  return filtered;
}
function sortGroupsByCurrentAndSize(groups, direction='desc'){
  return [...groups].sort((a,b)=>{
    const aCur=a.dirs.some(t=>t.is_current);
    const bCur=b.dirs.some(t=>t.is_current);
    if(aCur!==bCur)return aCur?-1:1;
    return direction==='asc' ? a.total_skills-b.total_skills : b.total_skills-a.total_skills;
  });
}
function getVisibleSourceTargets(){
  return filterGroupsByView(targetGroups,_sourceViewMode).flatMap(g=>g.dirs);
}
function getVisibleSourceGroups(){
  return filterGroupsByView(targetGroups,_sourceViewMode);
}
function getDailyScanTargets(){
  // 兼容老入口(active+current 合并);新代码请用 getScanScopeTargets(scope)。
  return targetGroups.flatMap(g=>g.dirs.filter(sourceIsScanActive));
}
function getScanScopeTargets(scope){
  // 单 scope 入口(兼容外部调用);内部多选走 getSelectedScanScopeTargets。
  return targetGroups.flatMap(g=>g.dirs.filter(t=>sourceMatchesScanScope(t,scope)));
}
// 多选 toggle:对当前 _scanScope 里每个 scope 求 OR 合并去重。
function getSelectedScanScopeTargets(){
  const seen=new Set();
  const out=[];
  const scopes=[..._scanScope];
  if(!scopes.length)return [];
  // all 直接全量
  if(scopes.includes('all'))return targets.slice();
  targetGroups.forEach(g=>g.dirs.forEach(t=>{
    const norm=String(t.path||'').replace(/\/+$/,'');
    if(seen.has(norm))return;
    if(scopes.some(s=>sourceMatchesScanScope(t,s))){
      seen.add(norm);
      out.push(t);
    }
  }));
  return out;
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
    const scopeLabelMap={current:'当前目录',active:'当前可用',inventory:'来源库存',review:'待复核',all:'全部目录',daily:'当前可用',deep:'当前可用'};
    const scopeLabel=scopeLabelMap[scanResult.scope]||scanResult.scope||'当前可用';
    const tokenOk=scanResult.github_token_configured;
    const est=scanResult.upstream_api_estimate||0;
    const rl=scanResult.github_rate_limit||{};
    // 上游检测计费提示:仅当本次扫描勾了 upstream(estimate>0)才显示
    let apiHint='';
    if(est>0){
      const quota=tokenOk?'5000 次/小时':'60 次/小时';
      apiHint=`<span style="color:var(--amber);margin-left:8px" title="upstream 检测对每个 skill 调 GitHub API">上游检测: ${est} 个 skill ≈ ${est} 次 API（${quota}）</span>`;
      if(rl.limited){
        apiHint+=`<span style="color:var(--red);margin-left:8px" title="已触发 GitHub 限流">限流中，约 ${rl.reset_in_sec?Math.ceil(rl.reset_in_sec/60):0} 分钟后重置</span>`;
      }
    }
    statusHtml=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;font-size:11px;color:var(--text-muted)">
      <span>扫描：${scopeLabel} · ${scanResult.scanned_dirs} 目录 · ${(scanResult.duration_ms/1000).toFixed(1)}s</span>
      ${tokenOk?`<span style="color:var(--green);margin-left:8px" title="已配置 GITHUB_TOKEN，额度 5000 次/小时">🔐 Token 已配置</span>`:`<span style="color:var(--amber);margin-left:8px" title="未配置 GITHUB_TOKEN，GitHub API 未认证额度 60 次/小时">⚠ 未配置 Token</span>`}
      ${apiHint}
      ${scanResult.lint?.warnings?.length?`<span style="color:var(--red);margin-left:8px">${scanResult.lint.warnings.length} 个数据异常</span>`:''}
    </div>`;
  }
  const scopeBtn=(scope,label,title)=>{
    // toggle 多选:active 用 ✓ 前缀 + 实色背景,非 active 灰底。
    const active=_scanScope.has(scope);
    const check=active?'<span style="display:inline-block;width:10px;height:10px;border:1.5px solid currentColor;border-radius:2px;position:relative;vertical-align:-1px;margin-right:3px"><span style="position:absolute;left:1px;top:-2px;font-size:9px;line-height:1">✓</span></span>':'<span style="display:inline-block;width:10px;height:10px;border:1.5px solid var(--text-muted);border-radius:2px;vertical-align:-1px;margin-right:3px"></span>';
    return `<button class="btn btn-sm ${active?'btn-primary':''}" onclick="setScanScope('${scope}')" title="${esc(title)}" style="${active?'':'background:var(--bg-card-alt);color:var(--text-muted)'}">${check}${label}</button>`;
  };
  const checkBox=(key,label,title)=>{
    const checked=_scanChecks.includes(key);
    return `<label title="${esc(title)}" style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-muted);cursor:pointer;user-select:none">
      <input type="checkbox" ${checked?'checked':''} onchange="toggleScanCheck('${key}',this.checked)" style="cursor:pointer">
      <span>${label}</span>
    </label>`;
  };
  // 扫描范围跟能力来源页视图映照:能选 current/active/inventory/review 对应范围
  // + all(全量档)。多选 toggle。
  el.innerHTML=`<div class="card" style="border-left:3px solid var(--accent)">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px">
      <button class="btn btn-primary" id="cleanup-start-btn" onclick="startCleanupFlow()">开始整理</button>
      <span style="font-size:11px;color:var(--text-muted)">勾选检查项后点开始,自动扫描并生成处理建议。</span>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-left:auto;align-items:center">
        <div style="display:flex;gap:4px;align-items:center;padding-right:8px;border-right:1px solid var(--border-subtle)" title="扫描范围跟能力来源页视图映照">
          ${scopeBtn('current','当前目录','只扫当前 target 目录')}
          ${scopeBtn('active','当前可用','映照「能力来源 → 当前可用」:已启用插件/连接器/用户根/系统内置')}
          ${scopeBtn('inventory','来源库存','映照「能力来源 → 来源库存」:市场目录/缓存/已装未启用')}
          ${scopeBtn('review','待复核','映照「能力来源 → 待复核」:导入副本/项目级/未知')}
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          ${checkBox('same-name','同名','跨目录同名 skill')}
          ${checkBox('upstream','上游 (API)','检查是否有上游新版本（消耗 GitHub API，未认证 60 次/小时）')}
          ${checkBox('content-changes','变更','检测本地内容改动')}
        </div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px">
      扫描范围: ${getSelectedScanScopeTargets().length} 目录（多选 toggle，再点取消）
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
    const checks=[..._scanChecks];
    if(checks.length){
      await runScan(null,{silent:true,deferRender:true,checks});
    }
    setCleanupLoading(true,2);
    await runCleanupPlan(null,{deferRender:true});
    await runExecutionPlan('declutter',{silent:true});
    toast('整理完成：已生成可处理建议');
  }catch(e){
    if(cleanupPlan)renderIssues();
    toast('整理失败: '+e.message,'error');
  }finally{
    setCleanupLoading(false);
  }
}

async function runScan(scope,opts={}){
  try{
    // scope 参数忽略(向后兼容);实际用 _scanScope 多选合并 directories。
    // all 在 _scanScope 里 → 全量;否则合并所有选中 scope 的目录去重。
    const scanTargets=_scanScope.has('all')?targets:getSelectedScanScopeTargets();
    const directories=scanTargets.map(t=>t.path).filter(Boolean);
    if(!directories.length){
      // targets 没加载完 或 选中范围无目录——绝不调 scan-run,否则后端会 fallback 全量、烧 API 还污染缓存
      if(!opts.silent)toast('目录还在加载或选中范围没有目录,稍候再点','error');
      return;
    }
    const scopeTag=[..._scanScope].sort().join(',')||'active';
    const checks=(opts.checks&&opts.checks.length)?opts.checks:['same-name','content-changes'];
    const r=await fetch('/api/scan-run',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({directories,scope:scopeTag,checks})
    }).then(r=>r.json());
    if(r.error){toast(r.error,'error');return}
    scanResult=r;
    // Map scan result into health/globalOverlap for renderIssues
    health={
      upstream_sources:r.upstream_sources||[],
      source_status:r.source_status||[],
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

// 把 scan 选中的范围透传给 cleanup-plan/execution-plan 后端。
// 不再把所有非 all scope 压成 daily —— 那会让选"当前目录"时治理 tab 冒出别处目录。
// 改为:directories = scan 选中的目录(合并去重);cleanup scope 仍 daily/deep 二档
// (all→deep 全量审计;其余→daily 重点,过滤 observe/hide)。
function cleanupScopeAndDirectories(){
  // 治理 tab(可移垃圾站/待你看)看全量可清理目录,不受 scan scope 限制——
  // scan scope 只管分析 tab(同名/上游/变更)。否则选窄 scope 时治理 tab 全空。
  const cleanupScope=_scanScope.has('all')?'deep':'daily';
  return {cleanupScope, directories:[]};
}
async function runCleanupPlan(scope,opts={}){
  // scope 参数忽略(向后兼容);实际用 _scanScope 多选。
  const {cleanupScope,directories}=cleanupScopeAndDirectories();
  const btn=$('cleanup-plan-btn');
  const deepBtn=$('cleanup-plan-deep-btn');
  if(btn)btn.disabled=true;
  if(deepBtn)deepBtn.disabled=true;
  if(btn)btn.textContent=cleanupScope==='deep'?'⏳ 全量计划中...':'⏳ 生成计划中...';
  try{
    const dirQuery=directories.map(d=>`&dir=${encodeURIComponent(d)}`).join('');
    const r=await fetch(`/api/cleanup-plan?scope=${encodeURIComponent(cleanupScope)}${dirQuery}`).then(r=>r.json());
    if(r.error){toast(r.error,'error');return}
    cleanupPlan=r;
    // 记录本次 plan 限定的目录,runExecutionPlan 复用
    cleanupPlanDirectories=directories;
    executionPlan=null;
    cleanupExcludedActions.clear();
    if(!opts.deferRender)renderIssues();
    if(!opts.silent)toast(`${cleanupScope==='deep'?'全量目录审计':'重点治理计划'}完成: ${r.summary?.directories||0} 目录`);
  }catch(e){toast('清理计划失败: '+e.message,'error')}
  finally{
    if(btn){btn.disabled=false;btn.textContent='目录依据'}
    if(deepBtn)deepBtn.disabled=false;
  }
}
let cleanupPlanDirectories=[]; // runCleanupPlan 选中的目录,runExecutionPlan 复用

async function runExecutionPlan(strategy='conservative',opts={}){
  const cleanupScope=cleanupPlan?.scope||(_scanScope.has('all')?'deep':'daily');
  const directories=cleanupPlanDirectories.length?cleanupPlanDirectories:getSelectedScanScopeTargets().map(t=>t.path).filter(Boolean);
  const btn=$('execution-plan-btn');
  const declutterBtn=$('execution-plan-declutter-btn');
  if(btn)btn.disabled=true;
  if(declutterBtn)declutterBtn.disabled=true;
  if(btn)btn.textContent=strategy==='declutter'?'⏳ 生成断舍离预案...':'⏳ 生成执行预案...';
  try{
    const dirQuery=directories.map(d=>`&dir=${encodeURIComponent(d)}`).join('');
    const r=await fetch(`/api/cleanup-execution-plan?scope=${encodeURIComponent(cleanupScope)}&strategy=${encodeURIComponent(strategy)}${dirQuery}`).then(r=>r.json());
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

// 治理分桶：从 executionPlan.phases 按 operation 归成 3 组（供治理 tab 用）
// trash=可移垃圾站(勾选删除) / review=待你看(展示+多端部署标记) / frozen=不动(锁定/观察)
function computeGovernBuckets(){
  const buckets={trash:[],review:[],frozen:[]};
  if(!executionPlan)return buckets;
  const trashOps=['move_skills_to_trash','move_skill_to_trash'];
  (executionPlan.phases||[]).forEach(p=>(p.actions||[]).forEach(a=>{
    if(trashOps.includes(a.operation))buckets.trash.push(a);
    else if(a.operation==='manual_review'||a.operation==='mark_multi_agent_deploy')buckets.review.push(a);
    else buckets.frozen.push(a);
  }));
  return buckets;
}
// evidence 元素兼容 object({type,text}) 和老 string。
const _evText=(e)=>typeof e==='object'&&e?e.text:String(e||'');
// 单个治理目录卡 —— 按 operation 分流:move 类突出"为什么可删+可恢复",
// review 类突出"为什么不能自动删+你要看什么"。evidence 默认折叠。
function renderGovernActionCard(a){
  const executable=cleanupIsCandidateAction(a);
  const doc=LAYER_DOC[a.layer]||{};
  const boundary=doc.boundary||boundaryLabel(a);
  const bTone=boundaryTone(boundary);
  const layerText=a.layer_label||a.from_state||'未知层级';
  const isMove=/move_.*_trash/.test(a.operation);
  const isReview=a.operation==='manual_review';
  const isMultiDeploy=a.operation==='mark_multi_agent_deploy';
  const subj=a.skill_name||a.agent||'';
  // move 类主标突出 skill/目录名 + "可删"
  const title=isMove?(a.skill_name?`${a.skill_name} · 重复副本可删`:`${subj} · 整个目录可删`)
    :isReview?`${subj} · 需要你看一眼`
    :isMultiDeploy?`${a.skill_name||subj} · 多端部署副本`
    :subj;
  const evidenceHtml=(a.evidence&&a.evidence.length)?`<details style="margin-top:6px"><summary style="font-size:10px;color:var(--text-dim);cursor:pointer;list-style:none">▸ 为什么这么判断（${a.evidence.length} 条）</summary><div style="font-size:10px;color:var(--text-dim);line-height:1.6;padding-top:4px">${a.evidence.map(e=>`<div>· ${escapeHtml(_evText(e))}</div>`).join('')}</div></details>`:'';
  // move 类:主提示条(为什么可删 + 进垃圾站可恢复);review 类:主提示(为什么不能自动删 + 你要看什么)
  const promptBar=isMove
    ?`<div style="font-size:11px;color:var(--text);line-height:1.6;padding:6px 10px;margin:6px 0;background:var(--red)10;border-left:3px solid var(--red);border-radius:0 6px 6px 0">${escapeHtml(a.why||'')}<br><span style="color:var(--text-muted)">点上方勾选 → 右上「移入垃圾站」执行。只进可恢复垃圾站，不会永久删除。</span></div>`
    :isReview
    ?`<div style="font-size:11px;color:var(--text);line-height:1.6;padding:6px 10px;margin:6px 0;background:var(--amber)10;border-left:3px solid var(--amber);border-radius:0 6px 6px 0">${escapeHtml(a.why||'')}<br><span style="color:var(--text-muted)">建议：点 skill 名看内容，确认不用了再走「同名」或「损坏」tab 单独删。</span></div>`
    :`<div style="font-size:11px;color:var(--text-muted);line-height:1.5;margin-top:4px">${escapeHtml(a.why||'')}</div>`;
  return `<div style="border:1px solid var(--border-subtle);border-radius:10px;padding:0;background:var(--bg-card-alt);overflow:hidden">
    <div style="padding:8px 10px;background:${bTone}22;border-bottom:1px solid ${bTone}66;display:flex;gap:8px;align-items:center">
      <span style="font-size:13px;font-weight:700;color:${bTone}">${escapeHtml(layerText)}</span>
    </div>
    <div style="padding:8px 10px">
      ${executable?`<label style="display:flex;align-items:center;gap:8px;padding:8px 10px;margin-bottom:8px;border-radius:8px;cursor:pointer;background:${cleanupExcludedActions.has(a.id)?'var(--bg-card)':'var(--red)'}14;border:1px solid ${cleanupExcludedActions.has(a.id)?'var(--border-subtle)':'var(--red)'}55"><input type="checkbox" ${cleanupExcludedActions.has(a.id)?'':'checked'} onchange="toggleCleanupExclude('${esc(a.id)}')" style="width:16px;height:16px;cursor:pointer;accent-color:var(--red)"><span style="font-size:12px;font-weight:700;color:${cleanupExcludedActions.has(a.id)?'var(--text-muted)':'var(--red)'}">${cleanupExcludedActions.has(a.id)?'已排除（这个不会被处理）':`☑ 纳入清理 · 移入垃圾站 · ${a.count||0} skills`}</span></label>`:''}
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
        ${isMultiDeploy?`<button class="btn btn-sm" onclick="markMultiAgentDeployment('${esc(a.skill_name||'')}','${esc(a.content_hash||'')}','${esc(a.path||'')}','${esc(a.duplicate_of||'')}')" style="font-size:9px;padding:1px 6px">标记多端部署</button>`:''}
        ${isMultiDeploy?'<span class="skill-tag">不进垃圾站</span>':a.skill_name?'<span class="skill-tag">单个重复 skill</span>':'<span class="skill-tag">目录候选</span>'}
        <span style="flex:1"></span>
        <span style="font-size:11px;color:var(--text-muted)">${a.count||0} skills</span>
      </div>
      <div style="font-size:12px;font-weight:600;color:var(--text);margin-top:6px">${escapeHtml(title)}</div>
      ${promptBar}
      <div style="font-size:11px;color:var(--text-muted);line-height:1.5">回滚：${escapeHtml(a.rollback||'')}</div>
      ${renderIssuePath(a.path)}
      ${a.duplicate_of?`<div style="font-size:10px;color:var(--text-dim);margin-top:5px">保留副本</div>${renderIssuePath(a.duplicate_of)}`:''}
      ${evidenceHtml}
      ${a.sample_skills?.length?`<div class="skill-tags" style="margin-top:6px">${a.sample_skills.slice(0,5).map(n=>`<span class="skill-tag">${escapeHtml(n)}</span>`).join('')}</div>`:''}
    </div>
  </div>`;
}
// 处理建议顶部操作条（精简一行：移入垃圾站 + 本地决策 + 收起）。常驻 tab 上方。
function renderExecHeader(){
  if(!executionPlan)return '';
  const candidateActions=cleanupCandidateActions();
  const executableActions=candidateActions.filter(a=>!cleanupExcludedActions.has(a.id));
  const executableSkillCount=executableActions.reduce((s,a)=>s+(a.count||0),0);
  const excludedCount=candidateActions.length-executableActions.length;
  return `<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:8px 12px;margin-bottom:8px;background:var(--bg-card-alt);border-left:3px solid var(--red);border-radius:0 8px 8px 0">
    <span style="font-size:12px;font-weight:700">🧹 处理建议</span>
    <span style="font-size:11px;color:var(--text-muted)">点「🗑️ 可移垃圾站」勾选候选 → 点右侧按钮批量移入；只进可恢复垃圾站 · 预案 ${fmtScanTime(executionPlan.generated_at)}</span>
    <span style="flex:1"></span>
    ${excludedCount?`<button class="btn btn-sm" onclick="restoreAllCleanupCandidates()" title="恢复被排除的候选">恢复${excludedCount}项</button>`:''}
    <button class="btn btn-sm" onclick="showDuplicateDecisions()" title="本机运行状态，不随 Git 提交">本地决策</button>
    <button class="btn btn-sm" onclick="executionPlan=null;_issueTypeTab='same-name';renderIssues()">收起</button>
    <button class="btn btn-sm btn-danger" id="cleanup-execute-btn" onclick="executeRecommendedCleanupActions()" ${executableActions.length?'':'disabled'}>移入垃圾站 ${executableActions.length} 项 / ${executableSkillCount} skills</button>
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
  // scope 参数忽略(runScan/runCleanupPlan 内部用 _scanScope 多选自治)。
  const strategy=opts.strategy||executionPlan?.strategy||'declutter';
  const hadCleanupPlan=!!cleanupPlan||!!executionPlan;
  const hadExecutionPlan=!!executionPlan;
  const tabBefore=_issueTypeTab;
  const showAllBefore=_issueShowAll;
  await loadTrash();
  await refreshAfterDelete(changedPaths||[]);
  if(hadCleanupPlan){
    await runCleanupPlan(null,{silent:true,deferRender:true});
  }
  await runScan(null,{silent:true,deferRender:true,preserveIssueView:true});
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
    await refreshIssuesAfterDelete(d.changed_paths||[],{strategy:strategyBefore});
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
  const execHeaderHtml=renderExecHeader();
  const planHtml=executionPlan?'':renderCleanupPlan();

  // No scan yet
  if(!scanResult&&(!health||(!upstreams.length&&!issues.length))){
    $('issues-list').innerHTML=execHeaderHtml+planHtml+'<div class="empty" style="padding:30px 0">点击「开始整理」扫描目录并生成处理建议。</div>';
    return;
  }

  // ── 内容类型计数：按问题类型分，不再按运行态 view 过滤 ──
  const sameNameGroups=sameName.filter(dup=>dup.locations.length>=2);
  const upstreamAll=upstreams.filter(s=>s.repo);
  const changedSkills=changes?.changed||[];
  const brokenLinks=issues.filter(i=>i.kind==='broken_symlink'||i.kind==='broken_skill_link');

  // 待补来源:三信号(steal-meta / .git remote / .skill-lock)全空的 skill。
  // 数据来自扫描的 source_status(后端 detect_source_local,0 GitHub API,默认扫描即产出)。
  // 点「补来源」直接打开 skill 详情的补来源面板,按 SKILL.md 内容搜回上游。
  const recoverDirs=(health?.source_status||[]).filter(s=>s.source==='unknown');

  const issueTabs=[
    {key:'same-name',emoji:'📛',label:'同名',count:sameNameGroups.length},
    {key:'upstream',emoji:'🔗',label:'上游',count:upstreamAll.length},
    {key:'changes',emoji:'🔄',label:'变更',count:changedSkills.length},
    {key:'broken',emoji:'🔴',label:'损坏',count:brokenLinks.length},
    {key:'recover',emoji:'◆',label:'待补来源',title:'没有任何上游来源留痕的 skill(steal-meta/.git/lock 三信号全空),点补来源按内容搜回上游',count:recoverDirs.length},
  ];
  const govBuckets=computeGovernBuckets();
  // frozen tab(不动:锁定/观察/缓存)只在 all scope 显示 —— 非 all 时
  // 这些目录本来就不该出现在治理结果里,展示出来纯困惑。
  const showFrozen=_scanScope.has('all');
  const governTabs=executionPlan?[
    {key:'trash',emoji:'🗑️',label:'可移垃圾站',title:'hash 一致的重复 skill / 候选目录副本',count:govBuckets.trash.length},
    {key:'review',emoji:'🔎',label:'待你看',title:'导入副本 / 项目级 / 未知来源,需你判断',count:govBuckets.review.length},
    ...(showFrozen?[{key:'frozen',emoji:'🔒',label:'不动',title:'保护区 / 市场货架 / 缓存,只读不动',count:govBuckets.frozen.length}]:[]),
  ]:[];
  const allTabs=[...issueTabs,...governTabs];
  // tab 不存在才回退;count=0 也允许切(下方有空状态提示)。
  // 不能弹回——弹回会让用户点了同名/变更却高亮跳走,以为"点不动"。
  const curDef=allTabs.find(t=>t.key===_issueTypeTab);
  if(!curDef){
    _issueTypeTab='same-name';
  }

  const tabBtn=(t)=>`<button class="issue-tab ${_issueTypeTab===t.key?'active':''}" onclick="_issueTypeTab='${t.key}';_issueShowAll=false;renderIssues()" title="${esc(t.title||'')}"><span>${t.emoji}</span><span>${t.label}</span>${t.count?`<b>${t.count}</b>`:''}</button>`;
  let tabHtml='<div class="issue-tabs">'+issueTabs.map(tabBtn).join('');
  if(governTabs.length){
    tabHtml+=`<span aria-hidden="true" style="display:inline-flex;align-self:center;width:1px;height:16px;background:var(--border);margin:0 4px"></span>${governTabs.map(tabBtn).join('')}`;
  }
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
  let h=freshnessHtml+execHeaderHtml+tabHtml;

  const curTab=allTabs.find(t=>t.key===_issueTypeTab);
  const totalForTab=curTab?.count||0;

  // ── 治理 tab：渲染对应桶的目录卡片（多列并排）──
  if(curTab&&governTabs.some(t=>t.key===curTab.key)){
    const actions=govBuckets[curTab.key]||[];
    if(!actions.length){
      const reviewCnt=(govBuckets.review||[]).length;
      const isTrash=curTab.key==='trash';
      h+=`<div class="empty" style="padding:26px 0;line-height:1.7">${isTrash&&reviewCnt
        ?`✅ 当前范围没有可自动移垃圾站的候选（保守策略：只推荐备份/导入/下载层里 SKILL.md 完全一致的重复，且可恢复）。<br><span style="color:var(--text-muted)">有 ${reviewCnt} 个目录在「🔎 待你看」等你判断要不要清理 → </span><button class="btn btn-sm btn-primary" onclick="_issueTypeTab='review';_issueShowAll=false;renderIssues()">去看待你看</button>`
        :`✅ 这组没有目录`}</div>`;
    }else{
      const GLIMIT=12;
      const shown=_issueShowAll?actions:actions.slice(0,GLIMIT);
      h+=`<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:10px;align-items:start;margin-top:8px">${shown.map(renderGovernActionCard).join('')}</div>`;
      if(actions.length>GLIMIT){
        h+=_issueShowAll
          ?`<div class="notice-line"><span>显示全部 ${actions.length} 个</span><button class="btn btn-sm" onclick="_issueShowAll=false;renderIssues()">只看前 ${GLIMIT}</button></div>`
          :`<div class="notice-line"><span>显示前 ${GLIMIT} / 共 ${actions.length} 个</span><button class="btn btn-sm btn-primary" onclick="_issueShowAll=true;renderIssues()">显示全部 ${actions.length}</button></div>`;
      }
    }
    $('issues-list').innerHTML=h;
    return;
  }

  // ── 分析 tab：原有 section 逻辑 ──
  if(totalForTab===0){
    h+=`<div class="empty" style="padding:30px 0">✅ 没有发现问题</div>`;
    h+=planHtml;
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
    const headTag=[outdated.length&&`${outdated.length} 个过时`,pendingCompare.length&&`${pendingCompare.length} 个已最新/待比对`].filter(Boolean).join(' · ')||'无过时';
    h+=`<section class="issue-section"><div class="issue-section-head"><div><h3>🔗 上游追踪</h3><p>只提示可复核更新，不自动改文件。未配置 token 时仅展示检测到的来源。</p></div><span>${headTag}</span></div>`;
    h+=`<div class="card issue-list-card">`;
    if(outdated.length){
      const SOURCE_LABEL={
        'steal-meta':['Steal安装','通过 Skill Dashboard 从 GitHub 安装'],
        'git-remote':['Git仓库','目录本身是一个 Git 仓库，可 git pull'],
        'vercel-lock':['NPX/Vercel','通过 npx skills add 安装，记录在 ~/.agents/.skill-lock.json'],
        'unknown':['未知','无法识别上游来源']
      };
      // 先按 canonical_dir 去重(合并 symlink 副本,避免 N 个相同更新按钮)
      const upstreamGroups={};
      outdated.forEach(s=>{
        const key=s.canonical_dir||s.dir;
        if(!upstreamGroups[key]){
          upstreamGroups[key]={...s, copies:[]};
        }
        upstreamGroups[key].copies.push({dir:s.dir,is_symlink:s.is_symlink,link_target:s.link_target});
      });
      // 再按应用(agent)分组,套折叠卡——用户要"按应用展开看哪些 skill 要更新"。
      const upByAgent={};
      Object.values(upstreamGroups).forEach(s=>{
        const agent=_dirCategory(s.dir);
        (upByAgent[agent]=upByAgent[agent]||[]).push(s);
      });
      h+=`<div class="issue-card-grid">`;
      Object.entries(upByAgent).forEach(([agent,skills])=>{
        const cm=CAT_META[agent]||CAT_META.unknown;
        h+=`<div style="border:1px solid var(--border-subtle);border-radius:8px;overflow:hidden">
          <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-card-alt);cursor:pointer" onclick="var b=this.parentElement.querySelector('.up-body');var on=b.style.display==='none';b.style.display=on?'block':'none';this.querySelector('.up-arrow').textContent=on?'▼':'▶'">
            <span class="up-arrow" style="font-size:10px;color:var(--text-muted)">▶</span>
            <span style="font-size:13px;font-weight:600">${cm?.name||agent}</span>
            <span style="font-size:11px;color:var(--red);background:var(--bg-card);padding:1px 6px;border-radius:999px">${skills.length} 个过时</span>
          </div>
          <div class="up-body" style="display:none;padding:6px 12px 10px">
            ${skills.map(s=>{
              const [sourceLabel,sourceTitle]=SOURCE_LABEL[s.source||'unknown']||SOURCE_LABEL['unknown'];
              const canonical=s.canonical_dir||s.dir;
              const updateLabel=s.source==='vercel-lock'?'NPX 更新':s.source==='git-remote'?'Git 更新':'更新';
              const copyCount=s.copies.length;
              const copyHint=copyCount>1?`&#10;共 ${copyCount} 个副本: ${s.copies.map(c=>c.dir.replace(/^\/Users\/[^/]+/,'~')).join(', ')}`:'';
              return `<div class="issue-row">
                ${issueDirBadge(canonical)}
                <div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px"><span style="font-size:13px;font-weight:500">${s.name}</span>${copyCount>1?`<span style="font-size:10px;color:var(--text-muted);background:var(--bg-card-alt);padding:1px 5px;border-radius:999px" title="${esc(copyHint)}">+${copyCount-1} 副本</span>`:''}</div><div style="font-size:11px;color:var(--text-muted)">${s.repo}</div><div style="font-size:10px;color:var(--text-muted);font-family:monospace" title="当前版本 → 上游最新版本">${s.installed_commit?.slice(0,8)||'?'} → ${s.latest_commit?.slice(0,8)||'?'}</div>${renderIssuePath(canonical)}</div>
                <span style="font-size:11px;color:var(--red)">⚠ 过时</span>
                <span style="font-size:10px;color:var(--text-muted);white-space:nowrap" title="${esc(sourceTitle)}${copyHint}">${sourceLabel}</span>
                <button class="btn btn-sm" onclick="updateUpstream('${esc(s.name)}',{target:this},'${esc(canonical)}')">${updateLabel}</button>
              </div>`;
            }).join('')}
          </div>
        </div>`;
      });
      h+=`</div>`;
    }
    if(pendingCompare.length){
      const upLim=_upShowAll?pendingCompare.length:Math.min(LIMIT,pendingCompare.length);
      h+=`<div style="border:1px solid var(--border-subtle);border-radius:8px;overflow:hidden;margin-top:10px">
        <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-card-alt);cursor:pointer" onclick="var b=this.parentElement.querySelector('.up-pend-body');var on=b.style.display==='none';b.style.display=on?'block':'none';this.querySelector('.up-pend-arrow').textContent=on?'▼':'▶'">
          <span class="up-pend-arrow" style="font-size:10px;color:var(--text-muted)">▶</span>
          <span style="font-size:13px;font-weight:600">已最新 / 待比对</span>
          <span style="font-size:11px;color:var(--text-muted);background:var(--bg-card);padding:1px 6px;border-radius:999px">${pendingCompare.length} 个</span>
        </div>
        <div class="up-pend-body" style="display:none;padding:6px 12px 10px">`;
      pendingCompare.slice(0,upLim).forEach(s=>{
        const canonical=s.canonical_dir||s.dir;
        const isCurrent=s.status==='current';
        h+=`<div class="issue-row">${issueDirBadge(canonical)}<div style="flex:1;min-width:0"><div style="display:flex;align-items:center;gap:6px"><span style="font-size:13px;font-weight:500">${s.name}</span></div><div style="font-size:11px;color:var(--text-muted)">${s.repo}</div>${renderIssuePath(canonical)}</div><span style="font-size:11px;color:${isCurrent?'var(--green)':'var(--text-muted)'}">${isCurrent?'✓ 已最新':'待比对'}</span></div>`;
      });
      if(pendingCompare.length>LIMIT){
        h+=_upShowAll
          ?`<div class="notice-line"><span>显示全部 ${pendingCompare.length} 个</span><button class="btn btn-sm" onclick="_upShowAll=false;renderIssues()">只看前 ${LIMIT}</button></div>`
          :`<div class="notice-line"><span>显示前 ${LIMIT} / 共 ${pendingCompare.length} 个</span><button class="btn btn-sm btn-primary" onclick="_upShowAll=true;renderIssues()">显示全部 ${pendingCompare.length}</button></div>`;
      }
      h+=`</div></div>`;
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
            // unknown skill 判断:dir 推不出活跃能力桶 → 高亮"补来源"按钮
            const _t=_dirTarget(loc.dir);
            const _bucket=_t?sourceCapabilityBucket(_t):'unknown';
            const needSrc=_bucket==='unknown'||_bucket==='review-copy';
            return `<div style="display:grid;grid-template-columns:auto auto minmax(0,1fr) auto auto auto;gap:6px;padding:5px 0;border-bottom:1px solid var(--border-subtle);align-items:center">
              ${issueDirBadge(loc.dir)}
              <input type="checkbox" class="issue-check" data-skey="${esc(sKey)}" data-sname="${esc(sn)}" data-sdir="${esc(loc.dir)}" ${_issueSelected.has(sKey)?'checked':''} onchange="toggleIssueSelect(this)" style="cursor:pointer">
              <div style="min-width:0">
                <span style="font-size:12px;font-weight:500;color:var(--indigo);cursor:pointer" onclick="showSkill('${esc(sn)}','${esc(loc.dir)}')">${sn}</span>
                ${renderIssuePath(loc.dir)}
              </div>
              <button class="btn btn-sm" onclick="showSkill('${esc(sn)}','${esc(loc.dir)}',{autoExpandRecovery:true})" title="按内容搜回上游来源" style="font-size:9px;padding:2px 6px;${needSrc?'color:var(--amber);border-color:var(--amber)':''}">补来源</button>
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

  // ── Recover(待补来源)section:按目录分组,卡内 skill 懒展开 ──
  if(_issueTypeTab==='recover'&&recoverDirs.length){
    // 按 dir 分组,目录按 unknown skill 数降序。卡内 skill **懒展开**——初始只渲染
    // 卡头(目录+count),点开才渲染 skill 行。否则 active scope 几百 skill 全量进
    // display:none body(每卡),12 卡 = 几千行 DOM 每次重渲染,浏览器卡死点不动。
    const recoverGroups={};
    recoverDirs.forEach(s=>{(recoverGroups[s.dir]=recoverGroups[s.dir]||[]).push(s);});
    const recoverGroupList=Object.entries(recoverGroups)
      .map(([dir,skills])=>({dir,skills}))
      .sort((a,b)=>b.skills.length-a.skills.length);
    h+=`<section class="issue-section"><div class="issue-section-head"><div><h3 style="color:var(--amber)">◆ 待补来源</h3><p>这些 skill 没有任何上游来源留痕（steal-meta / .git / lock 三信号全空）。按目录分组,展开点「补来源」按 SKILL.md 内容搜回上游仓库。</p></div><span>${recoverDirs.length} skill · ${recoverGroupList.length} 目录</span></div>`;
    h+=`<div class="issue-card-grid">`;
    const gLim=_issueShowAll?recoverGroupList.length:Math.min(LIMIT,recoverGroupList.length);
    recoverGroupList.slice(0,gLim).forEach((g,i)=>{
      const gid='rc'+i+'-'+Math.random().toString(36).slice(2,6);
      _rcGroupData[gid]=g.skills; // 存数据,展开时取(不初始渲染 DOM)
      const shortDir=(g.dir||'').replace(/^\/Users\/[^/]+/,'~');
      h+=`<div style="border:1px solid var(--border-subtle);border-radius:8px;overflow:hidden">
        <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-card-alt);cursor:pointer" onclick="toggleRecoverGroup('${gid}',this)">
          <span class="rc-arrow" style="font-size:10px;color:var(--text-muted)">▶</span>
          ${issueDirBadge(g.dir)}
          <span style="flex:1;min-width:0;font-size:12px;font-weight:600;font-family:var(--mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(g.dir)}">${esc(shortDir)}</span>
          <span style="font-size:11px;color:var(--text-muted);flex-shrink:0">${g.skills.length} skill</span>
        </div>
        <div id="rcbody-${gid}" class="issue-group-body" style="display:none;padding:6px 12px 10px"></div>
      </div>`;
    });
    h+=`</div></section>`;
    if(recoverGroupList.length>LIMIT){
      h+=_issueShowAll
        ?`<div class="notice-line"><span>显示全部 ${recoverGroupList.length} 个目录</span><button class="btn btn-sm" onclick="_issueShowAll=false;renderIssues()">只看前 ${LIMIT}</button></div>`
        :`<div class="notice-line"><span>显示前 ${LIMIT} / 共 ${recoverGroupList.length} 个目录(${recoverDirs.length} skill)</span><button class="btn btn-sm btn-primary" onclick="_issueShowAll=true;renderIssues()">显示全部</button></div>`;
    }
  }

  h+=planHtml;
  $('issues-list').innerHTML=h;
}

// recover 目录卡懒展开:点卡头才渲染该目录的 skill 行,避免 active scope 几百 skill
// 全量进 display:none body 卡死浏览器。_rcGroupData 存每卡 skill 数据(renderIssues 填)。
const _rcGroupData={};
function toggleRecoverGroup(gid,headerEl){
  const body=document.getElementById('rcbody-'+gid);
  if(!body)return;
  const arrow=headerEl.querySelector('.rc-arrow');
  const on=body.style.display==='none';
  body.style.display=on?'block':'none';
  if(arrow)arrow.textContent=on?'▼':'▶';
  // 懒展开:首次展开才渲染 skill 行,避免初始全量 DOM 卡死
  if(on&&!body.dataset.filled){
    body.dataset.filled='1';
    const skills=_rcGroupData[gid]||[];
    body.innerHTML=skills.map(s=>`<div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--border-subtle)">
      <span style="flex:1;min-width:0;font-size:12px;font-weight:500;color:var(--indigo);cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s.name)}" onclick="showSkill('${esc(s.name)}','${esc(s.dir)}')">${escapeHtml(s.name)}</span>
      <button class="btn btn-sm" onclick="showSkill('${esc(s.name)}','${esc(s.dir)}',{autoExpandRecovery:true})" title="按 SKILL.md 内容搜回上游来源" style="font-size:9px;padding:2px 6px;color:var(--amber);border-color:var(--amber)">补来源</button>
    </div>`).join('');
  }
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
    const cnt=t.skill_count||1;
    const countLabel=t.kind==='symlink'?'链接入口':`${cnt} skill${cnt>1?'s':''}`;
    if(t.kind==='package'){
      const skills=(t.skills||[]).map(s=>`<div style="font-size:11px;padding:4px 0;border-bottom:1px solid var(--border-subtle)"><strong style="color:var(--text)">${escapeHtml(s.name||'')}</strong><div style="color:var(--text-muted);font-family:var(--mono);font-size:10px;word-break:break-all;margin-top:2px">${escapeHtml(s.original_path||'')}</div></div>`).join('');
      h+=`<div class="card" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:10px">
          <span onclick="togglePkgCard(this)" style="cursor:pointer;display:inline-block;transition:transform .15s;font-size:10px;color:var(--text-muted);width:12px;text-align:center">▶</span>
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:13px">${escapeHtml(t.name)}</div>
            <div style="font-size:11px;color:var(--text-muted)">${countLabel} · ${dateStr}</div>
          </div>
          <button class="btn btn-sm btn-primary" onclick="event.stopPropagation();restoreTrash('${t.id}')" style="font-size:10px">恢复包</button>
          <button class="btn btn-sm btn-danger" onclick="event.stopPropagation();permanentDeleteTrash('${t.id}','${esc(t.name)}')" style="font-size:10px">删除</button>
        </div>
        <div class="pkg-body" style="display:none;margin-top:10px;padding:6px 0 6px 16px;border-left:2px solid var(--accent-bg)">${skills||'<div style="font-size:11px;color:var(--text-muted)">空</div>'}</div>
      </div>`;
    }else{
      h+=`<div class="card" style="margin-bottom:8px">
        <div style="display:flex;align-items:center;gap:10px">
          <div style="flex:1;min-width:0">
            <div style="font-weight:600;font-size:13px">${escapeHtml(t.name)}</div>
            <div style="font-size:11px;color:var(--text-muted)">原路径: ${escapeHtml(t.original_path||'未知')} · ${countLabel} · ${dateStr}</div>
          </div>
          <button class="btn btn-sm btn-primary" onclick="restoreTrash('${t.id}')" style="font-size:10px">恢复</button>
          <button class="btn btn-sm btn-danger" onclick="permanentDeleteTrash('${t.id}','${esc(t.name)}')" style="font-size:10px">永久删除</button>
        </div>
      </div>`;
    }
  });
  $('trash-list').innerHTML=h;
}
function togglePkgCard(arrow){
  const card=arrow.closest('.card');
  const body=card&&card.querySelector('.pkg-body');
  if(!body)return;
  const open=body.style.display!=='none';
  body.style.display=open?'none':'block';
  arrow.style.transform=open?'rotate(0deg)':'rotate(90deg)';
}
async function restoreTrash(id){
  try{
    const r=await fetch(`/api/trash/${encodeURIComponent(id)}/restore`,{method:'POST',headers:{'Content-Type':'application/json'}});
    const d=await r.json();
    if(d.ok){
      if(d.kind==='package'){
        const okN=d.restored_to?d.restored_to.length:0;
        const failN=d.failed?d.failed.length:0;
        toast(`已恢复 ${okN} 个 skill${failN?`，${failN} 个失败`:''}`);
      }else{
        toast(`已恢复: ${d.restored_to}`);
      }
      await loadTrash();invalidateTargetsCache();clearGlobalSearchCache();await loadData()
    }else{toast(d.error||'恢复失败','error')}
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
