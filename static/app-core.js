let scan=null,health=null,skills=[],diagState='idle';
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
function getTheme(){return localStorage.getItem('sd-theme')||'light'}
function applyTheme(t){
  document.documentElement.setAttribute('data-theme',t);
  localStorage.setItem('sd-theme',t);
  $('theme-icon').textContent=t==='dark'?'🌙':'☀️';
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
  const titles={dashboard:'仪表盘',skills:'当前目录技能',issues:'问题与整理',sources:'全部目录技能',trash:'垃圾站',history:'操作日志'};
  $('view-title').textContent=titles[v]||v;
  $('sidebar').classList.remove('open');
}
function goView(v){
  switchView(v,document.querySelector(`.nav-item[onclick*="${v}"]`));
}

/* ── Data ── */
let globalStats=null;
let favDirs=[];
async function loadFavDirs(){
  try{favDirs=await fetch('/api/favorite-dirs').then(r=>r.json())}catch{favDirs=[]}
}
let globalOverlap=null;
let scanResult=null; // cached scan results from /api/scan-run
let cleanupPlan=null; // dry-run governance plan from /api/cleanup-plan
let executionPlan=null; // executable-shaped preview; does not run actions
let cleanupExcludedActions=new Set();
let _compareData={};
let _issueSelected=new Set(); // tracks "name|dir" keys for issue cards
let _issueShowAll=false;
const SIGNATURE_SIMILARITY_DISPLAY_THRESHOLD=0.30;
const TFIDF_SIMILARITY_DISPLAY_THRESHOLD=0.50;
let _sourcesShowAll=false;
let _sourceViewMode=localStorage.getItem('sd-source-view')||'daily'; // daily | deep
let _expandedSourceAgent=null;
async function loadGlobalOverlap(){
  try{globalOverlap=await fetch('/api/global-overlap').then(r=>r.json())}catch{globalOverlap=null}
}

function isVisibleSimilarityGroup(g){
  const threshold=g?.source==='tfidf'?TFIDF_SIMILARITY_DISPLAY_THRESHOLD:SIGNATURE_SIMILARITY_DISPLAY_THRESHOLD;
  return (g?.score||0) >= threshold;
}
async function loadCachedScanResult(){
  const r=await fetch('/api/scan-result').then(r=>r.json()).catch(()=>null);
  if(r&&!r.error&&r.scanned_at){
    if(r.scan_schema_version!==2){
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
      overlap_groups:r.overlap_groups||[],
      content_changes:r.content_changes,
      structure_issues:[],
      cleanup_candidates:[],
      generated_at:r.scanned_at,
    };
    globalOverlap={
      duplicates_same_name:r.duplicates_same_name||[],
      duplicates_identical:r.duplicates_identical||[],
      agent_similar:r.agent_similar||{},
      total_identical:(r.duplicates_identical||[]).length,
    };
    renderIssues();
    renderStats();renderWorkbench();
    updateDiagBadges();
  }
}
async function saveFavDirs(){
  await fetch('/api/favorite-dirs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(favDirs)});
}
function isFav(path){return favDirs.includes(path)}
async function toggleFav(path){
  if(favDirs.includes(path)){
    favDirs=favDirs.filter(p=>p!==path);
  }else{
    favDirs.push(path);
  }
  await saveFavDirs();renderSources();updateTargetSelector();
}
async function loadData(){
  await loadFavDirs();
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
  // Load global overlap data (cross-directory duplicates + similarity)
  // Removed: now user-triggered via scan panel
  // Fallback: load targets as sources (directory list)
  if(!scan?.sources?.length){
    try{
      const td=await fetch('/api/targets').then(r=>r.json());
      const ts=td.targets||td;
      if(ts?.length){
        targets=ts;targetGroups=td.groups||[];
        scan.sources=ts.map(t=>({name:t.name,display_name:t.name,path:t.rel||t.path,count:t.count}));
        // Only rebuild if sources view hasn't been rendered yet by render()
        // (avoids destroying user's expanded state during slow /api/targets)
        if(!$('sources-list')?.children?.length){
          renderSources();
        }else{
          // Just update badges and sidebar — don't touch the DOM
          $('badge-sources').textContent=td.groups?.length||ts.length;
          updateTargetSelector();
        }
        renderStats();renderWorkbench();
      }
    }catch(e){}
  }
  // Load cached scan results (if user previously ran a scan)
  loadCachedScanResult();
  loadTrash();
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
  renderIssues();renderSources();renderSkillsList();
  updateTargetSelector();
  $('badge-skills').textContent=skills.length;
  // badge-sources is maintained by updateTargetSelector / loadData targets fetch
  updateDiagBadges();
}

function getTriageMetrics(){
  const sameName=(globalOverlap?.duplicates_same_name||[]).length;
  const overlapGroups=(health?.overlap_groups||[]).filter(isVisibleSimilarityGroup).length;
  const agentSimilar=Object.values(globalOverlap?.agent_similar||{}).reduce((s,g)=>s+g.filter(isVisibleSimilarityGroup).length,0);
  const similar=overlapGroups+agentSimilar;
  const upstreams=health?.upstream_sources||[];
  const outdated=upstreams.filter(s=>s.status==='outdated').length;
  const changes=health?.content_changes?.changed?.length||0;
  const actionable=sameName+similar+outdated+changes;
  const observed=sameName+similar+upstreams.length+changes;
  return {
    sameName,overlapGroups,agentSimilar,similar,upstreams,outdated,changes,
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
  const simGroups=[...(health?.overlap_groups||[]),...Object.values(globalOverlap?.agent_similar||{}).flat()].filter(isVisibleSimilarityGroup);
  simGroups.forEach(g=>{
    const meta=g.skills_meta||{};
    const cats=new Set();
    (g.skills||[]).forEach(s=>cats.add(_dirCategory((meta[s]||{}).dir||'')));
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
  const policyCounts={
    manage:targets.filter(t=>sourcePolicy(t)==='manage').length,
    review:targets.filter(t=>sourcePolicy(t)==='review').length,
    observe:targets.filter(t=>sourcePolicy(t)==='observe'||sourcePolicy(t)==='hidden').length,
  };
  const current=targets.find(t=>t.is_current)||scan?.target;
  const currentName=current?.name||scan?.target?.label||'当前目录';
  const scanMeta=scanResult
    ? `${m.scannedDirs} 个目录 · ${(m.durationMs/1000).toFixed(1)}s · ${new Date(m.scannedAt).toLocaleString('zh-CN')}`
    : health
    ? `最近诊断 · ${m.scannedDirs||'?'} 个目录 · ${m.scannedAt?new Date(m.scannedAt).toLocaleString('zh-CN'):''}`
    : `轻量模式 · ${globalStats?.targets_scanned||targetGroups.length||'?'} 个目录已发现`;
  const queue=[
    {
      title:m.outdated?`更新过时来源`:'先扫日常目录',
      desc:m.outdated?`${m.upstreams.length} 个有来源，${m.outdated} 个落后上游`:'先分析可管理/待复核目录；缓存和市场包留给全量审计',
      count:m.outdated||'未扫描',
      action:'issues'
    },
    {
      title:m.sameName?`合并同名重复`:'检查当前常用目录',
      desc:m.sameName?`先看用户自建和跨 Agent 副本，不急着碰 marketplace`:`${currentName} 当前 ${skills.length} 个 skills`,
      count:m.sameName||skills.length,
      action:m.sameName?'issues':'skills'
    },
    {
      title:m.similar?`复核相似功能`:'整理目录地图',
      desc:m.similar?`相似不等于重复，默认只展示需要人工复核的线索`:`${targetGroups.length||'?'} 个应用分组，按常用目录优先`,
      count:m.similar||targetGroups.length||'--',
      action:m.similar?'issues':'sources'
    }
  ];
  el.innerHTML=`<div class="workbench">
    <div class="card focus-panel">
      <div class="focus-head">
        <div>
          <div class="focus-kicker">Triage</div>
          <div class="focus-title">${m.actionable?`先复核 ${m.actionable} 条整理线索`:'先建立整理基线'}</div>
          <div class="focus-sub">${scanMeta}</div>
        </div>
        <div class="focus-score"><div class="num">${m.actionable||'--'}</div><div class="lbl">待复核</div></div>
      </div>
      <div class="queue-list">${queue.map((q,i)=>`<div class="queue-row">
        <div class="queue-rank">${i+1}</div>
        <div><div class="queue-title">${q.title}</div><div class="queue-desc">${q.desc}</div></div>
        <div class="queue-count">${q.count}</div>
      </div>`).join('')}</div>
      <div class="workbench-actions">
        <button class="btn btn-primary" onclick="goView('issues')">${scanResult?'查看重点':'去日常扫描'}</button>
        <button class="btn" onclick="goView('sources')">目录地图</button>
        <button class="btn" onclick="goView('skills')">当前目录</button>
      </div>
    </div>
    <div class="card">
      <div class="card-head"><h3>整理范围</h3><span class="sub">${currentName}</span></div>
      <div class="scope-grid">
        <div class="scope-card primary"><div class="scope-name"><span>可管理目录</span><b>${policyCounts.manage||0}</b></div><div class="scope-desc">当前/用户技能库，可作为日常整理对象。</div></div>
        <div class="scope-card warn"><div class="scope-name"><span>待复核目录</span><b>${policyCounts.review||0}</b></div><div class="scope-desc">导入副本和项目级 skill，先对比再删除。</div></div>
        <div class="scope-card muted"><div class="scope-name"><span>观察/隐藏</span><b>${policyCounts.observe||0}</b></div><div class="scope-desc">marketplace、缓存和内置包默认不进删除队列。</div></div>
      </div>
    </div>
  </div>`;
}

/* ── Stats ── */
function renderStats(){
  const h=health;
  const gUnique=globalStats?.unique_skills||0;
  const gTargets=targetGroups.length||globalStats?.targets_scanned||0;
  const m=getTriageMetrics();
  const actionable=(scanResult||health)?m.actionable:0;
  $('stats-row').innerHTML=`
    <div class="stat s-unique" title="跨所有技能库去重后的唯一 skill 数"><div class="val">${gUnique||skills.length}</div><div class="lbl">全量 Skills</div></div>
    <div class="stat s-libraries" title="已扫描的技能库目录数（实际目录，非合并数）"><div class="val">${gTargets}</div><div class="lbl">技能库</div></div>
    <div class="stat s-issues" title="同名、相似、上游过时、内容变更等需要人工复核的线索"><div class="val" style="color:${actionable>0?'var(--red)':'var(--green)'}">${actionable}</div><div class="lbl">待复核</div></div>`;
}

/* ── Categories ── */

/* ── Categories ── */
const CAT_NAMES={'code-dev':'💻 代码开发','content':'✍️ 内容创作','image-gen':'🎨 图片生成','video-audio':'🎬 视频/音频','data':'📊 数据分析','web-search':'🔍 搜索','social':'📱 社交','doc':'📄 文档','comms':'💬 通讯','design':'🖌️ 设计','translate':'🌐 翻译','sysadmin':'🖥️ 系统','persona':'🎭 人格','finance':'💰 财务','sales':'💼 销售/求职','other':'📦 其他','':'📦 未分类'};
const CAT_COLORS={'code-dev':'var(--indigo)','content':'var(--green)','image-gen':'var(--purple)','video-audio':'var(--red)','data':'var(--cyan)','web-search':'var(--amber)','social':'#f472b6','sales':'#f59e0b','other':'var(--text-muted)','':'var(--text-muted)'};

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
    let html=`<div class="card"><div class="card-head"><h3>📊 全量分类分布</h3><span class="sub">${total} skills · ${globalStats.targets_scanned||'?'} 个目录 · ${cats.length} 个分类</span></div>
    <div class="cat-grid">${cats.map(([c,n])=>{
      const pct=(n/total*100).toFixed(1);
      const barW=(n/mx*100).toFixed(0);
      return`<div class="cat-row"><div class="cat-icon">${(CAT_NAMES[c]||c).split(' ')[0]}</div><div class="cat-name">${(CAT_NAMES[c]||c).split(' ').slice(1).join(' ')||c}</div><div class="cat-bar-wrap"><div class="cat-bar" style="width:${barW}%;background:${CAT_COLORS[c]||'var(--indigo)'}"></div></div><div class="cat-num">${n} <span class="cat-pct">${pct}%</span></div></div>`;
    }).join('')}</div></div>`;
    // Current target distribution
    html+=`<div class="card" style="margin-top:12px"><div class="card-head"><h3>📂 ${safeDesc(curLabel)}</h3><span class="sub">${localTotal} skills · ${localCats.length} 个分类</span></div>
    <div class="cat-grid">${localCats.map(([c,n])=>{
      const pct=(n/localTotal*100).toFixed(1);
      const barW=(n/localMx*100).toFixed(0);
      return`<div class="cat-row"><div class="cat-icon">${(CAT_NAMES[c]||c).split(' ')[0]}</div><div class="cat-name">${(CAT_NAMES[c]||c).split(' ').slice(1).join(' ')||c}</div><div class="cat-bar-wrap"><div class="cat-bar" style="width:${barW}%;background:${CAT_COLORS[c]||'var(--indigo)'}"></div></div><div class="cat-num">${n} <span class="cat-pct">${pct}%</span></div></div>`;
    }).join('')}</div></div>`;
    $('cat-card').innerHTML=html;
  }else{
    // Fallback: current-target only
    $('cat-card').innerHTML=`<div class="card"><div class="card-head"><h3>📂 ${curLabel}</h3><span class="sub">${localCats.length} 类</span></div>
    <div class="cat-grid">${localCats.map(([c,n])=>`<div class="cat-row"><div class="cat-icon">${(CAT_NAMES[c]||c).split(' ')[0]}</div><div class="cat-name">${(CAT_NAMES[c]||c).split(' ').slice(1).join(' ')||c}</div><div class="cat-bar-wrap"><div class="cat-bar" style="width:${(n/localMx*100).toFixed(0)}%;background:${CAT_COLORS[c]||'var(--indigo)'}"></div></div><div class="cat-num">${n}</div></div>`).join('')}</div></div>`;
  }
}

/* ── Skill issue tags for list view ── */
function getSkillIssueTags(name){
  const tags=[];
  if(!health)return tags;
  // Similar skills (overlap_groups)
  const similarGroup=(health.overlap_groups||[]).filter(isVisibleSimilarityGroup).find(g=>g.skills.includes(name));
  if(similarGroup)tags.push({icon:'🔍',title:`相似: ${Math.round((similarGroup.score||0)*100)}%`,color:'var(--amber)'});
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
  return `<div class="skill-summary" title="${esc(summary)}">${escapeHtml(summary)}</div>${tagHtml}`;
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
function renderSimilarityReason(g){
  const terms=(g.shared_terms||[]).slice(0,8);
  const pair=g.strongest_pair;
  const pairText=pair?.skills?.length===2?`最强配对：${pair.skills.join(' ↔ ')} · ${Math.round((pair.score||0)*100)}%`:'';
  const termText=terms.length?`共享关键词：${terms.join('、')}`:'旧扫描结果未记录共同词，重新扫描后会显示原因';
  const sourceText=g.source==='tfidf'
    ? '深度内容审计：TF-IDF 全文相似，只作为复核线索。'
    : '轻量相似：基于名称、description、keywords 和标题的关键词重叠。';
  return `<div style="font-size:11px;color:var(--text-muted);line-height:1.5;padding:6px 10px;background:var(--bg-card-alt);border-top:1px solid var(--border-subtle)">
    ${escapeHtml(termText)}${pairText?`<br>${escapeHtml(pairText)}`:''}<br>
    <span style="color:var(--amber)">规则说明：${escapeHtml(sourceText)}不等于可删除。</span>
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
    const icon=(CAT_NAMES[cat]||cat).split(' ')[0];
    const label=(CAT_NAMES[cat]||cat).split(' ').slice(1).join(' ')||cat;
    html+=`<div class="skill-group">
      <div class="skill-group-head" onclick="toggleGroup(this)" style="display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:6px;cursor:pointer;transition:background .1s ease">
        <span class="arrow" style="font-size:9px;color:var(--text-muted);transition:transform .15s">▶</span>
        <div class="cat-icon">${icon}</div>
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
  await loadData();
}

function toggleGroup(el){el.classList.toggle('open');el.nextElementSibling.classList.toggle('open')}

/* ── Upstream update helper (used by issues view) ── */
async function updateUpstream(name,ev){
  const btn=ev.target;btn.disabled=true;btn.textContent='更新中...';
  try{const r=await fetch(`/api/skill/${name}/update`,{method:'PATCH'});const d=await r.json();
    if(d.ok){toast(`${name} 已更新`);await loadData()}else toast(d.error||'更新失败','error')}
  catch(e){toast('更新失败: '+e.message,'error')}
  finally{btn.disabled=false;btn.textContent='更新'}
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
