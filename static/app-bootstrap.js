/* ── Refresh ── */
async function refreshData(){
  $('refresh-btn').disabled=true;
  try{
    // Quick refresh: reload fast-scan only
    const sr=await fetch('/api/fast-scan').then(r=>r.json()).catch(()=>null);
    if(sr){
      scan=sr;skills=sr?.installed||[];
      skills.forEach(s=>{if(s.description)s.description=safeDesc(s.description)});
      loadCategoryOverrides();
      skills.forEach(s=>{
        if(categoryOverrides[s.name]){s.category=categoryOverrides[s.name];s.categorySource='user'}
        else if(!s.category||s.category===''||!CAT_NAMES[s.category]){
          s.category=classifySkillJS(s.name,s.description);s.categorySource='keyword'
        }else{s.categorySource='frontmatter'}
      });
      render();
    }
    await loadSourcesFallback();
    toast('已刷新');
  }catch(e){toast('刷新失败','error')}
  finally{$('refresh-btn').disabled=false}
}

/* ── Target selector (custom dropdown) ── */
let targets=[];
function toggleTargetDropdown(){
  $('target-dropdown').classList.toggle('open');
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.target-bar'))$('target-dropdown').classList.remove('open');
});

let targetGroups=[];
async function updateTargetSelector(){
  let data;
  try{data=await fetch('/api/targets').then(r=>r.json())}catch{return}
  targets=data.targets||data;
  targetGroups=data.groups||[];
  const cur=targets.find(t=>t.is_current)||targets[0];
  if(cur){
    $('t-icon').textContent=cur.scope==='global'?'🌐':'📁';
    $('t-name').textContent=cur.name;
    $('t-scope').textContent=cur.scope==='global'?'全局':'项目级';
    $('t-count').textContent=cur.count;
  }
  // Empty state: guide user to mark favorites
  if(!favDirs.length){
    $('target-dropdown').innerHTML=`<div style="padding:16px 12px;text-align:center;color:var(--text-muted);font-size:12px">
      <div style="margin-bottom:8px">还没有设置常用目录</div>
      <div style="font-size:11px">去 <span style="color:var(--accent);cursor:pointer" onclick="switchView('sources',document.querySelector('.nav-item:nth-child(2)'));document.getElementById('target-dropdown').classList.remove('open')">📚 全部目录技能</span> 里标记 ⭐</div>
    </div>`;
    $('badge-sources').textContent=targetGroups.length||targets.length;
    renderStats();renderWorkbench();renderSources();
    return;
  }
  // Sort: current group first, then by skill count
  const sorted=[...targetGroups].sort((a,b)=>{
    const aCur=a.dirs.some(t=>t.is_current);
    const bCur=b.dirs.some(t=>t.is_current);
    if(aCur!==bCur)return aCur?-1:1;
    return b.total_skills-a.total_skills;
  });
  // Filter: only show groups that have at least one favorite directory
  const filtered=sorted.filter(g=>g.dirs.some(t=>isFav(t.path)));
  // For each group, only show favorite directories
  const visibleFor=(g)=>g.dirs.filter(t=>isFav(t.path));
  // Show groups with expandable sub-directories (two-level menu)
  $('target-dropdown').innerHTML=filtered.map(g=>{
    const isCurGroup=g.dirs.some(t=>t.is_current);
    const visibleDirs=visibleFor(g);
    const gId='tg-'+g.agent.replace(/[^a-zA-Z0-9]/g,'');
    return`<div class="tg-wrap${isCurGroup?' tg-active':''}" style="border-bottom:1px solid var(--border-subtle)">
      <div class="target-opt" onclick="toggleTgSub('${gId}',this)" style="padding:8px 10px">
        <span style="font-size:11px;transition:transform .15s" id="${gId}-arrow">${isCurGroup?'▼':'▶'}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:12px;display:flex;align-items:center;gap:5px">${g.agent}${isCurGroup?'<span style="font-size:9px;color:var(--accent);font-weight:400">当前</span>':''}</div>
          <div style="font-size:10px;color:var(--text-muted)">${visibleDirs.length} 个目录 · ${g.total_skills} skills</div>
        </div>
      </div>
      <div id="${gId}" style="display:${isCurGroup?'block':'none'};background:var(--bg-card-alt)">
        ${visibleDirs.map(t=>{
          return`<div class="target-opt${t.is_current?' active':''}" onclick="event.stopPropagation();switchTarget('${t.path}')" style="padding:6px 10px 6px 28px" title="${t.rel}">
            <span class="to-scope ${t.scope==='global'?'to-global':'to-project'}" style="font-size:9px">${t.scope==='global'?'🌐':'📁'}</span>
            <span style="flex:1;font-size:11px">${t.rel}</span>
            <span class="to-count" style="font-size:10px">${t.count}</span>
            <button class="btn btn-sm" style="font-size:8px;padding:1px 4px;margin-left:4px;flex-shrink:0;color:var(--text-muted);border-color:transparent" onclick="event.stopPropagation();toggleFav('${t.path}')" title="从常用移除">✕</button>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }).join('');
  $('badge-sources').textContent=targetGroups.length||targets.length;
  renderStats();renderWorkbench();renderSources();
}

function toggleTgSub(id,head){
  const sub=$(id);
  const arrow=$(id+'-arrow');
  if(!sub)return;
  const isOpen=sub.style.display!=='none';
  sub.style.display=isOpen?'none':'block';
  if(arrow)arrow.textContent=isOpen?'▶':'▼';
}

async function switchTarget(path){
  $('target-dropdown').classList.remove('open');
  if(!path)return;
  selectedSkills.clear();_issueSelected.clear();srcSelectedSkills.clear();
  $('refresh-btn').disabled=true;
  try{
    const r=await fetch('/api/target',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({target:path})});
    const d=await r.json();
    if(d.error){toast(d.error,'error');return}
    scan=d;skills=d?.installed||[];
    skills.forEach(s=>{if(s.description)s.description=safeDesc(s.description)});
    loadCategoryOverrides();
    skills.forEach(s=>{
      if(categoryOverrides[s.name]){s.category=categoryOverrides[s.name];s.categorySource='user'}
      else if(!s.category||s.category===''||!CAT_NAMES[s.category]){
        s.category=classifySkillJS(s.name,s.description);s.categorySource='keyword'
      }else{s.categorySource='frontmatter'}
    });
    render();
    toast(`已切换 (${d.duration_ms}ms)`);
    // Refresh targets + groups so dashboard/sources stay in sync
    try{
      const td=await fetch('/api/targets').then(r=>r.json());
      const ts=td.targets||td;
      if(ts?.length){
        targets=ts;targetGroups=td.groups||[];
        if(!scan?.sources?.length) scan.sources=ts.map(t=>({name:t.name,display_name:t.name,path:t.rel||t.path,count:t.count}));
        renderSources();
        $('badge-sources').textContent=targetGroups.length||ts.length;
        renderStats();renderWorkbench();
      }
    }catch(e){}
    // Refresh global stats
    fetch('/api/global-stats').then(r=>r.json()).catch(()=>null).then(gs=>{
      if(gs){globalStats=gs;renderStats();renderWorkbench();renderCategories()}
    });
  }catch(e){toast('切换失败: '+e.message,'error')}
  finally{$('refresh-btn').disabled=false}
}

/* ── Diagnosis ── */
async function checkCachedDiagnosis(){
  const r=await fetch('/api/diagnosis-status').then(r=>r.json()).catch(()=>null);
  if(!r)return;
  if((r.status==='cached'||r.status==='done')&&r.health_score){
    applyFullDiagnosis(r);
  }
  // Also load sources from full scan if available
  if(!scan.sources?.length){
    loadSourcesFallback();
  }
}

async function loadSourcesFallback(){
  // Only use stale /api/scan data if we don't have fresh /api/targets data
  if(scan.sources?.length) return;
  const fullScan=await fetch('/api/scan').then(r=>r.json()).catch(()=>null);
  if(fullScan?.sources?.length){
    scan.sources=fullScan.sources;
    renderSources();
    // badge-sources is maintained by updateTargetSelector — don't overwrite with flat source count
  }
}

function applyFullDiagnosis(d){
  health=d;
  // Don't overwrite fresh /api/targets data with stale diagnosis sources
  if(d.sources?.length && !scan.sources?.length){
    scan.sources=d.sources;
  }
  renderIssues();renderSources();renderStats();renderWorkbench();
  updateDiagBadges();
  const btn=$('diag-btn');
  const issuesBtn=$('issues-diag-btn');
  if(btn){btn.textContent='🔧 一键诊断';btn.disabled=false;btn.classList.remove('running');}
  if(issuesBtn){issuesBtn.textContent='🔧 重新检测';issuesBtn.disabled=false;issuesBtn.classList.remove('running');}
}

async function runDiagnosis(){
  const curTarget=targets.find(t=>t.is_current)||targets[0];
  const name=curTarget?curTarget.name:'当前目标库';
  if(!confirm(`确认对「${name}」运行完整诊断？\n\n将检测：相似度、上游追踪、结构问题\n预计耗时 5-15 秒`))return;
  const btn=$('diag-btn');
  const issuesBtn=$('issues-diag-btn');
  if(btn){btn.textContent='⏳ 启动中...';btn.disabled=true;btn.classList.add('running');}
  if(issuesBtn){issuesBtn.textContent='⏳ 启动中...';issuesBtn.disabled=true;issuesBtn.classList.add('running');}
  diagState='running';
  try{
    const r=await fetch('/api/diagnose',{method:'POST'}).then(r=>r.json());
    if(r.status==='running'){
      // Already running from before, start polling
      pollDiagnosis();
    }else if(r.status==='started'){
      pollDiagnosis();
    }else if(r.error){
      toast(r.error,'error');
      btn.textContent='🔧 一键诊断';btn.disabled=false;btn.classList.remove('running');
    }
  }catch(e){
    toast('诊断失败: '+e.message,'error');
    btn.textContent='🔧 一键诊断';btn.disabled=false;btn.classList.remove('running');
  }
}

function pollDiagnosis(){
  clearInterval(diagPollTimer);
  const btn=$('diag-btn');
  const issuesBtn=$('issues-diag-btn');
  diagPollTimer=setInterval(async()=>{
    const r=await fetch('/api/diagnosis-status').then(r=>r.json()).catch(()=>null);
    if(!r)return;
    if(r.status==='done'&&r.health_score){
      clearInterval(diagPollTimer);
      applyFullDiagnosis(r);
      toast(`诊断完成 (${(r.duration_ms/1000).toFixed(1)}s)`);
      diagState='done';
    }else if(r.status==='error'){
      clearInterval(diagPollTimer);
      toast('诊断出错: '+(r.error||'未知'),'error');
      if(btn){btn.textContent='🔧 一键诊断';btn.disabled=false;btn.classList.remove('running');}
      if(issuesBtn){issuesBtn.textContent='🔧 重新检测';issuesBtn.disabled=false;issuesBtn.classList.remove('running');}
      diagState='error';
    }else if(r.status==='running'){
      const phase=r.phase==='check'?'相似度分析':'扫描来源';
      const text=`⏳ ${phase}... ${(r.elapsed_ms/1000).toFixed(0)}s`;
      if(btn)btn.textContent=text;
      if(issuesBtn)issuesBtn.textContent=text;
    }
  },1500);
}

/* ── Install Skill (steal) ── */
function showStealDialog(){
  const curTarget=targets.find(t=>t.is_current)||targets[0];
  const name=curTarget?curTarget.name:'当前库';
  $('modal-title').textContent='安装 Skill';
  $('modal-body').innerHTML=`
    <div style="font-family:-apple-system,sans-serif;font-size:13px;color:var(--text)">
      <div style="margin-bottom:12px;color:var(--text-muted)">安装到: <strong style="color:var(--indigo)">${name}</strong></div>
      <label style="display:block;margin-bottom:6px;font-weight:500">来源 (GitHub URL 或 skill 名称)</label>
      <input id="steal-source" type="text" placeholder="https://github.com/user/repo 或 skill名称" style="width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);font-size:13px;font-family:inherit;margin-bottom:12px">
      <div id="steal-result" style="display:none;padding:10px;border-radius:8px;margin-bottom:8px;font-size:12px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn" onclick="$('modal').classList.add('hidden')">取消</button>
        <button class="btn btn-primary" id="steal-btn" onclick="doSteal()">安装</button>
      </div>
    </div>`;
  $('modal').classList.remove('hidden');
}

async function doSteal(){
  const source=$('steal-source').value.trim();
  if(!source){toast('请输入来源','error');return}
  const btn=$('steal-btn');
  const result=$('steal-result');
  btn.disabled=true;btn.textContent='安装中...';
  result.style.display='block';result.style.background='var(--bg-card-alt)';result.style.color='var(--text-muted)';
  result.textContent='正在安装...';
  try{
    const r=await fetch('/api/steal',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source})});
    const d=await r.json();
    if(d.ok){
      result.style.background='var(--green-bg)';result.style.color='var(--green)';
      result.textContent='✅ 安装成功';
      toast('Skill 已安装');
      await loadData();
    }else{
      result.style.background='var(--red-bg)';result.style.color='var(--red)';
      result.textContent='❌ '+(d.error||'安装失败');
    }
  }catch(e){
    result.style.background='var(--red-bg)';result.style.color='var(--red)';
    result.textContent='❌ '+e.message;
  }
  btn.disabled=false;btn.textContent='安装';
}

loadData();

/* — footer copy-to-clipboard — */
function copyChip(el,text,label){
  navigator.clipboard.writeText(text).then(function(){
    el.classList.add('copied');
    var orig=el.querySelector('span').textContent;
    el.querySelector('span').textContent='已复制';
    showCopyToast('✓ 已复制'+label+'：'+text);
    setTimeout(function(){
      el.classList.remove('copied');
      el.querySelector('span').textContent=orig;
    },1800);
  });
}
function showCopyToast(msg){
  var t=document.querySelector('.copy-toast');
  if(!t){t=document.createElement('div');t.className='copy-toast';document.body.appendChild(t);}
  t.textContent=msg;t.classList.add('show');
  setTimeout(function(){t.classList.remove('show');},2200);
}
