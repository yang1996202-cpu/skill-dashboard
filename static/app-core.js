let scan=null,health=null,skills=[],diagState='idle',installedPlugins=[],enabledPlugins=[],knownMarketplaces=[],mcpInventory=null;
const $=id=>document.getElementById(id);
let diagPollTimer=null;
let categoryOverrides={};
function loadCategoryOverrides(){
  try{categoryOverrides=JSON.parse(localStorage.getItem('sd-category-overrides')||'{}')}catch(e){categoryOverrides={}}
}
function saveCategoryOverrides(){localStorage.setItem('sd-category-overrides',JSON.stringify(categoryOverrides))}
function setCategory(name,cat){
  if(!cat||cat==='other'){delete categoryOverrides[name]}else{categoryOverrides[name]=cat}
  saveCategoryOverrides();
  skills.forEach(s=>{if(s.name===name){s.category=cat||classifySkillJS(name);s.categorySource=cat?'user':'keyword'}});
  render();toast(`已设置 "${name}" 分类为 ${cat||'其他'}`);
}
let selectedSkills=new Set();

/* ── Theme ── */
const ICON_SUN='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4.2"/><path d="M12 2v2.5M12 19.5V22M4.2 4.2l1.8 1.8M18 18l1.8 1.8M2 12h2.5M19.5 12H22M4.2 19.8l1.8-1.8M18 6l1.8-1.8"/></svg>';
const ICON_MOON='<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>';
function getTheme(){return localStorage.getItem('sd-theme')||'light'}
function applyTheme(t){
  document.documentElement.setAttribute('data-theme',t);
  localStorage.setItem('sd-theme',t);
  $('theme-icon').innerHTML=t==='dark'?ICON_MOON:ICON_SUN;
}
function toggleTheme(){applyTheme(getTheme()==='light'?'dark':'light')}
applyTheme(getTheme());

/* ── Toast ── */
let toastTimer;
function toast(msg,type='success'){
  clearTimeout(toastTimer);
  const el=$('toast');el.textContent=msg;el.className='toast show '+type;
  toastTimer=setTimeout(()=>el.classList.remove('show'),3000);
}

/* ── View switching ── */
function switchView(v,el){
  document.querySelectorAll('.view').forEach(e=>e.classList.remove('active'));
  $('view-'+v).classList.add('active');
  document.querySelectorAll('.nav-item').forEach(e=>e.classList.remove('active'));
  if(el)el.classList.add('active');
  const titles={dashboard:'仪表盘',skills:'当前目录技能',issues:'问题与整理',sources:'能力来源',trash:'垃圾站',history:'操作日志'};
  $('view-title').textContent=titles[v]||v;
  $('sidebar').classList.remove('open');
  if(v==='sources'){
    renderSources();
    updateTargetSelector(false,'full');
  }
}
function goView(v){
  switchView(v,document.querySelector(`.nav-item[onclick*="${v}"]`));
}

/* ── Data ── */
let globalStats=null;
let globalOverlap=null;
let _targetsCache=null;
let _targetsCacheTs=0;
function invalidateTargetsCache(){_targetsCache=null;_targetsCacheTs=0;}
const TARGETS_CACHE_TTL=3*60*1000; // 3 minutes, matching backend TTL
let scanResult=null; // cached scan results from /api/scan-run
let cleanupPlan=null; // dry-run governance plan from /api/cleanup-plan
let executionPlan=null; // executable-shaped preview; does not run actions
let cleanupExcludedActions=new Set();
let _compareData={};
let _issueSelected=new Set(); // tracks "name|dir" keys for issue cards
let _issueShowAll=false;
let _sourcesShowAll=false;
let _globalSearchQuery='';
let _globalSearchResults=null;
let _globalSearchTimer=null;
let _globalSearchCache={};
const GLOBAL_SEARCH_CACHE_TTL=2*60*1000; // 2 minutes
function clearGlobalSearchCache(){_globalSearchCache={};}
let _sourceViewMode=(()=>{
  const raw=localStorage.getItem('sd-source-view');
  if(['active','inventory','review','all'].includes(raw)) return raw;
  if(raw==='mine') return 'active';
  if(raw==='source-market') return 'inventory';
  // migrate legacy values
  if(raw==='deep') return 'all';
  return 'active';
})(); // active | inventory | review | all
let _expandedSourceAgent=null;

const CAPABILITY_META={
  'active-user':{color:'var(--accent)',label:'用户自建',desc:'用户全局技能根，通常会进入该 Agent 的基础能力面。'},
  'active-system':{color:'var(--indigo)',label:'系统内置',desc:'宿主内置技能，不是用户项目目录。'},
  'active-plugin':{color:'var(--green)',label:'已启用插件',desc:'宿主配置明确启用的插件包。'},
  'active-connector':{color:'var(--cyan)',label:'连接器包',desc:'由 app/connector 运行时按需暴露的能力包。'},
  'installed-disabled':{color:'var(--text-muted)',label:'已安装未启用',desc:'安装记录存在，但当前宿主未启用。'},
  'source-cache':{color:'var(--text-muted)',label:'仅缓存',desc:'本地缓存、旧包或备份，不等于当前上下文能力。'},
  'source-catalog':{color:'var(--amber)',label:'市场目录',desc:'marketplace/catalog 货架目录，只作为来源材料。'},
  'review-copy':{color:'var(--purple)',label:'导入/副本',desc:'跨 Agent 复制或导入目录，需要复核。'},
  'project-local':{color:'#4E7A8C',label:'项目级',desc:'项目内技能目录，是否加载取决于宿主和当前项目。'},
  commands:{color:'#B89B3A',label:'命令',desc:'命令目录，和 skills 分开统计。'},
  unknown:{color:'var(--text-muted)',label:'未知',desc:'只确认文件存在，未识别运行态。'},
};

const SKILL_ROLE_META={
  router:{label:'路由',short:'router'},
  workflow:{label:'工作流',short:'workflow'},
  guide:{label:'指南',short:'guide'},
  helper:{label:'辅助',short:'helper'},
  automation:{label:'自动化',short:'automation'},
  unknown:{label:'未分类',short:'unknown'},
};

function mergeSkillRoleCounts(into,counts){
  Object.entries(counts||{}).forEach(([k,v])=>{into[k]=(into[k]||0)+(v||0)});
}

function skillRoleSummaryText(counts,opts={}){
  counts=counts||{};
  const parts=[];
  if(counts.router)parts.push(`路由 ${counts.router}`);
  if(counts.workflow)parts.push(`工作流 ${counts.workflow}`);
  if(counts.automation)parts.push(`自动化 ${counts.automation}`);
  if(counts.guide)parts.push(`指南 ${counts.guide}`);
  if(counts.helper)parts.push(`辅助 ${counts.helper}`);
  if(opts.includeUnknown&&counts.unknown)parts.push(`未分类 ${counts.unknown}`);
  return parts.join(' · ');
}

function sourceCapabilityBucket(t){
  if(!t)return 'unknown';
  if(t.type==='commands'){
    // 只留跟当前 target 同根的 commands(全局主)进 active,其他项目级归 review
    const cur=targets.find(x=>x.is_current);
    if(cur){
      const cmdRoot=(t.path||'').replace(/\/commands$/,'');
      const curRoot=(cur.path||'').replace(/\/(skills|commands)$/,'');
      if(cmdRoot===curRoot) return 'commands';
    }
    return 'project-local';
  }
  const state=t.runtime_state||'';
  if(state==='user-root')return 'active-user';
  if(state==='builtin')return 'active-system';
  if(state==='enabled'||state==='loaded')return 'active-plugin';
  if(state==='connector')return 'active-connector';
  if(state==='installed')return 'installed-disabled';
  if(['cache','orphaned','stale'].includes(state))return 'source-cache';
  if(state==='catalog')return 'source-catalog';
  if(t.category==='user')return 'active-user';
  if(t.layer==='vendor-bundled')return 'active-system';
  if(t.category==='cache')return 'source-cache';
  if(t.category==='marketplace')return 'source-catalog';
  if(t.category==='cross-copy')return 'review-copy';
  if(t.category==='project')return 'project-local';
  return 'unknown';
}

function capabilityMeta(key){return CAPABILITY_META[key]||CAPABILITY_META.unknown}

function summarizeCapabilityDirs(dirs){
  const s={
    dirs:(dirs||[]).length,
    inventorySkills:0,
    commandCount:0,
    activeSkills:0,
    activeDirs:0,
    userSkills:0,
    systemSkills:0,
    pluginSkills:0,
    pluginDirs:0,
    connectorSkills:0,
    connectorDirs:0,
    duplicateRuntimeSkills:0,
    duplicateRuntimeDirs:0,
    installedDirs:0,
    cacheSkills:0,
    cacheDirs:0,
    catalogSkills:0,
    catalogDirs:0,
    reviewSkills:0,
    reviewDirs:0,
    roleCounts:{},
    topLevelSkillCount:0,
    supportSkillCount:0,
  };
  (dirs||[]).forEach(t=>{
    const count=t.count||0;
    const bucket=sourceCapabilityBucket(t);
    if(bucket==='commands'){s.commandCount+=count;return}
    mergeSkillRoleCounts(s.roleCounts,t.skill_role_counts);
    s.topLevelSkillCount+=(t.top_level_skill_count||0);
    s.supportSkillCount+=(t.support_skill_count||0);
    const duplicateRuntime=!!t.loaded_elsewhere&&(bucket==='active-connector'||bucket==='active-plugin');
    s.inventorySkills+=count;
    if(duplicateRuntime){
      s.duplicateRuntimeSkills+=count;s.duplicateRuntimeDirs+=1;
    }else if(['active-user','active-system','active-plugin','active-connector'].includes(bucket)){
      s.activeSkills+=count;s.activeDirs+=1;
    }
    if(bucket==='active-user')s.userSkills+=count;
    else if(bucket==='active-system')s.systemSkills+=count;
    else if(bucket==='active-plugin'){s.pluginSkills+=count;s.pluginDirs+=1}
    else if(bucket==='active-connector'){s.connectorSkills+=duplicateRuntime?0:count;s.connectorDirs+=1}
    else if(bucket==='installed-disabled')s.installedDirs+=1;
    else if(bucket==='source-cache'){s.cacheSkills+=count;s.cacheDirs+=1}
    else if(bucket==='source-catalog'){s.catalogSkills+=count;s.catalogDirs+=1}
    else if(bucket==='review-copy'||bucket==='project-local'||bucket==='unknown'){s.reviewSkills+=count;s.reviewDirs+=1}
  });
  s.sourceOnlySkills=s.cacheSkills+s.catalogSkills;
  s.sourceOnlyDirs=s.cacheDirs+s.catalogDirs;
  return s;
}

function currentAgentGroup(){
  const current=targets.find(t=>t.is_current);
  if(current){
    const group=targetGroups.find(g=>g.dirs.some(d=>d.path===current.path));
    if(group)return group;
  }
  const currentName=current?.name||scan?.target?.label;
  return targetGroups.find(g=>g.agent===currentName)||null;
}

// 按 group.agent 关键词匹配 mcpInventory 条目(claude/codex/cursor)。
// 仪表盘当前 Agent 计数 + 能力来源页各卡片内联 MCP 区块共用同一口径,
// 避免两份关键词列表漂移。
function findAgentMcpEntry(group){
  if(!mcpInventory||!mcpInventory.agents)return null;
  const cur=(group?.agent||'').toLowerCase();
  if(!cur)return null;
  let key='';
  if(cur.includes('claude'))key='claude';
  else if(cur.includes('codex'))key='codex';
  else if(cur.includes('cursor'))key='cursor';
  for(const b of mcpInventory.agents){
    const ba=b.agent.toLowerCase();
    if((key&&ba.includes(key))||ba===cur)return b;
  }
  return null;
}
function currentAgentMcpCount(){
  const b=findAgentMcpEntry(currentAgentGroup());
  if(!b)return 0;
  return b.sources.reduce((n,s)=>n+((s.servers||[]).length),0);
}
function currentCapabilitySummary(){
  const group=currentAgentGroup();
  const dirs=group?.dirs||targets;
  return {
    ...summarizeCapabilityDirs(dirs),
    agent:group?.agent||targets.find(t=>t.is_current)?.name||scan?.target?.label||'当前 Agent',
    profile:group?.profile_summary||null,
    mcpCount:currentAgentMcpCount(),
  };
}

function compactCapabilityParts(s){
  const parts=[];
  if(s.userSkills)parts.push(`用户 ${s.userSkills}`);
  if(s.systemSkills)parts.push(`系统 ${s.systemSkills}`);
  if(s.pluginDirs)parts.push(`插件 ${s.pluginDirs}`);
  if(s.connectorDirs)parts.push(`连接器 ${s.connectorDirs}`);
  if(s.supportSkillCount)parts.push(`支撑 ${s.supportSkillCount}`);
  if(s.commandCount)parts.push(`commands ${s.commandCount}`);
  return parts.join(' · ')||'暂无运行态解释';
}

function formatHostProfileSummary(profile){
  if(!profile)return '';
  const parts=[];
  if(profile.family)parts.push(profile.family);
  if(profile.source_root_count)parts.push(`来源根 ${profile.source_root_count}`);
  if(profile.mcp_runtime_server_count)parts.push(`运行 MCP ${profile.mcp_runtime_server_count}`);
  if(profile.mcp_catalog_server_count)parts.push(`市场 MCP ${profile.mcp_catalog_server_count}`);
  else if(profile.mcp_server_count)parts.push(`MCP ${profile.mcp_server_count}`);
  return parts.join(' · ');
}

async function fetchTargets(force=false){
  const now=Date.now();
  if(!force&&_targetsCache&&(now-_targetsCacheTs)<TARGETS_CACHE_TTL){
    return _targetsCache;
  }
  try{
    const data=await fetch('/api/targets'+(force?'?refresh=1':'')).then(r=>r.json());
    _targetsCache=data;
    _targetsCacheTs=now;
    return data;
  }catch(e){
    return _targetsCache;
  }
}

async function loadCachedScanResult(){
  const r=await fetch('/api/scan-result').then(r=>r.json()).catch(()=>null);
  if(r&&!r.error&&r.scanned_at){
    if(r.scan_schema_version!==4){
      scanResult=null;
      health=null;
      globalOverlap=null;
      renderIssues();
      renderStats();renderWorkbench();
      updateDiagBadges();
      return;
    }
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
    renderIssues();
    renderStats();renderWorkbench();
    updateDiagBadges();
  }
}
async function loadData(){
  // Layer 0: fast-scan (instant skill list + classification)
  const sr=await fetch('/api/fast-scan').then(r=>r.json()).catch(()=>null);
  scan=sr;skills=sr?.installed||[];
  // Sanitize descriptions to prevent template literal injection
  skills.forEach(s=>{if(s.description)s.description=safeDesc(s.description)});
  loadCategoryOverrides();
  skills.forEach(s=>{
    if(categoryOverrides[s.name]){s.category=categoryOverrides[s.name];s.categorySource='user'}
    else if(!s.category||s.category===''||!CAT_NAMES[s.category]){
      s.category=classifySkillJS(s.name,s.description);s.categorySource='keyword'
    }else{s.categorySource='frontmatter'}
  });
  render();
  // Load global category stats (cached, fast on repeat)
  fetch('/api/global-stats').then(r=>r.json()).catch(()=>null).then(gs=>{
    if(gs){globalStats=gs;renderStats();renderWorkbench();renderCategories()}
  });
  // Load global overlap data (cross-directory duplicates)
  // Removed: now user-triggered via scan panel
  // Fallback: load targets as sources (directory list)
  if(!scan?.sources?.length){
    try{
      const td=await fetchTargets();
      const ts=td.targets||td;
      if(ts?.length){
        targets=ts;targetGroups=td.groups||[];
        scan.sources=ts.map(t=>({name:t.name,display_name:t.name,path:t.rel||t.path,count:t.count}));
        if($('view-sources').classList.contains('active')){
          renderSources();
          updateTargetSelector(false,'full');
        }else{
          updateTargetSelector(false,'sidebar');
        }
        renderStats();renderWorkbench();
      }
    }catch(e){}
  }
  // Load cached scan results (if user previously ran a scan)
  loadCachedScanResult();
  loadTrash();
  // Load installed plugins
  fetch('/api/installed-plugins').then(r=>r.json()).catch(()=>null).then(d=>{
    if(d){installedPlugins=d.plugins||[];enabledPlugins=d.enabled||[];knownMarketplaces=d.marketplaces||[];renderStats();renderWorkbench();}
  });
  fetch('/api/mcp-inventory').then(r=>r.json()).catch(()=>null).then(d=>{
    if(d){mcpInventory=d;renderStats();if(typeof renderSources==='function')renderSources();}
  });
}

// JS-side keyword classification (mirrors nlp.sh taxonomy)
const CAT_KW={
  'code-dev':['tdd','frontend','backend','api','debug','refactor','lint','ci','git','commit','pull-request','review','typescript','python','rust','react','vue','next','npm','pnpm','bun','代码','dev','ios','qa','fix','clean','deploy','benchmark','test','plan','eng','devex','guard','spec','code','ios-','sync','qa-only','learning','learn','handoff','交接','checkpoint','cleanup','session','skill-manager'],
  'content':['write','article','blog','copywriting','seo','newsletter','文章','写作','创作','选题','大纲','稿件','essay','writer','content','khazix','title','prd','explain','解释','洞察','insight','report'],
  'image-gen':['image','picture','photo','cover','banner','illustration','封面','配图','dalle','midjourney','flux','logo','glm-image','seedream','illustrator','picgo'],
  'video-audio':['video','audio','ffmpeg','remotion','mp4','podcast','subtitle','srt','tts','voice','视频','音频','字幕'],
  'data':['data','analytics','chart','csv','excel','dashboard','visualization','stats','metrics','sql','报告','报表','统计','analysis','笔记','知识库','note','knowledge'],
  'web-search':['search','web','browse','scrape','crawl','spider','搜索','抓取','爬虫','google','bing','perplexity','web-access','gstack'],
  'social':['小红书','xhs','twitter','weibo','公众号','wechat','instagram','tiktok','抖音','微博','linkedin','threads','youtube','bilibili','wechat-styler'],
  'doc':['pdf','docx','pptx','xlsx','notion','confluence','文档','readme','spec','make-pdf','document'],
  'comms':['email','mail','slack','feishu','lark','dingtalk','telegram','discord','飞书','邮件','钉钉'],
  'design':['figma','canvas','theme','brand','sketch','wireframe','prototype','tailwind','css','设计','design','design-html'],
  'translate':['translate','translation','i18n','l10n','locale','翻译','多语言'],
  'sysadmin':['server','docker','k8s','kubernetes','devops','ssh','linux','nginx','infra','terraform','aws','gcp','azure','运维','部署','deploy','setup','macos','sleep','caffeinate','pmset'],
  'persona':['personality','persona','mbti','sbti','character','role','人格','角色','蒸馏','elon','feynman','女娲','造人','skill'],
  'finance':['finance','invoice','receipt','stock','trade','accounting','发票','财务','金融'],
  'sales':['sales','crm','销售','线索','客户','lead','求职','岗位','面试','简历','boss','job','hiring']
};
function classifySkillJS(name,desc){
  const low=(name+' '+(desc||'')).toLowerCase();let best='other',bestScore=0;
  for(const[cat,kws]of Object.entries(CAT_KW)){
    const score=kws.filter(kw=>low.includes(kw)).length;
    if(score>bestScore){bestScore=score;best=cat}
  }
  return bestScore>0?best:'other';
}

function render(){
  renderTarget();renderStats();renderWorkbench();renderCategories();
  renderIssues();renderSkillsList();
  const onSources=$('view-sources')?.classList.contains('active');
  if(onSources){
    renderSources();
    updateTargetSelector(false,'full');
  }else{
    updateTargetSelector(false,'sidebar');
  }
  $('badge-skills').textContent=skills.length;
  // badge-sources is maintained by updateTargetSelector / loadData targets fetch
  updateDiagBadges();
}

function getTriageMetrics(){
  const sameName=(globalOverlap?.duplicates_same_name||[]).length;
  const upstreams=health?.upstream_sources||[];
  const outdated=upstreams.filter(s=>s.status==='outdated').length;
  const changes=health?.content_changes?.changed?.length||0;
  const actionable=sameName+outdated+changes;
  const observed=sameName+upstreams.length+changes;
  return {
    sameName,upstreams,outdated,changes,
    actionable,observed,
    scannedDirs:scanResult?.scanned_dirs||globalStats?.targets_scanned||targetGroups.length||0,
    durationMs:scanResult?.duration_ms||0,
    scannedAt:scanResult?.scanned_at||health?.generated_at||null,
  };
}

function getCategoryActionCounts(){
  const counts={user:0,marketplace:0,cache:0,'cross-copy':0,project:0,unknown:0};
  const bump=(dir)=>{
    const c=_dirCategory(dir);
    counts[c]=(counts[c]||0)+1;
  };
  (globalOverlap?.duplicates_same_name||[]).forEach(dup=>{
    const cats=new Set((dup.locations||[]).map(l=>_dirCategory(l.dir)));
    cats.forEach(c=>{counts[c]=(counts[c]||0)+1});
  });
  (health?.upstream_sources||[]).filter(s=>s.status==='outdated').forEach(s=>bump(s.dir));
  return counts;
}

function updateDiagBadges(){
  if(!scanResult&&!health){
    $('badge-health').textContent='-';
    $('badge-issues').textContent='—';
    return;
  }
  const h=health;
  $('badge-health').textContent=h?.health_score?`${h.health_score.score}/100`:'-';
  const m=getTriageMetrics();
  $('badge-issues').textContent=m.actionable||'—';
}

function renderTarget(){
  const t=scan?.target;
  const ts=scan?.generated_at||health?.generated_at;
  if(ts)$('last-update').textContent=new Date(ts).toLocaleString('zh-CN');
}

function renderWorkbench(){
  const el=$('workbench-card');
  if(!el)return;
  const m=getTriageMetrics();
  const cap=currentCapabilitySummary();
  const policyCounts={
    manage:targets.filter(t=>sourcePolicy(t)==='manage').length,
    review:targets.filter(t=>sourcePolicy(t)==='review').length,
    observe:targets.filter(t=>sourcePolicy(t)==='observe'||sourcePolicy(t)==='hidden').length,
  };
  const current=targets.find(t=>t.is_current)||scan?.target;
  const currentName=current?.name||scan?.target?.label||'当前目录';
  const profileHint=formatHostProfileSummary(cap.profile);
  const roleHint=skillRoleSummaryText(cap.roleCounts);
  const scanMeta=scanResult
    ? `${m.scannedDirs} 个目录 · ${(m.durationMs/1000).toFixed(1)}s · ${new Date(m.scannedAt).toLocaleString('zh-CN')}`
    : health
    ? `最近诊断 · ${m.scannedDirs||'?'} 个目录 · ${m.scannedAt?new Date(m.scannedAt).toLocaleString('zh-CN'):''}`
    : `轻量模式 · ${globalStats?.targets_scanned||targetGroups.length||'?'} 个目录已发现`;
  const queue=[
    {
      title:'确认当前可用能力',
      desc:`${compactCapabilityParts(cap)}${roleHint?` · ${roleHint}`:''}`,
      count:cap.activeSkills||'--',
      action:'sources'
    },
    {
      title:'隔离仅库存来源',
      desc:`缓存 ${cap.cacheSkills||0} skills · 市场目录 ${cap.catalogSkills||0} skills`,
      count:cap.sourceOnlySkills||0,
      action:'sources'
    },
    {
      title:m.actionable?'处理整理线索':'建立整理基线',
      desc:m.actionable?`同名、上游、内容变更等 ${m.actionable} 条待复核`:'从重点目录开始，不把 marketplace/cache 当删除对象',
      count:m.actionable||'未扫描',
      action:'issues'
    }
  ];
  el.innerHTML=`<div class="workbench">
    <div class="card focus-panel">
      <div class="focus-head">
        <div>
          <div class="focus-kicker">Capability Map</div>
          <div class="focus-title">${cap.agent} 能力面</div>
          <div class="focus-sub">${cap.inventorySkills||skills.length} skills 库存 · ${cap.topLevelSkillCount||0} 顶层/工作流候选 · ${cap.supportSkillCount||0} 支撑型 · ${cap.activeDirs||0} 个当前可用来源 · ${scanMeta}${profileHint?` · ${profileHint}`:''}</div>
        </div>
        <div class="focus-score"><div class="num">${cap.activeSkills||'--'}</div><div class="lbl">当前能力</div></div>
      </div>
      <div class="queue-list">${queue.map((q,i)=>`<div class="queue-row">
        <div class="queue-rank">${i+1}</div>
        <div><div class="queue-title">${q.title}</div><div class="queue-desc">${q.desc}</div></div>
        <div class="queue-count">${q.count}</div>
      </div>`).join('')}</div>
      <div class="workbench-actions">
        <button class="btn btn-primary" onclick="goView('sources')">能力地图</button>
        <button class="btn" onclick="goView('issues')">${scanResult?'查看重点':'去重点扫描'}</button>
        <button class="btn" onclick="goView('skills')">当前目录</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h3>运行态摘要</h3><span class="sub">${currentName}</span></div>
      <div class="scope-grid">
        <div class="scope-card primary"><div class="scope-name"><span>当前可用</span><b>${cap.activeSkills||0}</b></div><div class="scope-desc">${compactCapabilityParts(cap)}${roleHint?` · ${roleHint}`:''}</div></div>
        <div class="scope-card warn"><div class="scope-name"><span>仅作来源</span><b>${cap.sourceOnlySkills||0}</b></div><div class="scope-desc">市场目录、缓存和旧包只解释来源，不等同上下文加载。</div></div>
        <div class="scope-card muted"><div class="scope-name"><span>整理队列</span><b>${m.actionable||0}</b></div><div class="scope-desc">${policyCounts.manage||0} 个可管理目录 · ${policyCounts.review||0} 个待复核目录 · ${policyCounts.observe||0} 个观察目录。</div></div>
      </div>
    </div>
  </div>`;
}

/* ── Stats ── */
function renderStats(){
  const h=health;
  const gTargets=targetGroups.length||globalStats?.targets_scanned||0;
  const m=getTriageMetrics();
  const actionable=(scanResult||health)?m.actionable:0;
  const cap=currentCapabilitySummary();
  $('stats-row').innerHTML=`
    <div class="stat s-unique" title="当前 Agent 可解释为运行态能力的 skill 数"><div class="val">${cap.activeSkills||skills.length}</div><div class="lbl">当前能力</div></div>
    <div class="stat" title="当前 Agent 已启用插件目录数"><div class="val">${cap.pluginDirs||0}</div><div class="lbl">启用插件</div></div>
    <div class="stat" title="当前 Agent 连接器能力包数量"><div class="val">${cap.connectorDirs||0}</div><div class="lbl">连接器</div></div>
    <div class="stat" style="cursor:pointer" onclick="goView('sources')" title="当前 Agent 配置的 MCP server 数（点击查看清单）"><div class="val">${cap.mcpCount||0}</div><div class="lbl">MCP</div></div>
    <div class="stat" title="市场目录、缓存、旧包等只作为来源库存的 skill 数"><div class="val">${cap.sourceOnlySkills||0}</div><div class="lbl">仅库存</div></div>
    <div class="stat s-libraries" title="已发现的 Agent/应用分组数"><div class="val">${gTargets}</div><div class="lbl">应用</div></div>
    <div class="stat s-issues" title="同名、上游过时、内容变更等需要人工复核的线索"><div class="val" style="color:${actionable>0?'var(--red)':'var(--green)'}">${actionable}</div><div class="lbl">待复核</div></div>`;
}

/* ── Categories ── */

/* ── Categories ── */
const CAT_NAMES={'code-dev':'代码开发','content':'内容创作','image-gen':'图片生成','video-audio':'视频/音频','data':'数据分析','web-search':'搜索','social':'社交','doc':'文档','comms':'通讯','design':'设计','translate':'翻译','sysadmin':'系统','persona':'人格','finance':'财务','sales':'销售/求职','other':'其他','':'未分类'};
const CAT_COLORS={'code-dev':'var(--indigo)','content':'var(--green)','image-gen':'var(--purple)','video-audio':'var(--red)','data':'var(--cyan)','web-search':'var(--amber)','social':'#C0658C','doc':'#8B7355','comms':'#5E8CA0','design':'#A67C52','translate':'#4E7A8C','sysadmin':'#7E6B8E','persona':'#9E6B7E','finance':'#B89B3A','sales':'#C08800','other':'var(--text-muted)','':'var(--text-muted)'};
const CAT_ABBR={'code-dev':'CD','content':'CN','image-gen':'IG','video-audio':'AV','data':'DA','web-search':'SR','social':'SO','doc':'DC','comms':'CM','design':'DS','translate':'TR','sysadmin':'SY','persona':'PS','finance':'FN','sales':'SL','other':'OT','':'·'};
function catLabel(c){return CAT_NAMES[c]||c}
function catAbbr(c){return CAT_ABBR[c]||String(c||'?').replace(/[^a-z0-9]/gi,'').slice(0,2).toUpperCase()||'?'}

function renderCategories(){
  // Build current-target distribution
  const localCm={};skills.forEach(s=>{const c=s.category||'other';localCm[c]=(localCm[c]||0)+1});
  const localTotal=skills.length||1;
  const localCats=Object.entries(localCm).sort((a,b)=>b[1]-a[1]);
  const localMx=localCats[0]?.[1]||1;
  const curLabel=targets.find(t=>t.is_current)?.name||'当前目录技能';

  if(globalStats){
    // Global distribution
    const gd=globalStats.category_distribution||{};
    const cats=Object.entries(gd).sort((a,b)=>b[1]-a[1]);const mx=cats[0]?.[1]||1;
    const total=globalStats.unique_skills||0;
    let html=`<div class="card"><div class="card-head"><h3>库存分类分布</h3><span class="sub">${total} skills · ${globalStats.targets_scanned||'?'} 个目录 · ${cats.length} 个分类</span></div>
    <div class="cat-grid">${cats.map(([c,n])=>{
      const pct=(n/total*100).toFixed(1);
      const barW=(n/mx*100).toFixed(0);
      return`<div class="cat-row"><div class="cat-icon" style="--cat-c:${CAT_COLORS[c]||'var(--indigo)'}">${catAbbr(c)}</div><div class="cat-name">${catLabel(c)}</div><div class="cat-bar-wrap"><div class="cat-bar" style="width:${barW}%;background:${CAT_COLORS[c]||'var(--indigo)'}"></div></div><div class="cat-num">${n} <span class="cat-pct">${pct}%</span></div></div>`;
    }).join('')}</div></div>`;
    // Current target distribution
    html+=`<div class="card" style="margin-top:12px"><div class="card-head"><h3>${safeDesc(curLabel)}</h3><span class="sub">${localTotal} skills · ${localCats.length} 个分类</span></div>
    <div class="cat-grid">${localCats.map(([c,n])=>{
      const pct=(n/localTotal*100).toFixed(1);
      const barW=(n/localMx*100).toFixed(0);
      return`<div class="cat-row"><div class="cat-icon" style="--cat-c:${CAT_COLORS[c]||'var(--indigo)'}">${catAbbr(c)}</div><div class="cat-name">${catLabel(c)}</div><div class="cat-bar-wrap"><div class="cat-bar" style="width:${barW}%;background:${CAT_COLORS[c]||'var(--indigo)'}"></div></div><div class="cat-num">${n} <span class="cat-pct">${pct}%</span></div></div>`;
    }).join('')}</div></div>`;
    $('cat-card').innerHTML=html;
  }else{
    // Fallback: current-target only
    $('cat-card').innerHTML=`<div class="card"><div class="card-head"><h3>${curLabel}</h3><span class="sub">${localCats.length} 类</span></div>
    <div class="cat-grid">${localCats.map(([c,n])=>`<div class="cat-row"><div class="cat-icon" style="--cat-c:${CAT_COLORS[c]||'var(--indigo)'}">${catAbbr(c)}</div><div class="cat-name">${catLabel(c)}</div><div class="cat-bar-wrap"><div class="cat-bar" style="width:${(n/localMx*100).toFixed(0)}%;background:${CAT_COLORS[c]||'var(--indigo)'}"></div></div><div class="cat-num">${n}</div></div>`).join('')}</div></div>`;
  }
}

/* ── Skill issue tags for list view ── */
function getSkillIssueTags(name){
  const tags=[];
  if(!health)return tags;
  // Structure issues
  const issue=(health.structure_issues||[]).find(i=>i.name===name);
  if(issue){
    if(issue.kind==='broken_symlink')tags.push({icon:'🔴',title:'损坏链接',color:'var(--red)'});
    else if(issue.kind==='no_frontmatter')tags.push({icon:'📋',title:'缺 frontmatter',color:'var(--amber)'});
  }
  // Cleanup candidates
  const cleanup=health.cleanup_candidates||[];
  if(cleanup.includes(name)){
    const sk=skills.find(s=>s.name===name);
    if(sk){
      if(!sk.description)tags.push({icon:'📝',title:'缺描述',color:'var(--text-muted)'});
      if(sk.oversized)tags.push({icon:'📦',title:'过大',color:'var(--indigo)'});
    }
  }
  // Content changes
  const changed=(health.content_changes?.changed||[]).find(c=>c.name===name);
  if(changed)tags.push({icon:'🔄',title:'内容已变更',color:'var(--accent)'});
  return tags;
}

function understandingLabels(items,limit=3){
  return (items||[]).slice(0,limit).map(x=>x.label||x.key||String(x)).filter(Boolean);
}
function skillUnderstandingText(s){
  const u=s?.understanding||{};
  return [
    s?.name||'',
    s?.description||'',
    u.summary_zh||'',
    ...understandingLabels(u.scenarios,5),
    ...understandingLabels(u.capabilities,5),
    ...understandingLabels(u.risks,3),
  ].join(' ').toLowerCase();
}
function renderSkillMiniUnderstanding(s){
  const u=s?.understanding||{};
  const summary=safeDesc(u.summary_zh||s.description||'暂无中文理解，点击查看原文');
  const tags=[
    ...understandingLabels(u.scenarios,2),
    ...understandingLabels(u.capabilities,3),
  ].slice(0,4);
  const tagHtml=tags.length?`<div class="skill-tags">${tags.map(t=>`<span class="skill-tag">${escapeHtml(t)}</span>`).join('')}</div>`:'';
  return `<div class="skill-summary" title="${esc(summary)}"><span class="fm-key">description</span><span class="fm-val">${escapeHtml(summary)}</span></div>${tagHtml}`;
}
function renderUnderstandingPanel(u,opts={}){
  if(!u||u.error)return '';
  const compact=!!opts.compact;
  const chip=(text,kind='')=>`<span class="skill-tag" style="${kind==='risk'?'color:var(--amber);border-color:color-mix(in srgb,var(--amber) 35%,var(--border-subtle))':''}">${escapeHtml(text)}</span>`;
  const scenarios=understandingLabels(u.scenarios,compact?3:6);
  const caps=understandingLabels(u.capabilities,compact?4:8);
  const risks=understandingLabels(u.risks,compact?2:6);
  const users=understandingLabels(u.target_users,compact?2:4);
  const evidence=(u.evidence||[]).slice(0,compact?2:4);
  return `<div style="border:1px solid var(--border);border-radius:8px;background:var(--bg-card-alt);padding:12px;margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap">
      <div style="font-size:12px;font-weight:700;color:var(--text)">理解摘要</div>
      <span class="skill-tag">离线规则</span>
      <span class="skill-tag">不消耗 token</span>
      ${u.needs_ai_translation?'<span class="skill-tag" style="color:var(--amber)">待 AI 翻译</span>':''}
      <span class="skill-tag">可信度 ${escapeHtml(u.confidence||'low')}</span>
    </div>
    <div style="font-size:13px;line-height:1.6;color:var(--text);margin-bottom:8px">${escapeHtml(u.summary_zh||'暂无摘要')}</div>
    ${scenarios.length||caps.length||users.length?`<div class="skill-tags" style="margin-bottom:8px">
      ${scenarios.map(t=>chip(t)).join('')}
      ${caps.map(t=>chip(t)).join('')}
      ${users.map(t=>chip(t)).join('')}
    </div>`:''}
    ${risks.length?`<div class="skill-tags" style="margin-bottom:8px">${risks.map(t=>chip(t,'risk')).join('')}</div>`:''}
    ${!compact&&evidence.length?`<details style="margin-top:8px"><summary style="font-size:11px;color:var(--text-muted);cursor:pointer">查看判断依据</summary>
      <div style="margin-top:6px;display:grid;gap:5px">${evidence.map(e=>`<div style="font-size:11px;color:var(--text-muted);line-height:1.5;border-left:2px solid var(--border);padding-left:8px">${escapeHtml(e)}</div>`).join('')}</div>
    </details>`:''}
  </div>`;
}
function absolutePathLabel(path){
  return path||'';
}
function copyPath(path){
  if(!path)return;
  navigator.clipboard.writeText(path).then(()=>showCopyToast('✓ 已复制路径：'+path)).catch(()=>toast('复制失败','error'));
}
function renderIssuePath(path){
  const p=absolutePathLabel(path);
  if(!p)return '';
  return `<div style="display:flex;align-items:center;gap:6px;min-width:0;margin-top:2px">
    <code style="font-size:10px;color:var(--text-muted);background:var(--bg-card-alt);border:1px solid var(--border-subtle);border-radius:4px;padding:1px 4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:420px" title="${esc(p)}">${escapeHtml(p)}</code>
    <button class="btn btn-sm" onclick="event.stopPropagation();copyPath('${esc(p)}')" style="font-size:9px;padding:1px 5px">复制路径</button>
  </div>`;
}

/* ── Skills list with category groups (horizontal bar + expandable) ── */
function renderSkillsList(){
  const q=($('search').value||'').toLowerCase();
  const filtered=skills.filter(s=>!q||skillUnderstandingText(s).includes(q));
  // Group by category
  const groups={};filtered.forEach(s=>{const c=s.category||'other';if(!groups[c])groups[c]=[];groups[c].push(s)});
  const sortedCats=Object.entries(groups).sort((a,b)=>b[1].length-a[1].length);
  const mx=sortedCats[0]?.[1].length||1;
  const total=filtered.length||1;
  let html='';
  if(sortedCats.length===0){html='<div class="empty">无匹配结果</div>'}
  else{sortedCats.forEach(([cat,sk])=>{
    const pct=(sk.length/total*100).toFixed(1);
    const barW=(sk.length/mx*100).toFixed(0);
    const icon=catAbbr(cat);
    const label=catLabel(cat);
    html+=`<div class="skill-group">
      <div class="skill-group-head" onclick="toggleGroup(this)" style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:6px;cursor:pointer;transition:background .1s ease">
        <span class="arrow" style="font-size:9px;color:var(--text-muted);transition:transform .15s">▶</span>
        <div class="cat-icon" style="--cat-c:${CAT_COLORS[cat]||'var(--indigo)'}">${icon}</div>
        <div class="cat-name" style="min-width:70px">${label}</div>
        <div class="cat-bar-wrap"><div class="cat-bar" style="width:${barW}%;background:${CAT_COLORS[cat]||'var(--indigo)'}"></div></div>
        <div class="cat-num">${sk.length} <span class="cat-pct">${pct}%</span></div>
      </div>
      <div class="skill-group-body">${sk.map(s=>{
        const issues=getSkillIssueTags(s.name);
        const issueHtml=issues.length?`<span style="display:inline-flex;gap:3px;margin-left:4px">${issues.map(t=>`<span style="font-size:10px;color:${t.color};cursor:help" title="${t.title}">${t.icon}</span>`).join('')}</span>`:'';
        return `<div class="skill-row" onclick="if(event.target.type!=='checkbox')showSkill('${esc(s.name)}')">
        <input type="checkbox" class="skill-check" ${selectedSkills.has(s.name)?'checked':''} onchange="toggleSkillSelect('${esc(s.name)}')">
        <div class="name">
          <div style="display:flex;align-items:center;min-width:0"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${s.name}</span>${issueHtml}</div>
          ${renderSkillMiniUnderstanding(s)}
        </div>
        <div class="agent">${s.categorySource==='frontmatter'?'📋':s.categorySource==='user'?'🏷️':''} ${s.agent||''}</div>
        <div><span class="kind ${s.kind==='symlink'?'k-symlink':s.kind==='broken_symlink'?'k-broken':'k-entity'}">${s.kind||'entity'}</span></div>
      </div>`;
      }).join('')}</div>
    </div>`;
  })}
  $('skills-list').innerHTML=html;
  updateBatchUI();
}

function toggleSkillSelect(name){
  if(selectedSkills.has(name))selectedSkills.delete(name);else selectedSkills.add(name);
  updateBatchUI();
}
function toggleSelectAll(){
  const checked=$('select-all').checked;
  const q=($('search').value||'').toLowerCase();
  const visible=skills.filter(s=>!q||skillUnderstandingText(s).includes(q)).map(s=>s.name);
  if(checked){visible.forEach(n=>selectedSkills.add(n))}else{visible.forEach(n=>selectedSkills.delete(n))}
  renderSkillsList();
}
function updateBatchUI(){
  const count=selectedSkills.size;
  $('batch-count').textContent=`已选 ${count} 个`;
  $('batch-delete-btn').disabled=count===0;
  $('select-all').checked=count>0&&count===skills.length;
}
async function batchDelete(){
  const names=[...selectedSkills];
  if(!names.length)return;
  if(!confirm(`确认删除 ${names.length} 个 skill？\n\n${names.join(', ')}`))return;
  let ok=0,fail=0;
  for(const name of names){
    try{const r=await fetch(`/api/skill/${name}`,{method:'DELETE'});const d=await r.json();d.ok?ok++:fail++}
    catch{fail++}
  }
  selectedSkills.clear();
  toast(`已删除 ${ok} 个${fail?`，${fail} 个失败`:''}`);
  invalidateTargetsCache();
  clearGlobalSearchCache();
  await loadData();
}

/* ── Export / Import ── */
function exportSkills(){
  const target=scan?.target?.path||'unknown';
  const lines=[
    '# Skill Dashboard Export',
    `# Target: ${target}`,
    `# Date: ${new Date().toLocaleString('zh-CN')}`,
    `# Count: ${skills.length}`,
    '',
    '## Installed Skills',
    ...skills.map(s=>`- ${s.name}${s.description?': '+safeDesc(s.description):''}`),
    '',
    '## GitHub Sources (paste these to import)',
    ...skills.map(s=>{
      const u=(health?.upstream_sources||[]).find(x=>x.name===s.name);
      return u?`- ${u.repo}`:`# ${s.name}: no source tracked`;
    }).filter(l=>!l.startsWith('#')),
    '',
    '## Import format (one per line)',
    '# https://github.com/user/repo',
    '# https://github.com/user/repo/tree/main/subdir',
  ];
  const blob=new Blob([lines.join('\n')],{type:'text/plain'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=`skills-${Date.now()}.txt`;a.click();
  URL.revokeObjectURL(url);
  toast(`已导出 ${skills.length} 个 skill`);
}
function showImportDialog(){
  $('modal-title').textContent='导入 Skills';
  $('modal-body').innerHTML=`<div style="font-family:-apple-system,sans-serif;font-size:13px;color:var(--text)">
    <div style="margin-bottom:12px;color:var(--text-muted)">粘贴 GitHub URL 列表，每行一个</div>
    <textarea id="import-text" placeholder='https://github.com/user/repo\nhttps://github.com/user/repo/tree/main/subdir' style="width:100%;height:140px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:12px;font-family:'SF Mono',monospace;resize:vertical;margin-bottom:12px"></textarea>
    <div id="import-result" style="display:none;padding:10px;border-radius:8px;margin-bottom:8px;font-size:12px"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn" onclick="$('modal').classList.add('hidden')">取消</button>
      <button class="btn btn-primary" id="import-btn" onclick="doImport()">导入</button>
    </div>
  </div>`;
  $('modal').classList.remove('hidden');
}
async function doImport(){
  const btn=$('import-btn');const result=$('import-result');
  const text=$('import-text').value.trim();
  if(!text){toast('请输入内容','error');return}
  const sources=text.split('\n').map(l=>l.trim()).filter(l=>l&&!l.startsWith('#'));
  if(!sources.length){toast('未找到有效的 URL','error');return}
  btn.disabled=true;btn.textContent='安装中...';
  result.style.display='block';result.style.background='var(--bg-card-alt)';result.style.color='var(--text-muted)';
  let ok=0,fail=0,logs=[];
  for(const source of sources){
    try{
      const r=await fetch('/api/steal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source})});
      const d=await r.json();
      d.ok?(ok++):(fail++);logs.push(`${d.ok?'✅':'❌'} ${source.slice(0,50)}`);
      result.textContent=logs.slice(-5).join('\n');
    }catch(e){fail++;logs.push(`❌ ${source.slice(0,50)}: ${e.message}`)}
  }
  result.style.background=fail===0?'var(--green-bg)':'var(--red-bg)';
  result.style.color=fail===0?'var(--green)':'var(--red)';
  result.textContent=`完成: ${ok} 成功${fail?`, ${fail} 失败`:''}\n`+logs.join('\n');
  btn.disabled=false;btn.textContent='导入';
  toast(`导入完成: ${ok} 成功${fail?`, ${fail} 失败`:''}`);
  invalidateTargetsCache();
  clearGlobalSearchCache();
  await loadData();
}

function toggleGroup(el){el.classList.toggle('open');el.nextElementSibling.classList.toggle('open')}

/* ── Upstream update helper (used by issues view) ── */
async function updateUpstream(name,ev,dir){
  const btn=ev.target;btn.disabled=true;
  const source=btn.dataset.source||'';
  btn.textContent='更新中...';
  try{
    const url=dir?`/api/skill/${name}/update?target=${encodeURIComponent(dir)}`:`/api/skill/${name}/update`;
    const r=await fetch(url,{method:'PATCH'});const d=await r.json();
    if(d.ok){toast(`${name} 已更新`);invalidateTargetsCache();await loadData()}else toast(d.error||'更新失败','error')}
  catch(e){toast('更新失败: '+e.message,'error')}
  finally{btn.disabled=false;btn.textContent=source==='vercel-lock'?'NPX 更新':source==='git-remote'?'Git 更新':'更新'}
}

/* ── Switch to a directory and show skill detail ── */
async function switchAndShow(dirPath,skillName){
  await switchTarget(dirPath);
  setTimeout(()=>showSkill(skillName),300);
}

/* ── Preview skill from any directory (no switch) ── */
async function previewSkill(dirPath,name){
  try{
    const [d,u]=await Promise.all([
      fetch(`/api/preview?dir=${encodeURIComponent(dirPath)}&name=${encodeURIComponent(name)}`).then(r=>r.json()),
      fetch(`/api/understand?dir=${encodeURIComponent(dirPath)}&name=${encodeURIComponent(name)}`).then(r=>r.json()).catch(()=>null)
    ]);
    if(d.error){toast(d.error,'error');return}
    $('modal').classList.remove('hidden');
    $('modal-title').textContent=`${name} — ${d.agent}`;
    $('modal-body').innerHTML=`<div style="font-size:12px;color:var(--text-muted);margin-bottom:8px">${d.dir.replace(/^\/Users\/[^/]+/,'~')}</div>
      ${d.description?`<div style="font-size:13px;margin-bottom:10px;padding:6px 10px;background:var(--bg-card-alt);border-radius:6px">${esc(d.description)}</div>`:''}
      ${renderUnderstandingPanel(u)}
      <pre style="font-size:11px;line-height:1.5;white-space:pre-wrap;word-break:break-word;max-height:400px;overflow-y:auto;background:var(--bg-card-alt);padding:12px;border-radius:6px">${esc(d.preview)}</pre>
      <div style="font-size:10px;color:var(--text-muted);margin-top:8px">${d.size} bytes</div>`;
  }catch(e){toast('预览失败','error')}
}

/* ── Scan Config Panel ── */
