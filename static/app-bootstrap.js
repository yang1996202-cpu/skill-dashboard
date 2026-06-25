/* ── Target selector (custom dropdown) ── */
let targets=[];
function toggleTargetDropdown(){
  $('target-dropdown').classList.toggle('open');
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.target-bar'))$('target-dropdown').classList.remove('open');
});

let targetGroups=[];
async function updateTargetSelector(force=false,scope='full'){
  let data;
  try{data=await fetchTargets(force)}catch{return}
  targets=data.targets||data;
  targetGroups=data.groups||[];
  const cur=targets.find(t=>t.is_current)||targets[0];
  if(cur){
    $('t-icon').textContent=cur.scope==='global'?'🌐':'📁';
    $('t-name').textContent=cur.name;
    $('t-scope').textContent=cur.scope==='global'?'全局':'项目级';
    $('t-count').textContent=cur.count;
  }
  // Sidebar dropdown always shows all directories; view filtering lives on the Sources page.
  const displayGroups=sortGroupsByCurrentAndSize(filterGroupsByView(targetGroups,'all'));
  // Show all groups with directories sorted by skill count
  $('target-dropdown').innerHTML=displayGroups.map(g=>{
    const isCurGroup=g.dirs.some(t=>t.is_current);
    const visibleDirs=[...g.dirs].sort((a,b)=>{
      const aCur=a.is_current?1:0;
      const bCur=b.is_current?1:0;
      if(aCur!==bCur)return bCur-aCur;
      return b.count-a.count;
    });
    const gId='tg-'+g.agent.replace(/[^a-zA-Z0-9]/g,'');
    return`<div class="tg-wrap${isCurGroup?' tg-active':''}" style="border-bottom:1px solid var(--border-subtle)">
      <div class="target-opt" onclick="toggleTgSub('${gId}',this)" style="padding:8px 10px">
        <span style="font-size:11px;transition:transform .15s" id="${gId}-arrow">${isCurGroup?'▼':'▶'}</span>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;font-size:12px;display:flex;align-items:center;gap:5px">${g.agent}${isCurGroup?'<span style="font-size:9px;color:var(--accent);font-weight:400">当前</span>':''}</div>
          <div style="font-size:10px;color:var(--text-muted)">${g.dirs.length} 个目录 · ${g.total_skills} skills</div>
        </div>
      </div>
      <div id="${gId}" style="display:${isCurGroup?'block':'none'};background:var(--bg-card-alt)">
        ${visibleDirs.map(t=>{
          return`<div class="target-opt${t.is_current?' active':''}" onclick="event.stopPropagation();switchTarget('${t.path}')" style="padding:6px 10px 6px 28px" title="${t.rel}">
            <span class="to-scope ${t.scope==='global'?'to-global':'to-project'}" style="font-size:9px">${t.scope==='global'?'🌐':'📁'}</span>
            <span style="flex:1;font-size:11px">${t.rel}</span>
            <span class="to-count" style="font-size:10px">${t.count}</span>
          </div>`;
        }).join('')}
      </div>
    </div>`;
  }).join('');
  $('badge-sources').textContent=targetGroups.length||targets.length;
  if(scope==='dropdown') return;
  renderStats();renderWorkbench();
  if(scope==='full') renderSources();
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
    // Optimistically mark the new current target and sync the cache so dropdown stays correct
    targets.forEach(t=>{t.is_current=(t.path===path)});
    if(_targetsCache&&_targetsCache.targets){
      _targetsCache.targets.forEach(t=>{t.is_current=(t.path===path)});
    }
    render();
    toast(`已切换 (${d.duration_ms}ms)`);
    updateTargetSelector(false,'sidebar');
    // Refresh global stats asynchronously
    fetch('/api/global-stats').then(r=>r.json()).catch(()=>null).then(gs=>{
      if(gs){globalStats=gs;renderStats();renderWorkbench();renderCategories()}
    });
  }catch(e){toast('切换失败: '+e.message,'error')}
}

/* ── Diagnosis ── */
async function checkCachedDiagnosis(){
  const r=await fetch('/api/diagnosis-status').then(r=>r.json()).catch(()=>null);
  if(!r)return;
  if((r.status==='cached'||r.status==='done')&&r.health_score){
    applyFullDiagnosis(r);
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
  if(!confirm(`确认对「${name}」运行完整诊断？\n\n将检测：结构问题、上游追踪、内容变化\n预计耗时 5-15 秒`))return;
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
      const phase=r.phase==='check'?'结构检查':'扫描来源';
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
      invalidateTargetsCache();
      clearGlobalSearchCache();
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

/* ── Operation history ── */
const HISTORY_OP_LABELS={
  move_to_trash:'移入垃圾站',
  empty_trash:'清空垃圾站',
  delete:'永久删除',
  restore:'恢复',
  switch_target:'切换目标目录',
  install:'安装 skill',
  copy:'复制 skill',
  update:'更新 skill',
  fix:'修复 skill',
  mark_duplicate_decision:'标记多端部署',
  remove_duplicate_decision:'撤销多端部署标记',
  add_source:'添加路径',
  remove_source:'移除路径',
  rehash:'确认内容变更'
};
const HISTORY_STATUS_LABELS={
  ok:'成功',
  partial:'部分成功',
  failed:'失败',
  blocked:'被阻止'
};
async function loadHistory(){
  const list=$('history-list');
  if(!list)return;
  list.innerHTML='<div class="empty" style="padding:30px 0">加载中...</div>';
  try{
    const rows=await fetch('/api/history').then(r=>r.json()).catch(()=>[]);
    if(!rows||!rows.length){
      list.innerHTML='<div class="empty" style="padding:30px 0">暂无操作记录</div>';
      return;
    }
    const html=rows.slice().reverse().map(row=>{
      const op=HISTORY_OP_LABELS[row.op]||row.op;
      const status=HISTORY_STATUS_LABELS[row.status]||row.status;
      const statusColor=row.status==='ok'?'var(--green)':row.status==='partial'?'var(--amber)':'var(--red)';
      const paths=(row.paths||[]).map(p=>`<div style="font-family:monospace;font-size:11px;color:var(--text-dim);line-height:1.5;padding:2px 0;word-break:break-all">${escapeHtml(p)}</div>`).join('');
      const detail=row.detail?`<details style="margin-top:6px"><summary style="font-size:11px;color:var(--text-muted);cursor:pointer">详情</summary><pre style="font-size:10px;color:var(--text-dim);background:var(--bg-card-alt);padding:8px;border-radius:6px;margin-top:6px;overflow:auto;max-height:200px">${escapeHtml(JSON.stringify(row.detail,null,2))}</pre></details>`:'';
      return `<div class="card" style="margin-bottom:10px">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:6px">
          <span style="font-size:12px;font-weight:600;color:var(--text)">${escapeHtml(op)}</span>
          <span style="font-size:11px;color:${statusColor};font-weight:500">${escapeHtml(status)}</span>
          <span style="font-size:11px;color:var(--text-muted)">${row.count||0} 项</span>
          <span style="font-size:11px;color:var(--text-muted);margin-left:auto">${escapeHtml(row.ts||'')}</span>
        </div>
        <div style="margin-top:4px">${paths}</div>
        ${detail}
      </div>`;
    }).join('');
    list.innerHTML=`<div style="margin-bottom:12px;font-size:12px;color:var(--text-muted)">最近 ${rows.length} 条操作记录（最多保留 50 条）</div>${html}`;
  }catch(e){
    list.innerHTML='<div class="empty" style="padding:30px 0;color:var(--red)">加载失败：'+escapeHtml(e.message)+'</div>';
  }
}
