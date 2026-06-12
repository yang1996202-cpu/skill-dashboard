/* ── Modal ── */
const ALL_CATS=['code-dev','content','image-gen','video-audio','data','web-search','social','doc','comms','design','translate','sysadmin','persona','finance','other'];
async function showSkill(name,dir){
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
    </div>`;
    $('modal').classList.remove('hidden')
  }
  catch(e){$('modal-title').textContent=name;$('modal-body').textContent='加载失败';$('modal').classList.remove('hidden')}
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
  await loadData();
}
function escapeHtml(t){return t.replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]))}
// Escape for use inside HTML attributes and JS string literals
function esc(s){return String(s).replace(/\\/g,'\\\\').replace(/`/g,'\\`').replace(/'/g,"\\'").replace(/"/g,'&quot;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
// Safe description for template literals: strip backticks and ${} that break JS
function safeDesc(d){return String(d||'').replace(/`/g,"'").replace(/\$\{/g,'{')}
