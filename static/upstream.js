// ── 上游检测视图(从「健康检测」独立出来的一级菜单) ──
// 集中两件事:① 上游检测(查上游新版本,消耗 GitHub API)② 待补来源(三信号全空的 skill,补上游)。
// 数据复用全局 health/scanResult(由 runScan 填充),后端接口不变。
// 旧入口(issues 页 upstream/recover tab、sources 页补来源 badge)已删,只留本视图。
let _upstreamTab='upstream'; // 'upstream' | 'recover'
let _upShowAll=false;        // 上游 tab「待比对」列表截断(独立,不与 issues 页串)
let _upRecoverShowAll=false; // recover tab 目录截断
const _rcGroupData={};       // recover 目录卡懒展开数据(renderUpstreamView 填)

// 新视图永远跑 upstream 检测;附加 issues 页 _scanChecks 的 same-name/content-changes
// 保持 scanResult 完整(切去 issues 页数据还在,不污染)。runScan deferRender 跳过 renderIssues。
async function runUpstreamScan(opts={}){
  const checks=[...new Set([...(_scanChecks||[]),'upstream'])];
  const btn=$('upstream-scan-btn');
  const oldText=btn?btn.textContent:'';
  if(btn){btn.disabled=true;btn.textContent='⏳ 检测中(打 GitHub API,可能几十秒)...';}
  try{
    await runScan(null,{...opts,checks,deferRender:true,preserveIssueView:true});
    renderUpstreamView();
    updateDiagBadges();
  }catch(e){
    if(btn){btn.disabled=false;btn.textContent=oldText;}
    throw e;
  }
}

// 扫描配置区:scope 复用全局 _scanScope(与 issues 页共享);upstream 固定跑(本视图主功能)。
function renderUpstreamScanConfig(){
  const scopeLabelMap={current:'当前目录',active:'当前可用',inventory:'来源库存',review:'待复核',all:'全部目录'};
  const scopeLabel=scanResult?(scopeLabelMap[scanResult.scope]||scanResult.scope||'当前可用'):'';
  const tokenOk=scanResult?.github_token_configured;
  const est=scanResult?.upstream_api_estimate||0;
  const rl=scanResult?.github_rate_limit||{};
  let apiHint='';
  if(scanResult&&est>0){
    const quota=tokenOk?'5000 次/小时':'60 次/小时';
    apiHint=`<span style="color:var(--amber);margin-left:8px" title="upstream 检测对每个 skill 调 GitHub API">上游检测: ${est} 个 skill ≈ ${est} 次 API(${quota})</span>`;
    if(rl.limited){
      apiHint+=`<span style="color:var(--red);margin-left:8px" title="已触发 GitHub 限流">限流中,约 ${rl.reset_in_sec?Math.ceil(rl.reset_in_sec/60):0} 分钟后重置</span>`;
    }
  }
  const scopeBtn=(scope,label,title)=>{
    const active=_scanScope.has(scope);
    const check=active?'<span style="display:inline-block;width:10px;height:10px;border:1.5px solid currentColor;border-radius:2px;position:relative;vertical-align:-1px;margin-right:3px"><span style="position:absolute;left:1px;top:-2px;font-size:9px;line-height:1">✓</span></span>':'<span style="display:inline-block;width:10px;height:10px;border:1.5px solid var(--text-muted);border-radius:2px;vertical-align:-1px;margin-right:3px"></span>';
    return `<button class="btn btn-sm ${active?'btn-primary':''}" onclick="setScanScope('${scope}');renderUpstreamView()" title="${esc(title)}" style="${active?'':'background:var(--bg-card-alt);color:var(--text-muted)'}">${check}${label}</button>`;
  };
  let statusHtml='';
  if(scanResult){
    statusHtml=`<div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0;font-size:11px;color:var(--text-muted)">
      <span>上次扫描:${scopeLabel} · ${scanResult.scanned_dirs} 目录 · ${(scanResult.duration_ms/1000).toFixed(1)}s</span>
      ${tokenOk?`<span style="color:var(--green);margin-left:8px" title="已配置 GITHUB_TOKEN,额度 5000 次/小时">🔐 Token 已配置</span>`:`<span style="color:var(--amber);margin-left:8px" title="未配置 GITHUB_TOKEN,GitHub API 未认证额度 60 次/小时">⚠ 未配置 Token</span>`}
      ${apiHint}
    </div>`;
  }
  return `<div class="card" style="border-left:3px solid var(--accent);margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px">
      <button class="btn btn-primary" id="upstream-scan-btn" onclick="runUpstreamScan()" title="扫描选中范围,检测上游新版本 + 列出待补来源">🔍 开始上游检测</button>
      <button class="btn btn-sm" onclick="clearUpstreamCache()" title="清空上游检测缓存(24h 短路缓存),下次检测每个 skill 都走真实 GitHub API。已检测过没变的 skill 默认不烧 API,缓存可强制重查">🗑 清缓存</button>
      <span style="font-size:11px;color:var(--text-muted)">扫描选中范围,检测上游新版本 + 列出待补来源 skill。已检测过、SKILL.md 没变的 skill 24h 内不重复烧 API(缓存落盘,重启不丢)。</span>
      <div style="display:flex;gap:4px;align-items:center;margin-left:auto" title="扫描范围跟能力来源页视图映照">
        ${scopeBtn('current','当前目录','只扫当前 target 目录')}
        ${scopeBtn('active','当前可用','映照「能力来源 → 当前可用」')}
        ${scopeBtn('inventory','来源库存','映照「能力来源 → 来源库存」')}
        ${scopeBtn('review','待复核','映照「能力来源 → 待复核」')}
      </div>
    </div>
    <div style="font-size:11px;color:var(--text-muted)">扫描范围: ${getSelectedScanScopeTargets().length} 目录(多选 toggle,再点取消)</div>
    ${statusHtml}
  </div>`;
}

function renderUpstreamView(){
  const list=$('upstream-list');
  if(!list)return;
  const upstreams=health?.upstream_sources||[];
  const upstreamAll=upstreams.filter(s=>s.repo);
  const recoverDirs=(health?.source_status||[]).filter(s=>s.source==='unknown');
  const tabs=[
    {key:'upstream',emoji:'🔗',label:'上游检测',count:upstreamAll.length},
    {key:'recover',emoji:'◆',label:'待补来源',title:'没有任何上游来源留痕的 skill(steal-meta/.git/lock 三信号全空),点补来源按内容搜回上游',count:recoverDirs.length},
  ];
  const curDef=tabs.find(t=>t.key===_upstreamTab)||tabs[0];
  _upstreamTab=curDef.key;
  const tabBtn=(t)=>`<button class="issue-tab ${_upstreamTab===t.key?'active':''}" onclick="_upstreamTab='${t.key}';_upShowAll=false;_upRecoverShowAll=false;renderUpstreamView()" title="${esc(t.title||'')}"><span>${t.emoji}</span><span>${t.label}</span>${t.count?`<b>${t.count}</b>`:''}</button>`;

  let h=`<div style="margin-bottom:8px;display:flex;align-items:center;gap:8px;flex-wrap:wrap">
    <h3 style="font-size:15px;font-weight:600">🔗 上游检测</h3>
    <span style="font-size:11px;color:var(--text-muted)">追踪 skill 上游仓库新版本 + 补齐缺失来源</span>
  </div>`;
  h+=renderUpstreamScanConfig();

  // 未扫描且无数据 → 空状态
  if(!scanResult&&(!health||(!upstreams.length&&!recoverDirs.length))){
    h+=`<div class="empty" style="padding:30px 0">点击「🔍 开始上游检测」扫描目录,查看上游新版本与待补来源 skill。</div>`;
    list.innerHTML=h;
    return;
  }

  h+='<div class="issue-tabs">'+tabs.map(tabBtn).join('')+'</div>';

  const LIMIT=12;
  if(_upstreamTab==='upstream'){
    h+=renderUpstreamTab(upstreamAll,LIMIT);
  }else{
    h+=renderRecoverTab(recoverDirs,LIMIT);
  }
  list.innerHTML=h;
}

// ── 上游检测 tab:outdated(按 agent 折叠,带「更新」按钮)+ pendingCompare ──
function renderUpstreamTab(upstreamAll,LIMIT){
  if(!upstreamAll.length){
    return `<div class="empty" style="padding:30px 0">✅ 没有检测到带上游仓库的 skill(未配置 Token 时也只列已识别来源的;点「开始上游检测」刷新)。</div>`;
  }
  const outdated=upstreamAll.filter(s=>s.status==='outdated');
  const pendingCompare=upstreamAll.filter(s=>s.status!=='outdated');
  const headTag=[outdated.length&&`${outdated.length} 个过时`,pendingCompare.length&&`${pendingCompare.length} 个已最新/待比对`].filter(Boolean).join(' · ')||'无过时';
  const SOURCE_LABEL={
    'steal-meta':['Steal安装','通过 Skill Dashboard 从 GitHub 安装'],
    'git-remote':['Git仓库','目录本身是一个 Git 仓库,可 git pull'],
    'vercel-lock':['NPX/Vercel','通过 npx skills add 安装,记录在 ~/.agents/.skill-lock.json'],
    'unknown':['未知','无法识别上游来源']
  };
  let h=`<section class="issue-section"><div class="issue-section-head"><div><h3>🔗 上游追踪</h3><p>只提示可复核更新,不自动改文件。未配置 token 时仅展示检测到的来源;只有 status=outdated 才标"过时"。</p></div><span>${headTag}</span></div>`;
  h+=`<div class="card issue-list-card">`;
  if(outdated.length){
    // 先按 canonical_dir 去重(合并 symlink 副本,避免 N 个相同更新按钮)
    const upstreamGroups={};
    outdated.forEach(s=>{
      const key=s.canonical_dir||s.dir;
      if(!upstreamGroups[key])upstreamGroups[key]={...s,copies:[]};
      upstreamGroups[key].copies.push({dir:s.dir,is_symlink:s.is_symlink,link_target:s.link_target});
    });
    // 再按应用(agent)分组,套折叠卡
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
        ?`<div class="notice-line"><span>显示全部 ${pendingCompare.length} 个</span><button class="btn btn-sm" onclick="_upShowAll=false;renderUpstreamView()">只看前 ${LIMIT}</button></div>`
        :`<div class="notice-line"><span>显示前 ${LIMIT} / 共 ${pendingCompare.length} 个</span><button class="btn btn-sm btn-primary" onclick="_upShowAll=true;renderUpstreamView()">显示全部 ${pendingCompare.length}</button></div>`;
    }
    h+=`</div></div>`;
  }
  h+=`</div></section>`;
  return h;
}

// ── 待补来源 tab:按目录分组,卡内 skill 懒展开 ──
function renderRecoverTab(recoverDirs,LIMIT){
  if(!recoverDirs.length){
    return `<div class="empty" style="padding:30px 0">✅ 所有 skill 都有上游来源留痕(或尚未扫描)。点「开始上游检测」刷新。</div>`;
  }
  const recoverGroups={};
  recoverDirs.forEach(s=>{(recoverGroups[s.dir]=recoverGroups[s.dir]||[]).push(s);});
  const recoverGroupList=Object.entries(recoverGroups)
    .map(([dir,skills])=>({dir,skills}))
    .sort((a,b)=>b.skills.length-a.skills.length);
  let h=`<section class="issue-section"><div class="issue-section-head"><div><h3 style="color:var(--amber)">◆ 待补来源</h3><p>这些 skill 没有任何上游来源留痕(steal-meta / .git / lock 三信号全空)。按目录分组,展开点「补来源」按 SKILL.md 内容搜回上游仓库。</p></div><span>${recoverDirs.length} skill · ${recoverGroupList.length} 目录</span></div>`;
  h+=`<div class="issue-card-grid">`;
  const gLim=_upRecoverShowAll?recoverGroupList.length:Math.min(LIMIT,recoverGroupList.length);
  recoverGroupList.slice(0,gLim).forEach((g,i)=>{
    const gid='rc'+i+'-'+Math.random().toString(36).slice(2,6);
    _rcGroupData[gid]=g.skills;
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
    h+=_upRecoverShowAll
      ?`<div class="notice-line"><span>显示全部 ${recoverGroupList.length} 个目录</span><button class="btn btn-sm" onclick="_upRecoverShowAll=false;renderUpstreamView()">只看前 ${LIMIT}</button></div>`
      :`<div class="notice-line"><span>显示前 ${LIMIT} / 共 ${recoverGroupList.length} 个目录(${recoverDirs.length} skill)</span><button class="btn btn-sm btn-primary" onclick="_upRecoverShowAll=true;renderUpstreamView()">显示全部</button></div>`;
  }
  return h;
}

// recover 目录卡懒展开:点卡头才渲染该目录的 skill 行,避免几百 skill 全量进 DOM 卡死浏览器。
function toggleRecoverGroup(gid,headerEl){
  const body=document.getElementById('rcbody-'+gid);
  if(!body)return;
  const arrow=headerEl.querySelector('.rc-arrow');
  const on=body.style.display==='none';
  body.style.display=on?'block':'none';
  if(arrow)arrow.textContent=on?'▼':'▶';
  if(on&&!body.dataset.filled){
    body.dataset.filled='1';
    const skills=_rcGroupData[gid]||[];
    body.innerHTML=skills.map(s=>`<div style="display:flex;align-items:center;gap:6px;padding:5px 0;border-bottom:1px solid var(--border-subtle)">
      <span style="flex:1;min-width:0;font-size:12px;font-weight:500;color:var(--indigo);cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(s.name)}" onclick="showSkill('${esc(s.name)}','${esc(s.dir)}')">${escapeHtml(s.name)}</span>
      <button class="btn btn-sm" onclick="showSkill('${esc(s.name)}','${esc(s.dir)}',{autoExpandRecovery:true})" title="按 SKILL.md 内容搜回上游来源" style="font-size:9px;padding:2px 6px;color:var(--amber);border-color:var(--amber)">补来源</button>
    </div>`).join('');
  }
}

// 强制清空 upstream hash 缓存(内存 + 落盘),下次「开始上游检测」每个 skill 都走真实 GitHub API。
async function clearUpstreamCache(){
  if(!confirm('清空上游检测缓存?\n\n下次「开始上游检测」会对每个 skill 走真实 GitHub API(不受 24h 短路),消耗 API 额度。\n\n用于:怀疑缓存结果过期、或上游已更新但本地缓存还显示"已最新"时强制重查。'))return;
  try{
    const r=await fetch('/api/upstream-cache/clear',{method:'POST'}).then(r=>r.json());
    if(r.ok){toast('上游检测缓存已清空,下次检测走真实 API');}
    else{toast(r.error||'清缓存失败','error');}
  }catch(e){toast('清缓存失败: '+e.message,'error');}
}