/* ── Modal ── */
const ALL_CATS=['code-dev','content','image-gen','video-audio','data','web-search','social','doc','comms','design','translate','sysadmin','persona','finance','other'];
async function showSkill(name,dir,opts={}){
  _recoverCtx={name,dir:dir||'',candidates:[]};
  try{
    const url=dir?`/api/preview?full=1&dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(name)}`:`/api/skill/${name}/content`;
    const understandUrl=dir?`/api/understand?dir=${encodeURIComponent(dir)}&name=${encodeURIComponent(name)}`:`/api/understand?name=${encodeURIComponent(name)}`;
    const [d,u]=await Promise.all([
      fetch(url).then(r=>r.json()),
      fetch(understandUrl).then(r=>r.json()).catch(()=>null)
    ]);
    const sk=skills.find(s=>s.name===name);
    const cat=sk?.category||'other';
    const catSrc=sk?.categorySource||'';
    const catLabel=catSrc==='frontmatter'?'📋 frontmatter':catSrc==='user'?'🏷️ 用户覆盖':'🔤 关键词';
    // 判断 unknown skill:dir 推不出活跃能力桶(active-*) → 提示补来源。
    // 复用 sourceCapabilityBucket;steal-meta/.git/npx-lock 来源由后端识别,
    // 这里只看运行态能力分类是否未知。
    let isUnknown=false;
    if(dir&&typeof sourceCapabilityBucket==='function'&&typeof _dirTarget==='function'){
      const bucket=sourceCapabilityBucket(_dirTarget(dir));
      isUnknown=bucket==='unknown'||bucket==='review-copy';
    }
    // 来源信息:从扫描结果 health.source_status 查(detect_source_local 检测的三信号:
    // steal-meta/.git/vercel-lock)。没扫描时 health 无 source_status,提示扫描后显示。
    const SOURCE_LABELS={'steal-meta':'Steal安装','git-remote':'Git仓库','vercel-lock':'NPX安装','unknown':'未知(无来源留痕)'};
    let sourceInfo=null;
    try{
      if(typeof health!=='undefined'&&Array.isArray(health.source_status)){
        sourceInfo=health.source_status.find(s=>s.name===name&&(!dir||s.dir===dir))||null;
      }
    }catch(e){}
    const autoExpand=opts.autoExpandRecovery===true||(opts.autoExpandRecovery===undefined&&isUnknown);
    $('modal-title').textContent=name;
    $('modal-body').innerHTML=`<div style="font-family:-apple-system,sans-serif;color:var(--text);margin-bottom:12px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px;flex-wrap:wrap">
        <span style="font-size:12px;color:var(--text-muted)">分类: <strong style="color:var(--indigo)">${CAT_NAMES[cat]||cat}</strong></span>
        <span style="font-size:11px;color:var(--text-muted)">(${catLabel})</span>
        <select id="cat-edit" style="font-size:12px;padding:4px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);cursor:pointer"
          onchange="setCategory('${esc(name)}',this.value);$('modal').classList.add('hidden')">
          <option value="" ${!categoryOverrides[name]?'selected':''}>自动分类</option>
          ${ALL_CATS.map(c=>`<option value="${c}" ${categoryOverrides[name]===c?'selected':''}>${CAT_NAMES[c]||c}</option>`).join('')}
        </select>
      </div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:10px;padding:5px 10px;background:var(--bg-card-alt);border-radius:6px;border:1px solid var(--border-subtle)">
        <span>来源: </span>${sourceInfo?`<strong style="color:${sourceInfo.source==='unknown'?'var(--amber)':'var(--accent)'}">${SOURCE_LABELS[sourceInfo.source]||sourceInfo.source}</strong>${sourceInfo.repo?' · <span style="font-family:var(--mono);font-size:10px">'+escapeHtml(sourceInfo.repo)+'</span>':''}`:'<span style="color:var(--text-muted)">扫描后显示(点"开始整理"检测来源)</span>'}
      </div>
      ${renderUnderstandingPanel(u)}
      <pre style="white-space:pre-wrap;word-break:break-word;font-family:'SF Mono',monospace;font-size:11px;line-height:1.6;color:var(--text-dim);background:var(--bg-card-alt);padding:12px;border-radius:8px;border:1px solid var(--border);max-height:50vh;overflow-y:auto">${escapeHtml(d.preview||d.content||d.error||'(无内容)')}</pre>
      <div style="margin-top:10px;border-top:1px solid var(--border-subtle);padding-top:8px">
        <div onclick="toggleRecoveryPanel()" style="cursor:pointer;font-size:12px;color:var(--accent);display:flex;align-items:center;gap:4px;user-select:none">
          <span id="rec-arrow">▶</span> 补上游来源
          ${isUnknown?'<span style="color:var(--amber);font-size:10px;background:var(--bg-card-alt);padding:1px 6px;border-radius:999px;border:1px solid var(--border-subtle);margin-left:4px">unknown · 建议补来源</span>':'<span style="color:var(--text-muted);font-size:10px">(unknown skill 按内容搜回来源)</span>'}
        </div>
        <div id="rec-panel" style="display:none;margin-top:8px"></div>
      </div>
    </div>`;
    $('modal').classList.remove('hidden');
    // unknown skill(或外部主动调用)自动展开补来源面板,省一步点击
    if(autoExpand){
      // 双 rAF 确保 innerHTML 已 commit,再展开面板 + 滚到面板视野 + 自动搜索。
      // 用户点"补来源"就是想搜回上游,不该再手动滚+点搜索(2026-06-28 UX 反馈)。
      requestAnimationFrame(()=>requestAnimationFrame(()=>{
        const p=$('rec-panel');
        if(p&&p.style.display==='none')toggleRecoveryPanel();
        const panel=$('rec-panel');
        if(panel&&panel.scrollIntoView)panel.scrollIntoView({behavior:'smooth',block:'center'});
        if(typeof doSearchSource==='function')doSearchSource();
      }));
    }
  }
  catch(e){$('modal-title').textContent=name;$('modal-body').textContent='加载失败';$('modal').classList.remove('hidden')}
}
/* ── 补来源(unknown skill 按内容搜回上游) ── */
let _recoverCtx={name:'',dir:'',candidates:[]};
function toggleRecoveryPanel(){
  const p=$('rec-panel'),a=$('rec-arrow');
  if(p.style.display==='none'){
    p.style.display='block';a.textContent='▼';
    if(!p.dataset.inited){p.dataset.inited='1';renderRecoveryPanel(_recoverCtx.name,_recoverCtx.dir);}
  }else{p.style.display='none';a.textContent='▶';}
}
function renderRecoveryPanel(name,dir){
  _recoverCtx.name=name;_recoverCtx.dir=dir||'';
  // 主入口:按 skill 名字搜仓库(2026-06-28 主路线)。内容话术搜折叠成"高级"补充。
  $('rec-panel').innerHTML=`
    <div style="border:1px solid var(--amber);border-radius:8px;padding:10px;background:var(--bg-card-alt);margin-top:4px">
      <div style="font-size:12px;font-weight:600;color:var(--amber);margin-bottom:4px">◆ 补来源</div>
      <div style="font-size:11px;color:var(--text-muted);margin-bottom:8px;line-height:1.5">按 skill 名字搜 GitHub 仓库(优先你自己的)。搜不到可手动粘仓库地址。</div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <button class="btn btn-sm btn-primary" onclick="doSearchSource()">🔍 按名字搜来源</button>
        <span id="rec-status" style="font-size:11px;color:var(--text-muted)"></span>
      </div>
      <div id="rec-results"></div>
      <details style="margin-top:8px;border-top:1px dashed var(--border-subtle);padding-top:6px">
        <summary style="cursor:pointer;font-size:11px;color:var(--accent)">没搜到?手动粘贴仓库地址</summary>
        <div style="margin-top:6px;font-size:10px;color:var(--text-muted);line-height:1.4">支持仓库 / 子目录 / <b>SKILL.md 链接</b>(如 .../blob/main/SKILL.md),会 clone 解析列出 skills + hash 确认</div>
        <div style="margin-top:4px;display:flex;gap:6px;align-items:center">
          <input id="rec-manual-url" placeholder="仓库 URL / SKILL.md 链接 / owner-repo" style="flex:1;font-size:11px;font-family:var(--mono);padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--bg-card);color:var(--text)">
          <button class="btn btn-sm" onclick="doAttachManual()">记录</button>
        </div>
        <div id="rec-manual-status" style="margin-top:4px;font-size:11px"></div>
      </details>
      <details style="margin-top:6px">
        <summary style="cursor:pointer;font-size:11px;color:var(--text-muted)" onclick="ensureContentBox()">高级:按 SKILL.md 内容话术搜(慢,召回低)</summary>
        <div id="rec-content-box" style="margin-top:6px"></div>
      </details>
    </div>`;
}
function ensureContentBox(){
  const box=$('rec-content-box');
  if(!box||box.dataset.inited)return;
  box.dataset.inited='1';
  const sk=skills.find(s=>s.name===_recoverCtx.name);
  const pre=$('modal-body').querySelector('pre');
  const lines=[];
  const cleanRecover=l=>l.replace(/[*_`#>-]/g,' ').replace(/[（(][^)）]*[)）]/g,'').replace(/\s+/g,' ').trim();
  if(pre)(pre.textContent||'').split('\n').forEach(raw=>{const l=cleanRecover(raw);if(l&&l.length>=10&&l.length<=45&&!/^(name|description|allowed-?tools|license|---)/i.test(l))lines.push(l);});
  if(sk&&sk.description){const d=cleanRecover(sk.description).slice(0,45);if(d&&d.length>=10)lines.unshift(d);}
  box.innerHTML=`<textarea id="rec-snippets" rows="3" style="width:100%;font-size:11px;font-family:var(--mono);padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);box-sizing:border-box;resize:vertical">${escapeHtml([...new Set(lines)].slice(0,5).join('\n'))}</textarea><button class="btn btn-sm" style="margin-top:4px" onclick="doRecoverSearch()">内容搜</button>`;
}
async function doSearchSource(){
  const name=_recoverCtx.name;
  if(!name){$('rec-status').innerHTML='<span style="color:var(--amber)">无法定位 skill 名</span>';return;}
  $('rec-status').textContent='按名字搜索中...';
  $('rec-results').innerHTML='';
  try{
    const r=await fetch('/api/search-source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})}).then(r=>r.json());
    if(r.error){$('rec-status').innerHTML=`<span style="color:var(--amber)">${r.error==='code_search_requires_token'?'需在 .env 配 GITHUB_TOKEN':escapeHtml(r.error)}</span>`;return;}
    const cands=r.candidates||[];
    if(!cands.length){$('rec-status').innerHTML='<span style="color:var(--text-muted)">按名字没搜到。可手动粘仓库地址,或试"高级:内容搜"。</span>';return;}
    _recoverCtx.candidates=cands.map(c=>({...c,_byName:true}));
    $('rec-status').textContent=`命中 ${cands.length} 个仓库${r.login?' (优先 '+r.login+')':''}`;
    $('rec-results').innerHTML=cands.map((c,i)=>`<div style="padding:8px;border:1px solid var(--border-subtle);border-radius:6px;margin-bottom:6px;background:var(--bg-card-alt)${c.is_own?';border-color:var(--green)':''}">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
        <div style="flex:1;min-width:0"><div style="font-size:12px;font-weight:600">${escapeHtml(c.repo)} ${c.is_own?'<span style="color:var(--green);font-size:10px">自己仓库</span>':''}</div><div style="font-size:10px;color:var(--text-muted)">★${c.stars||0} · ${escapeHtml(c.description||'(无描述)')}</div></div>
        <button class="btn btn-sm btn-primary" onclick="doAttachSource(${i})">选为来源</button>
      </div></div>`).join('');
  }catch(e){$('rec-status').textContent='搜索失败: '+e.message;}
}
async function doRecoverSearch(){
  ensureContentBox();
  let dir=_recoverCtx.dir;
  if(!dir&&typeof scan!=='undefined'&&scan&&scan.target&&scan.target.path) dir=scan.target.path;
  if(!dir){$('rec-status').innerHTML='<span style="color:var(--amber)">无法定位 skill 目录</span>';return;}
  _recoverCtx.dir=dir;
  const nm=_recoverCtx.name;
  const skillDir=(nm&&dir.split('/').pop()===nm)?dir:(nm?dir.replace(/\/$/,'')+'/'+nm:dir);
  const snippets=($('rec-snippets').value||'').split('\n').map(s=>s.trim()).filter(Boolean);
  if(!snippets.length){$('rec-status').textContent='请输入独有话术';return;}
  $('rec-status').textContent='内容搜中(慢)...';
  $('rec-results').innerHTML='';
  try{
    const r=await fetch('/api/code-search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snippets,skill_dir:skillDir,confirm:true})}).then(r=>r.json());
    if(r.error){$('rec-status').innerHTML=`<span style="color:var(--amber)">${r.error==='code_search_requires_token'?'需配 GITHUB_TOKEN':escapeHtml(r.error)}</span>`;return;}
    const cands=r.candidates||[];
    if(!cands.length){$('rec-status').textContent='内容搜未召回候选';return;}
    _recoverCtx.candidates=cands.map(c=>({...c,_byName:false}));
    $('rec-status').textContent=`内容搜召回 ${cands.length} 个${r.rate_limited?' (部分限流)':''}`;
    $('rec-results').innerHTML=cands.map((c,i)=>{
      const m=c.match===true?'<span style="color:var(--green)">✓ 一致</span>':c.match===false?(c.confirm_error?`<span style="color:var(--amber)">⚠ ${escapeHtml(c.confirm_error)}</span>`:'<span style="color:var(--red)">✗ 不一致</span>'):'';
      return `<div style="padding:8px;border:1px solid var(--border-subtle);border-radius:6px;margin-bottom:6px;background:var(--bg-card-alt)">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="flex:1;min-width:0"><div style="font-size:12px;font-weight:600">${escapeHtml(c.repo)}</div><div style="font-size:10px;color:var(--text-muted)">${escapeHtml(c.path||'')} ${m}</div></div>
          <button class="btn btn-sm" onclick="doAttachSource(${i})">选为来源</button>
        </div></div>`;
    }).join('');
  }catch(e){$('rec-status').textContent='搜索失败: '+e.message;}
}
function _recoverMarkSolved(){
  // attach 成功后乐观更新:① 从待补来源(unknown)移除 ② 加到上游(status unknown 待扫描确认版本)。
  // 不然待补来源还显示 + 上游看不到(health 是扫描缓存,attach 不触发重扫)。不烧 API(status 用 unknown,扫描后更新)。
  if(typeof health==='undefined')return;
  const nm=_recoverCtx.name, dir=_recoverCtx.dir||'';
  const skillDir=(nm&&dir.split('/').pop()===nm)?dir:(nm?dir.replace(/\/$/,'')+'/'+nm:dir);
  const repo=_recoverCtx._lastRepo||'';
  if(Array.isArray(health.source_status)){
    health.source_status=health.source_status.filter(s=>s.name!==nm);
  }
  if(Array.isArray(health.upstream_sources)&&repo){
    health.upstream_sources=health.upstream_sources.filter(s=>s.name!==nm);
    health.upstream_sources.push({name:nm,dir:skillDir,repo,status:'unknown',source:'steal-meta',canonical_dir:skillDir});
  }
  if(typeof renderUpstreamView==='function') renderUpstreamView();
  if(typeof renderIssues==='function') renderIssues();
}
async function doAttachSource(i){
  const c=_recoverCtx.candidates&&_recoverCtx.candidates[i];
  if(!c) return;
  // 按名字搜候选(_byName):仓库无 path,subdir=''。内容搜候选:有 path 推 subdir。
  let subdir='';
  if(!c._byName&&c.path){const p=c.path.replace(/\/SKILL\.md$/i,'');subdir=p.startsWith('skills/')?p.slice(7):p;}
  const nm=_recoverCtx.name,d=_recoverCtx.dir||'';
  const skillDir=(nm&&d.split('/').pop()===nm)?d:(nm?d.replace(/\/$/,'')+'/'+nm:d);
  $('rec-status').textContent='记录来源中...';
  try{
    const r=await fetch('/api/attach-source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_dir:skillDir,repo:c.repo,subdir,ref:'main',url:c.url||c.html_url||''})}).then(r=>r.json());
    if(r.ok){toast('已记录来源: '+c.repo);$('rec-status').innerHTML=`<span style="color:var(--green)">✓ 已记录来源: ${escapeHtml(c.repo)}。下次二哥扫描(勾上游)可追溯版本。</span>`;$('rec-results').innerHTML='';_recoverCtx._lastRepo=c.repo;_recoverMarkSolved();}
    else{$('rec-status').innerHTML=`<span style="color:var(--red)">${escapeHtml(r.error||'记录失败')}</span>`;}
  }catch(e){$('rec-status').textContent='记录失败: '+e.message;}
}
async function doAttachManual(){
  const ms=$('rec-manual-status')||$('rec-status');  // 就近显示在手动 URL 区下方,不跳跃到上面
  const raw=($('rec-manual-url').value||'').trim();
  if(!raw){ms.innerHTML='<span style="color:var(--amber)">请粘贴仓库地址</span>';return;}
  const url=raw.startsWith('http')?raw:('https://github.com/'+raw.replace(/^github\.com\//,''));
  const nm=_recoverCtx.name,d=_recoverCtx.dir||'';
  const skillDir=(nm&&d.split('/').pop()===nm)?d:(nm?d.replace(/\/$/,'')+'/'+nm:d);
  ms.textContent='解析仓库确认中(clone 几秒)...';
  try{
    // 借用 install_skill 解析层:clone + 列 skills + hash 比对本地(2026-06-28)
    const p=await fetch('/api/probe-source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({url,skill_dir:skillDir})}).then(r=>r.json());
    if(!p.ok){ms.innerHTML=`<span style="color:var(--red)">解析失败: ${escapeHtml(p.error||'')}</span>`;return;}
    const skills=p.skills||[];
    let pick=skills.find(s=>s.match)||skills.find(s=>s.name===nm)||skills[0];
    if(!pick){ms.innerHTML='<span style="color:var(--amber)">仓库里没找到 SKILL.md</span>';return;}
    const r=await fetch('/api/attach-source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_dir:skillDir,repo:p.repo,subdir:pick.subdir,ref:'main',url})}).then(r=>r.json());
    if(r.ok){toast(pick.match?('✓ 来源确认: '+p.repo):('⚠ 已记录: '+p.repo+' (内容不一致)'));ms.innerHTML=pick.match?`<span style="color:var(--green)">✓ 来源确认: ${escapeHtml(p.repo)} (内容一致,subdir=${pick.subdir||'根'})</span>`:`<span style="color:var(--amber)">⚠ 已记 ${escapeHtml(p.repo)} (${escapeHtml(pick.name)} 内容不一致,subdir=${pick.subdir||'根'})</span>`;$('rec-results').innerHTML='';_recoverCtx._lastRepo=p.repo;_recoverMarkSolved();}
    else{ms.innerHTML=`<span style="color:var(--red)">${escapeHtml(r.error||'记录失败')}</span>`;}
  }catch(e){ms.textContent='失败: '+e.message;}
}
function firstMeaningfulLine(text){
  return (text||'').split('\n').map(l=>l.trim()).find(l=>l&&l.length>8&&!l.startsWith('```'))||'';
}
function renderCompareSummary(results){
  if(!results.length)return '';
  const lengths=results.map(r=>(r.content||'').length);
  const uniqueBodies=new Set(results.map(r=>r.content||''));
  const minLen=Math.min(...lengths),maxLen=Math.max(...lengths);
  const firstLines=[...new Set(results.map(r=>firstMeaningfulLine(r.content)).filter(Boolean))].slice(0,3);
  const sentence=uniqueBodies.size===1
    ? '内容判断：这些 SKILL.md 正文完全相同，主要差异在目录来源。'
    : `内容判断：正文不完全相同，长度约 ${minLen}-${maxLen} 字符；需要看下方并排原文确认差异。`;
  return `<div style="font-size:11px;color:var(--text-muted);line-height:1.6;background:var(--bg-card-alt);border:1px solid var(--border-subtle);border-radius:6px;padding:8px;margin-top:8px">
    ${escapeHtml(sentence)}
    ${firstLines.length?`<br>首段/标题线索：${firstLines.map(escapeHtml).join(' ｜ ')}`:''}
  </div>`;
}
async function compareSkills(btn,dataKey){
  const container=btn.closest('[style*="border:1px"]')||btn.parentElement.parentElement;
  // Toggle: if already showing compare, collapse
  const existing=container.querySelector('.compare-panel');
  if(existing){existing.remove();btn.textContent='并排对比';return;}
  const locs=_compareData[dataKey];
  if(!locs||!locs.length)return;
  btn.textContent='加载中...';btn.disabled=true;
  try{
    const results=await Promise.all(locs.map(async l=>{
      try{
        const r=await fetch(`/api/preview?full=1&dir=${encodeURIComponent(l.dir)}&name=${encodeURIComponent(l.name)}`);
        const d=await r.json();
        return{dir:l.dir,name:l.name,content:d.preview||d.content||'(无内容)',ok:true};
      }catch(e){return{dir:l.dir,name:l.name,content:'加载失败',ok:false};}
    }));
    let html=`<div class="compare-panel">
    ${renderCompareSummary(results)}
    <div style="margin-top:8px;display:flex;gap:8px;overflow-x:auto">
      ${results.map(r=>`<div style="flex:1;min-width:280px;border:1px solid var(--border-subtle);border-radius:6px;overflow:hidden">
        <div style="padding:6px 10px;background:var(--bg-card-alt);font-size:10px;color:var(--text-muted);border-bottom:1px solid var(--border-subtle)">
          <div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis" title="${esc(r.dir)}">${escapeHtml(r.dir)}</div>
          <button class="btn btn-sm" onclick="copyPath('${esc(r.dir)}')" style="font-size:9px;padding:1px 5px;margin-top:4px">复制路径</button>
        </div>
        <pre style="white-space:pre-wrap;word-break:break-word;font-family:'SF Mono',monospace;font-size:10px;line-height:1.5;color:var(--text-dim);padding:8px;max-height:300px;overflow-y:auto;margin:0">${escapeHtml(r.content)}</pre>
      </div>`).join('')}
      </div>
    </div>`;
    // Insert after the skills list (the second child div)
    const listDiv=container.querySelector('div[style*="display:none"]')||container.children[1];
    if(listDiv){listDiv.style.display='block';listDiv.insertAdjacentHTML('afterend',html);}
    else{container.insertAdjacentHTML('beforeend',html);}
    btn.textContent='收起对比';
  }catch(e){btn.textContent='并排对比';}
  finally{btn.disabled=false;}
}
function toggleIssueSelect(cb){
  const key=cb.dataset.skey;
  if(cb.checked)_issueSelected.add(key);else _issueSelected.delete(key);
}
async function deleteSelectedIssues(){
  if(!_issueSelected.size){toast('请先勾选要删除的 skill','error');return;}
  const checks=document.querySelectorAll('.issue-check:checked');
  if(!checks.length){toast('请先勾选要删除的 skill','error');return;}
  const names=[...new Set([...checks].map(c=>c.dataset.sname))];
  const changedDirs=[...new Set([...checks].map(c=>c.dataset.sdir).filter(Boolean))];
  if(!confirm(`确认删除选中的 ${checks.length} 个 skill？\n\n${names.join(', ')}`))return;
  let ok=0,fail=0;
  for(const cb of checks){
    const name=cb.dataset.sname;
    const dir=cb.dataset.sdir;
    const reason=cb.dataset.sreason||'';
    const url=dir?`/api/skill/${name}?target=${encodeURIComponent(dir)}${reason?`&reason=${reason}`:''}`:`/api/skill/${name}${reason?`?reason=${reason}`:''}`;
    try{const r=await fetch(url,{method:'DELETE'});const d=await r.json();d.ok?ok++:fail++;}
    catch{fail++;}
  }
  _issueSelected.clear();
  toast(`已删除 ${ok} 个${fail>0?`，${fail} 个失败`:''}`);
  if(typeof refreshIssuesAfterDelete==='function'&&document.querySelector('#view-issues')?.style.display!=='none'){
    await refreshIssuesAfterDelete(changedDirs);
  }else{
    invalidateTargetsCache();
    clearGlobalSearchCache();
    await loadData();
  }
}
function escapeHtml(t){return t.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
// Escape for use inside HTML attributes and JS string literals
function esc(s){return String(s).replace(/\\/g,'\\\\').replace(/`/g,'\\`').replace(/'/g,"\\'").replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
// Safe description for template literals: strip backticks and ${} that break JS
function safeDesc(d){return String(d||'').replace(/`/g,"'").replace(/\$\{/g,'{')}
