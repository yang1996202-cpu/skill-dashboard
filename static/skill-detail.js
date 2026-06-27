/* ── Modal ── */
const ALL_CATS=['code-dev','content','image-gen','video-audio','data','web-search','social','doc','comms','design','translate','sysadmin','persona','finance','other'];
async function showSkill(name,dir){
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
      ${renderUnderstandingPanel(u)}
      <pre style="white-space:pre-wrap;word-break:break-word;font-family:'SF Mono',monospace;font-size:11px;line-height:1.6;color:var(--text-dim);background:var(--bg-card-alt);padding:12px;border-radius:8px;border:1px solid var(--border);max-height:50vh;overflow-y:auto">${escapeHtml(d.preview||d.content||d.error||'(无内容)')}</pre>
      <div style="margin-top:10px;border-top:1px solid var(--border-subtle);padding-top:8px">
        <div onclick="toggleRecoveryPanel()" style="cursor:pointer;font-size:12px;color:var(--accent);display:flex;align-items:center;gap:4px;user-select:none">
          <span id="rec-arrow">▶</span> 补上游来源 <span style="color:var(--text-muted);font-size:10px">(unknown skill 按内容搜回来源)</span>
        </div>
        <div id="rec-panel" style="display:none;margin-top:8px"></div>
      </div>
    </div>`;
    $('modal').classList.remove('hidden')
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
  const sk=skills.find(s=>s.name===name);
  const pre=$('modal-body').querySelector('pre');
  const lines=[];
  if(sk&&sk.description) lines.push(sk.description);
  if(pre){
    (pre.textContent||'').split('\n').map(l=>l.trim()).forEach(l=>{
      if(l&&!l.startsWith('---')&&!l.startsWith('#')&&!l.startsWith('```')&&l.length>8) lines.push(l);
    });
  }
  const prefilled=[...new Set(lines)].slice(0,5).join('\n');
  $('rec-panel').innerHTML=`
    <div style="font-size:11px;color:var(--text-muted);margin-bottom:6px;line-height:1.5">从 SKILL.md 挑独有内容话术(描述/标题/罕见句),一行一条。<b>按名字搜会撞同类,按独有内容搜才准</b>。</div>
    <textarea id="rec-snippets" rows="4" style="width:100%;font-size:11px;font-family:var(--mono);padding:6px;border-radius:6px;border:1px solid var(--border);background:var(--bg-card);color:var(--text);box-sizing:border-box;resize:vertical">${escapeHtml(prefilled)}</textarea>
    <div style="margin-top:6px;display:flex;gap:8px;align-items:center">
      <button class="btn btn-sm btn-primary" onclick="doRecoverSearch()">🔍 搜索来源</button>
      <span id="rec-status" style="font-size:11px;color:var(--text-muted)"></span>
    </div>
    <div id="rec-results" style="margin-top:8px"></div>`;
}
async function doRecoverSearch(){
  let dir=_recoverCtx.dir;
  if(!dir&&typeof scan!=='undefined'&&scan&&scan.target&&scan.target.path) dir=scan.target.path;
  if(!dir){$('rec-status').innerHTML='<span style="color:var(--amber)">无法定位 skill 目录</span>';return;}
  _recoverCtx.dir=dir;
  const snippets=($('rec-snippets').value||'').split('\n').map(s=>s.trim()).filter(Boolean);
  if(!snippets.length){$('rec-status').textContent='请输入独有话术';return;}
  $('rec-status').textContent='搜索中(调 GitHub Code Search)...';
  $('rec-results').innerHTML='';
  try{
    const r=await fetch('/api/code-search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({snippets,skill_dir:dir,confirm:true})}).then(r=>r.json());
    if(r.error){$('rec-status').innerHTML=`<span style="color:var(--amber)">${r.error==='code_search_requires_token'?'需在项目根 .env 配 GITHUB_TOKEN 才能搜索':escapeHtml(r.error)}</span>`;return;}
    const cands=r.candidates||[];
    if(!cands.length){$('rec-status').textContent='未召回候选,换几个独有话术再试';return;}
    $('rec-status').textContent=`召回 ${cands.length} 个候选${r.rate_limited?' (部分限流)':''}`;
    _recoverCtx.candidates=cands;
    $('rec-results').innerHTML=cands.map((c,i)=>{
      const m=c.match===true?'<span style="color:var(--green)">✓ 内容一致</span>':c.match===false?'<span style="color:var(--red)">✗ 不一致</span>':'<span style="color:var(--text-muted)">未确认</span>';
      return `<div style="padding:8px;border:1px solid var(--border-subtle);border-radius:6px;margin-bottom:6px;background:var(--bg-card-alt)${c.match===true?';border-color:var(--green)':''}">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <div style="flex:1;min-width:0"><div style="font-size:12px;font-weight:600">${escapeHtml(c.repo)}</div><div style="font-size:10px;color:var(--text-muted)">${escapeHtml(c.path||'')} · 命中 ${c.hit_count||0} 片段</div></div>
          <div style="font-size:11px">${m}</div>
        </div>${c.match===true?`<button class="btn btn-sm btn-primary" style="margin-top:6px" onclick="doAttachSource(${i})">选为来源</button>`:''}
      </div>`;
    }).join('');
  }catch(e){$('rec-status').textContent='搜索失败: '+e.message;}
}
async function doAttachSource(i){
  const c=_recoverCtx.candidates&&_recoverCtx.candidates[i];
  if(!c||c.match!==true) return;
  let subdir='';
  if(c.path){const p=c.path.replace(/\/SKILL\.md$/i,'');subdir=p.startsWith('skills/')?p.slice(7):p;}
  $('rec-status').textContent='记录来源中...';
  try{
    const r=await fetch('/api/attach-source',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({skill_dir:_recoverCtx.dir,repo:c.repo,subdir,ref:'main',url:c.html_url||''})}).then(r=>r.json());
    if(r.ok){$('rec-status').innerHTML=`<span style="color:var(--green)">✓ 已记录来源: ${escapeHtml(c.repo)}。下次二哥扫描(勾上游)可追溯版本。</span>`;$('rec-results').innerHTML='';}
    else{$('rec-status').innerHTML=`<span style="color:var(--red)">${escapeHtml(r.error||'记录失败')}</span>`;}
  }catch(e){$('rec-status').textContent='记录失败: '+e.message;}
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
    const url=dir?`/api/skill/${name}?target=${encodeURIComponent(dir)}`:`/api/skill/${name}`;
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
