'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const state = { platform:null, directions:[], workspace:null, jobs:[], selected:null, detail:null, timer:null, mutation:false, dirty:false, profileDirty:false, feedbackDirty:false, providerDirty:false, assignmentsDirty:false, transition:false, contentDirty:false, reviewDirty:false, detailSeq:0, scope:0, editingProvider:null, excluded:new Set(), offset:0, all:false, route:'#home', jobsSeq:0, generating:new Set() };
  const statuses = { running:'正在生成', materials_ready:'资料已采集 · 非成稿', needs_ai:'等待AI · 非成稿', review_required:'待审核', approved:'已通过', rejected:'已退回', failed:'生成失败', published:'已发布' };
  const names = { home:'平台首页', explore:'找方向', workspace:'我的空间', overview:'稿件', providers:'服务商', config:'设置', detail:'内容与发布' };
  const domainNames = {finance:'财经',work:'职场',career:'职场',workplace:'职场',technology:'科技',tech:'科技',life:'生活',lifestyle:'生活',culture:'书影音',learning:'学习'};
  const contentFields = ['wechat_title','wechat_markdown','xhs_title','xhs_text'];
  const profileFields = ['brand','goal','audience','tone','stock_list','schedule_time','weekly_day','schedule_enabled','materials'];
  const configFields = ['dsa_report_dir','dsa_run_enabled','dsa_repo','dsa_python','dsa_timeout'];
  function el(tag,className,text) { const n=document.createElement(tag); if(className)n.className=className; if(text!==undefined)n.textContent=textValue(text); return n; }
  function textValue(v,f='') { return v==null?f:typeof v==='object'?JSON.stringify(v):String(v); }
  function showNotice(t,error=false) { $('notice').textContent=t; $('notice').className='notice'+(error?' error':''); $('notice').hidden=false; }
  function formatDate(v) { if(!v)return '时间未知'; const d=new Date(v); return Number.isNaN(d.getTime())?String(v):d.toLocaleString('zh-CN',{hour12:false}); }
  function safeURL(value,image=false) { if(typeof value!=='string'||!value.trim()||(!image&&!/^https?:\/\//i.test(value.trim())))return null; try { const u=new URL(value.trim(),location.origin); if(!['http:','https:'].includes(u.protocol)||u.username||u.password)return null; if(image&&u.origin!==location.origin)return null; return u.href; }catch{return null;} }
  function finance(w=state.workspace) { return w&&w.domain==='finance'; }
  function owner() { return state.workspace&&state.workspace.id; }
  function scoped(path,id=owner()) { return path+(path.includes('?')?'&':'?')+'workspace_id='+encodeURIComponent(id||''); }
  function owned(id,token) { return owner()===id&&state.scope===token; }
  async function api(path,options={}) { const controller=new AbortController(); const timer=setTimeout(()=>controller.abort(),30000); let response,raw; try { response=await fetch(path,{...options,signal:controller.signal,headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}}); raw=await response.text(); }catch(e){throw new Error(e.name==='AbortError'?'请求超时，请先刷新确认结果再重试。':'无法连接本地服务，请检查服务状态。');}finally{clearTimeout(timer);} let data=null; if(raw){try{data=JSON.parse(raw);}catch{if(response.ok)throw new Error('服务返回的数据格式无法识别。');}} if(!response.ok)throw new Error(textValue(data&&(data.error||data.message),`请求失败（HTTP ${response.status}）`)); return data; }
  const json = (method,body={}) => ({method,body:JSON.stringify(body)});
  function badge(s) { return el('span','badge '+(Object.hasOwn(statuses,s)?s:''),statuses[s]||s||'未知状态'); }
  function action(text,fn,cls='secondary') { const b=el('button',cls,text); b.type='button'; b.addEventListener('click',fn); return b; }
  function hasEdits() { return state.contentDirty||state.reviewDirty||state.dirty||state.profileDirty||state.feedbackDirty||state.providerDirty||state.assignmentsDirty; }
  function leaveGuard() { if(state.mutation||state.transition){showNotice('操作进行中，请稍候再切换。',true);return false;} if(!hasEdits())return true; return confirm('有未保存的改稿、审核意见或设置。离开会丢失这些修改，确认放弃吗？'); }
  function clearEdits() { state.contentDirty=false;state.reviewDirty=false;state.dirty=false;state.profileDirty=false;state.feedbackDirty=false;state.providerDirty=false;state.assignmentsDirty=false; }
  function route() {
    const hash=location.hash||'#home'; const [raw,id]=hash.slice(1).split('/'); const page=names[raw]?raw:'home';
    if(hash!==state.route&&(hasEdits()||state.mutation||state.transition)&&!leaveGuard()){history.replaceState(null,'',state.route);return;}
    if(hash!==state.route&&hasEdits()){clearEdits();renderWorkspace();renderProviders();editProvider(null);if(state.detail)renderDetail(state.detail);if(state.config)fillConfig(state.config);}
    state.route=hash;
    Object.keys(names).forEach(k=>$('page-'+k).hidden=k!==page);
    document.querySelectorAll('[data-page]').forEach(a=>{const active=a.dataset.page===(page==='detail'?'overview':page);a.classList.toggle('active',active);if(active)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});
    $('page-name').textContent=names[page]; updateContext(page);
    if(page==='config'&&finance()&&!state.dirty)loadConfig();
    if(page==='detail'&&id){let decoded;try{decoded=decodeURIComponent(id);}catch{showNotice('无效任务链接',true);return;}if(!/^[A-Za-z0-9_-]+$/.test(decoded)){showNotice('无效任务链接',true);return;}if(decoded!==state.selected){state.selected=decoded;state.detail=null;}loadDetail(true);}
  }
  function updateContext(page) { const w=state.workspace; const platform=['home','explore','providers'].includes(page); $('workspace-context').textContent=platform?'创作罗盘（暂名）· 平台':w?w.name+' · '+(domainNames[w.domain]||w.domain):'尚未选择工作空间'; }
  function upsertWorkspace(w) { if(!w||!w.id)throw new Error('服务返回的工作空间格式不正确。'); const list=state.platform.workspaces;const i=list.findIndex(x=>x.id===w.id);if(i<0)list.push(w);else list[i]=w;return w; }
  async function enterWorkspace(id) {
    if(!leaveGuard())return;state.transition=true;const token=++state.scope;
    try{const w=await api('/api/workspaces/'+encodeURIComponent(id)+'/activate',json('POST'));if(token!==state.scope)return;clearEdits();state.workspace=upsertWorkspace(w);state.platform.active_workspace_id=w.id;resetScope();renderPlatform();renderWorkspace();state.transition=false;location.hash='workspace';updateContext('workspace');loadJobs();}
    catch(e){if(token===state.scope)showNotice('切换空间失败：'+e.message,true);}
    finally{state.transition=false;}
  }
  function resetScope() { state.jobs=[];state.selected=null;state.detail=null;state.detailSeq++;state.jobsSeq++;$('detail-content').replaceChildren(el('div','empty panel','请从当前空间稿件中选择任务。'));renderJobs(); }
  function renderPlatform() { const root=$('home-spaces');root.replaceChildren(); const spaces=state.platform.workspaces||[];for(const [heading,items]of [['个人已选 · 财经工作空间',spaces.filter(finance)],['我的其他工作空间',spaces.filter(w=>!finance(w))]]){if(!items.length)continue;const panel=el('section','panel');panel.append(el('h2','',heading));const grid=el('div','direction-grid');items.forEach(w=>{const card=el('article','space-card');card.append(el('div','eyebrow',domainNames[w.domain]||w.domain),el('h3','',w.name),el('p','',w.profile&&w.profile.goal||'独立保存偏好、稿件与反馈'),action('进入'+w.name+' →',()=>enterWorkspace(w.id),'primary'));grid.append(card);});panel.append(grid);root.append(panel);}if(!spaces.length)root.append(el('div','info-banner','还没有工作空间。先浏览精选方向，选择后才创建；不会默认给你设置财经方向。'));renderDirections(); }
  function directionCard(d) { const card=el('article','direction-card');card.append(el('div','eyebrow',domainNames[d.domain]||d.domain),el('h2','',d.name),el('p','',d.reader),el('p','',d.work)); const p=d.preview||{};card.append(el('div','sample-label','预置模拟样例 · 非真实AI输出'),el('h3','',p.title||'查看完整样稿'),el('p','sample-excerpt',textValue(p.body).slice(0,160)));if(Array.isArray(d.topics)&&d.topics.length>1)card.append(el('p','help','更多样稿：'+d.topics.slice(1).map(t=>textValue(t.title)).join(' / ')));const buttons=el('div','actions');buttons.append(action('先看完整样稿',()=>previewDirection(d),'ghost'),action('选这个，创建空间',()=>createWorkspace(d),'primary'),action('不感兴趣',()=>{state.excluded.add(d.id);state.offset=0;renderDirections();},'ghost'));card.append(buttons);return card; }
  function renderDirections() { $('home-directions').replaceChildren(...state.directions.slice(0,3).map(directionCard)); const f=$('domain-filter').value;const dirs=state.directions.filter(d=>(!f||d.domain===f)&&!state.excluded.has(d.id));if(state.offset>=dirs.length)state.offset=0;const shown=state.all?dirs:dirs.slice(state.offset,state.offset+3);$('directions').replaceChildren(...shown.map(directionCard));if(!shown.length)$('directions').append(el('div','panel empty','暂无符合条件的方向。可切换领域或点击「恢复排除」。')); }
  function previewDirection(d) {
    const root=$('sample-content');root.replaceChildren(el('p','sample-label','预置模拟样例，只供方向预览。不能批准、发布或当作真实任务。'));
    const samples=[];if(d.preview)samples.push(d.preview);
    (Array.isArray(d.topics)?d.topics:[]).forEach(t=>{if(t.preview&&!samples.some(p=>p.title===t.preview.title))samples.push(t.preview);});
    samples.forEach((p,index)=>{const section=el('section','sample-section');section.append(el('div','eyebrow','SAMPLE '+String(index+1).padStart(2,'0')),el('h2','',p.title),el('pre','sample-body',p.body),el('h3','','小红书独立文案'),el('h4','',p.xhs_title),el('pre','sample-body',p.xhs_text));const cards=el('div','sample-cards');(Array.isArray(p.cards)?p.cards:[]).forEach((c,i)=>{const card=el('article','sample-card');card.append(el('small','',String(i+1).padStart(2,'0')),el('h3','',c.title),el('p','preserve-lines',c.body));cards.append(card);});section.append(cards);root.append(section);});
    if(!samples.length)root.append(el('p','help','服务端未提供样稿，请稍后重试。'));
    root.append(el('p','help','领域边界：'+textValue(d.rules)));$('sample-dialog').showModal();
  }
  async function createWorkspace(d) {
    if(!leaveGuard())return;state.transition=true;const token=++state.scope;let created=null;
    try{created=upsertWorkspace(await api('/api/workspaces',json('POST',{direction_id:d.id})));const w=await api('/api/workspaces/'+encodeURIComponent(created.id)+'/activate',json('POST'));if(token!==state.scope)return;clearEdits();state.workspace=upsertWorkspace(w);state.platform.active_workspace_id=w.id;resetScope();renderPlatform();renderWorkspace();state.transition=false;location.hash='workspace';showNotice('空间与方向卡已保存。尚未生成任务；请确认后明确点击生产。');loadJobs();}
    catch(e){if(token===state.scope){if(created)renderPlatform();showNotice((created?'空间已创建，但激活失败，可从首页重新进入：':'创建空间失败：')+e.message,true);}}
    finally{state.transition=false;}
  }
  function currentDirection() { return state.directions.find(d=>d.id===(state.workspace&&state.workspace.direction_id))||{}; }
  function renderWorkspace() { const w=state.workspace;$('workspace-empty').hidden=!!w;$('workspace-body').hidden=!w;$('jobs-heading').textContent=w?w.name+' · 稿件':'请先选择工作空间';document.querySelectorAll('.generate').forEach(b=>b.disabled=!w||state.generating.has(w.id));$('config-fields').disabled=!finance();$('save-config').disabled=!finance();if(!finance())$('config-state').textContent='仅财经空间可使用高级DSA配置';if(!w)return;const p=w.profile||{},d=currentDirection();$('workspace-title').textContent=w.name;$('workspace-domain').textContent=domainNames[w.domain]||w.domain;const grid=$('direction-summary');grid.replaceChildren();for(const [title,value]of [['服务谁',p.audience||d.reader],['做什么',p.goal||d.work],['素材范围',p.materials||d.materials],['表达风格',p.tone||'按所选方向'],['领域规则',d.rules],['成本边界',d.money||'模型按外部服务商实际用量收费，无收益保证']]){const card=el('article');card.append(el('h3','',title),el('p','',textValue(value,'未提供')));grid.append(card);}const f=$('profile-form');if(!state.profileDirty){f.elements.name.value=w.name;profileFields.forEach(k=>{const n=f.elements[k];if(n.type==='checkbox')n.checked=!!p[k];else n.value=p[k]==null?(k==='weekly_day'?'4':k==='schedule_time'?'18:00':''):Array.isArray(p[k])?p[k].join(','):textValue(p[k]);});$('profile-state').textContent='已加载本空间偏好';}$('stock-field').hidden=!finance();const feedback=$('feedback-form');if(!state.feedbackDirty){feedback.reset();const recent=Array.isArray(w.feedback)?w.feedback[w.feedback.length-1]:w.feedback||{};if(recent)for(const k of ['published','difficulty','question'])feedback.elements[k].value=recent[k]||(k==='question'?'':'unknown');}const history=$('feedback-history');history.replaceChildren();(Array.isArray(w.feedback_records)?w.feedback_records:Array.isArray(w.feedback)?w.feedback:w.feedback&&Object.keys(w.feedback).length?[w.feedback]:[]).slice(-5).reverse().forEach(x=>history.append(el('p','feedback-entry',formatDate(x.created_at)+' · 发布：'+({unknown:'未填',yes:'已发布',no:'未发布'}[x.published]||'未填')+' · 难度：'+({unknown:'未填',easy:'容易',hard:'有困难'}[x.difficulty]||'未填')+' · '+textValue(x.question))));renderMode();updateContext((location.hash.slice(1).split('/')[0])||'home'); }
  function assignedAI() { const a=state.platform&&state.platform.assignments||{};const ps=state.platform&&state.platform.providers||[];return ['topic_provider_id','writing_provider_id'].every(k=>ps.some(p=>p.id===a[k]&&p.has_api_key)); }
  function renderMode() { const ai=assignedAI();$('mode-title').textContent=ai?'Key已配置，连通性与实际生成仍以服务商响应为准':'尚未配置完整选题 / 写作Key';$('mode-copy').textContent=ai?'点击生成会发起真实AI请求，可能产生费用；保存不代表连通，也不保证文章质量。':finance()?'当前可仅采集财经资料，资料包不可批准或发布。生成成稿请先连接服务商并保存默认任务分配。':'本领域需要配置选题与写作服务商及Key后才能生成，不会用预置样稿替代真实生产。'; }
  async function refreshPlatform() { const p=await api('/api/platform');if(!p||!Array.isArray(p.workspaces)||!Array.isArray(p.directions)||!Array.isArray(p.providers))throw new Error('平台数据格式不正确');state.platform=p;state.directions=p.directions;return p; }
  function externalLink(label,url,root) { const safe=safeURL(url);if(!safe)return;const a=el('a','button ghost',label);a.href=safe;a.target='_blank';a.rel='noopener noreferrer';root.append(a); }
  function renderProviders() { if(!state.platform)return;const root=$('providers-list');root.replaceChildren();state.platform.providers.forEach(p=>{const card=el('article','panel provider-card');card.append(el('h2','',p.name),el('p','',p.model+' · '+p.api_base),el('p','help',p.has_api_key?'已保存Key（不回显）· 未承诺连通':'未配置Key'));const a=el('div','actions');a.append(action('编辑',()=>editProvider(p),'secondary'),action('测试连接 · 可能收费',async event=>{const button=event.currentTarget;if(button.disabled||!confirm('将向此服务商发起1次最小真实模型请求，可能产生费用。继续吗？'))return;button.disabled=true;try{const r=await api('/api/providers/'+encodeURIComponent(p.id)+'/test',json('POST'));const ok=typeof r.ok==='boolean'?r.ok:r.connected===true;showNotice((ok?'连通测试成功：':'连通测试失败：')+textValue(r.message||r.error,ok?'最小请求已完成':'服务商未提供错误详情')+(r.status?' · '+textValue(r.status):''),!ok);}catch(e){showNotice('测试失败：'+e.message,true);}finally{button.disabled=false;}},'ghost'),action('删除',async()=>{if(!confirm('删除此连接？相关任务默认分配可能需要重新设置。'))return;try{await api('/api/providers/'+encodeURIComponent(p.id),json('DELETE'));await refreshPlatform();renderProviders();renderMode();showNotice('连接已删除。');}catch(e){showNotice('删除失败：'+e.message,true);}},'danger'));const preset=(state.platform.provider_presets||[]).find(x=>x.id===p.preset);if(preset)externalLink('服务商官网 · 自行注册充值',preset.official_url||preset.website||preset.url,a);externalLink('推广链接（合作推广）',p.promotion_url,a);card.append(a);root.append(card);});if(!root.childNodes.length)root.append(el('div','panel empty','还没有服务商连接。请填写下方配置；不展示虚构余额、价格或模型可用状态。'));const preset=$('provider-preset');const selected=preset.value||'custom';preset.replaceChildren(new Option('自定义兼容接口','custom'));(state.platform.provider_presets||[]).filter(p=>p.id!=='custom').forEach(p=>preset.add(new Option(p.name||p.id,p.id)));preset.value=selected;if(state.assignmentsDirty)return;const form=$('assignments-form');for(const key of ['topic_provider_id','writing_provider_id']){const s=form.elements[key];s.replaceChildren(new Option('未分配',''));state.platform.providers.forEach(p=>s.add(new Option(p.name+(p.has_api_key?' · 已存Key':' · 无Key'),p.id)));s.value=(state.platform.assignments||{})[key]||'';} }
  function editProvider(p) { if(state.providerDirty&&!confirm('放弃当前连接表单修改？'))return;state.editingProvider=p?p.id:null;const f=$('provider-form');f.reset();for(const k of ['preset','name','api_base','model','promotion_url'])f.elements[k].value=p&&p[k]||(k==='preset'?'custom':'');state.providerDirty=false;$('provider-form-title').textContent=p?'编辑连接：'+p.name:'新增服务商连接';presetLinks(); }
  function presetLinks() { const p=(state.platform.provider_presets||[]).find(x=>x.id===$('provider-preset').value);const root=$('preset-links');root.replaceChildren(el('p','','服务商自行计费；不在平台代充或购买算力。请核实账号支持的模型ID。'));if(p){externalLink('服务商官网 · 自行注册充值',p.official_url||p.website||p.url,root);externalLink('推广入口（合作推广）',p.promotion_url,root);} }
  function renderJobs() { const list=$('jobs-list');list.replaceChildren();$('stat-total').textContent=state.jobs.length;for(const [key,s]of [['running','running'],['review','review_required'],['published','published']])$('stat-'+key).textContent=state.jobs.filter(j=>j.status===s).length;if(!state.jobs.length){list.append(el('div','empty',state.workspace?'本空间还没有真实内容任务。预置样稿不作为任务。':'请先进入一个工作空间。'));return;}state.jobs.forEach(j=>{const row=el('article','task');const info=el('div','task-info');info.append(el('h3','',j.title||j.wechat_title||(j.kind==='weekly'?'周报任务':'内容任务')),el('p','task-meta',formatDate(j.created_at)+' · '+j.id));if(j.stage_label)info.append(el('p','help',j.stage_label));if(j.error)info.append(el('p','error-text',j.error));row.append(info,badge(j.status),action('查看详情 →',()=>{location.hash='detail/'+encodeURIComponent(j.id);},'ghost'));list.append(row);}); }
  async function loadJobs() { const id=owner(),token=state.scope,seq=++state.jobsSeq;if(!id){renderJobs();return;}try{const jobs=await api(scoped('/api/jobs',id));if(!owned(id,token)||seq!==state.jobsSeq)return;if(!Array.isArray(jobs))throw new Error('任务列表格式不正确。');state.jobs=jobs.filter(j=>j.workspace_id===id);$('jobs-error').hidden=true;renderJobs();}catch(e){if(!owned(id,token)||seq!==state.jobsSeq)return;$('jobs-error').textContent='获取任务失败：'+e.message;$('jobs-error').hidden=false;} }
  function schedulePoll() { clearTimeout(state.timer);const delay=state.jobs.some(j=>j.status==='running')?2000:10000;$('poll-status').textContent='仅同步当前空间 · '+delay/1000+'秒间隔';state.timer=setTimeout(refresh,delay); }
  async function refresh() { await Promise.allSettled([loadJobs(),state.selected&&location.hash.startsWith('#detail')&&!state.contentDirty&&!state.reviewDirty&&!state.mutation?loadDetail(false):Promise.resolve()]);schedulePoll(); }
  async function loadDetail(loading) { const id=state.selected,wid=owner(),token=state.scope,seq=++state.detailSeq;if(!id||!wid)return;if(loading)$('detail-content').replaceChildren(el('div','empty panel','正在加载当前空间稿件…'));try{const job=await api(scoped('/api/jobs/'+encodeURIComponent(id),wid));if(!owned(wid,token)||state.selected!==id||seq!==state.detailSeq)return;if(!job||job.workspace_id!==wid)throw new Error('任务归属缺失或不属于当前空间。');const changed=JSON.stringify(state.detail)!==JSON.stringify(job);state.detail=job;if(!state.contentDirty&&!state.reviewDirty&&!state.mutation&&(loading||changed))renderDetail(job);}catch(e){if(!owned(wid,token)||state.selected!==id||seq!==state.detailSeq)return;if(loading)$('detail-content').replaceChildren(el('div','panel empty','加载稿件失败：'+e.message),action('重试',()=>loadDetail(true)));else showNotice('同步稿件失败：'+e.message,true);} }
  function fillConfig(c) { state.config=c;const f=$('config-form');configFields.forEach(k=>{if(f.elements[k].type==='checkbox')f.elements[k].checked=!!c[k];else f.elements[k].value=c[k]==null?(k==='dsa_timeout'?600:''):c[k];});$('config-fields').disabled=!finance();$('save-config').disabled=!finance();$('config-state').textContent='已加载财经高级配置'; }
  async function loadConfig() { if(!finance())return;const wid=owner(),token=state.scope;try{const d=await api('/api/config');if(!owned(wid,token)||state.dirty)return;fillConfig(d.config||d);}catch(e){if(owned(wid,token))$('config-state').textContent='加载失败：'+e.message;} }
  function makeLabel(title, name, value, multiline = false) { const label = el('label', '', title); const field = el(multiline ? 'textarea' : 'input'); field.name = name; field.id = 'edit-' + name; field.value = textValue(value); field.disabled = state.detail.status === 'running'; if (multiline) field.rows = 9; field.addEventListener('input', () => { state.contentDirty = true; updateDetailButtons(); }); label.append(field); return label; }
  function detailButton(label, action, className = 'secondary') { const b = el('button', className, label); b.type = 'button'; b.dataset.detailAction = action; b.addEventListener('click', () => mutateDetail(action)); return b; }
  function isReady(job) {
    if (!job || ['running', 'failed', 'needs_ai', 'materials_ready'].includes(job.status)) return false;
    if (job.publication && typeof job.publication.ready === 'boolean') return job.publication.ready;
    return false;
  }
  function jobBase(id = state.selected) { return '/api/jobs/' + encodeURIComponent(id); }
  function savedGuard(event) {
    if (state.contentDirty || state.mutation || !state.detail || ['running','failed'].includes(state.detail.status)) {
      if (event) event.preventDefault();
      showNotice(state.contentDirty ? '有未保存的改稿，请先「保存改稿」，再复制、预览或下载。不会复制旧稿。' : '请等待任务或保存操作完成后再操作。', true);
      return false;
    }
    return true;
  }
  function publishGuard() {
    if (!savedGuard()) return false;
    if (!isReady(state.detail)) { showNotice('这份内容尚不是可发布成稿。请在「服务商」连接AI后重新生成；当前仅可查看或导出资料包。', true); return false; }
    return true;
  }
  function copyButton(label, value) {
    const b = el('button', 'secondary', label); b.type = 'button'; b.dataset.publishCopy = 'true';
    b.addEventListener('click', async () => {
      if (!publishGuard()) return;
      try { if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('clipboard unavailable'); await navigator.clipboard.writeText(textValue(value)); showNotice(label.replace('复制', '') + '已复制（纯文本）。'); }
      catch { showNotice('浏览器未允许剪贴板访问。请在页面选中文字手动复制，或打开排版预览手选；未自动复制。', true); }
    }); return b;
  }
  function assetLink(label, path, download = false) {
    const a = el('a', 'button ghost', label); a.href = path.startsWith('/api/jobs/') ? scoped(path) : path;
    if (download) a.download = ''; else { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    a.addEventListener('click', savedGuard); return a;
  }
  function sourceRefs(ids) { return Array.isArray(ids) && ids.length ? '来源 ID：' + ids.map((id) => textValue(id)).join(' · ') : '来源待核验'; }
  function addTextList(parent, title, values) {
    if (!Array.isArray(values) || !values.length) return;
    parent.append(el('h3', '', title)); const list = el('ul', 'editorial-list'); values.forEach((value) => list.append(el('li', '', textValue(value)))); parent.append(list);
  }
  function renderDetail(job) {
    const root = $('detail-content'); root.replaceChildren();
    const ready = isReady(job); const editorial = job.editorial && typeof job.editorial === 'object' ? job.editorial : {};
    const topics = Array.isArray(editorial.topics) ? editorial.topics : [];
    const selected = editorial.selected_topic || topics.find((t) => t.id === editorial.selected_topic_id) || {};
    const analysis = editorial.analysis || {}; const currentAI = job.engine_mode === 'ai_editorial';
    const header = el('div', 'panel detail-header'); header.append(badge(job.status), el('h2', '', job.title || job.wechat_title || '内容任务'));
    const meta = el('div', 'detail-meta');
    [job.kind === 'weekly' ? '周报' : '本篇内容', formatDate(job.created_at), ...(job.domain === 'finance' ? ['交易日：' + textValue(job.market_date, '暂无')] : []), job.status === 'failed' ? '生成失败 · 未交付成稿' : job.status === 'running' ? '生产中 · 内容未就绪' : currentAI ? '真实AI选题与分析 · 需人工复核' : job.engine_mode === 'facts' ? '仅资料采集 · 非成稿' : '旧版资料 · 非新版AI选题', '数据来源：' + textValue(job.data_origin, '未提供')].forEach((v) => meta.append(el('span', '', v)));
    header.append(meta); const rules = currentDirection().rules; if (rules) header.append(el('p', 'help', '本领域边界：' + textValue(rules))); if (job.error) header.append(el('p', 'error-text', job.error));
    if (job.status === 'running') header.append(el('p', 'help', (job.stage_label || '正在采集资料、生成选题与内容') + '，完成后自动呈现。无需重复提交。'));
    if (!ready && job.status !== 'running') {
      const warning = el('div', 'readiness-warning'); warning.append(el('b', '', job.status === 'failed' ? '本次生成失败，未交付可发布成稿；请检查错误并重试' : job.status === 'needs_ai' ? 'AI生成未完成：资料已保留，但不是可发布成稿' : job.engine_mode === 'facts' ? '资料采集完成，未生成AI选题/分析，不是可发布成稿' : currentAI ? '内容暂不可发布，请查看检查结果' : '这是旧版资料，不是新版AI选题与分析成稿'));
      if (job.publication && job.publication.reason) warning.append(el('p', '', job.publication.reason));
      const setup = el('a', '', '去「服务商」连接模型 →'); setup.href = '#providers'; warning.append(setup, el('p', 'help', '资料仍可查看、下载用于研究；不能通过审核或标记发布。配置AI后请重新生成。')); header.append(warning);
    } else if (ready) header.append(el('p', 'help', 'AI内容已生成，尚需人工核验。手工改稿后必须重新审核；就绪不代表已经批准。'));
    root.append(header);
    const hero = el('section', 'panel editorial-hero'); hero.append(el('div', 'eyebrow', currentAI ? 'AI已自动选出主推题 · 无需手动选题' : '资料工作区'));
    hero.append(el('h2', '', currentAI ? selected.title || '主推选题待确认' : '先把资料变成观点，再谈发布'));
    if (currentAI) {
      if (editorial.analysis_stale) hero.append(el('p', 'readiness-warning', '正文/图卡已人工修改；此处为AI初稿分析，请以当前已保存正文和图卡复核。'));
      if (selected.angle) hero.append(el('p', 'editorial-angle', selected.angle));
      const reasonGrid = el('div', 'reason-grid');
      [['为什么今天推荐', selected.why_today], ['给读者的价值', selected.reader_value], [editorial.analysis_stale ? 'AI初稿核心观点（未随改稿更新）' : '文章核心观点', analysis.thesis]].forEach(([label, value]) => { const card = el('article'); card.append(el('h3', '', label), el('p', '', value || '未提供，请人工核验')); reasonGrid.append(card); }); hero.append(reasonGrid);
      if (selected.limitations && (typeof selected.limitations === 'string' || Array.isArray(selected.limitations))) hero.append(el('p', 'help', '选题边界：' + (Array.isArray(selected.limitations) ? selected.limitations.join('；') : selected.limitations)));
      hero.append(el('p', 'help', sourceRefs(selected.evidence_ids) + '（展开高级区域可查来源）'));
    } else hero.append(el('p', '', '本任务只提供事实资料或旧版内容，没有新版AI主推方向。以下预览和卡片仅供资料核查，不应当作可发布AI成稿。'));
    root.append(hero);
    const platforms = el('div', 'platforms');
    const wechat = el('section', 'panel platform-panel'); wechat.append(el('div', 'platform-kicker', '01 / 公众号'), el('h2', '', ready ? '可视排版稿' : '资料排版预览 · 非成稿'), el('p', 'platform-steps', '① 复制标题 → ② 复制排版正文 → ③ 公众号后台预览发布'));
    const title = el('h3', 'publish-title', job.wechat_title || '暂无公众号标题'); wechat.append(title);
    const wa = el('div', 'actions'); wa.append(copyButton('复制公众号标题', job.wechat_title));
    const htmlButton = el('button', 'primary', '复制排版正文'); htmlButton.type = 'button'; htmlButton.dataset.publishCopy = 'true';
    htmlButton.addEventListener('click', () => {
      if (!publishGuard()) return;
      const copyId = state.selected, copyOwner = owner(), copyToken = state.scope; const base = jobBase(copyId);
      // Start the safe-HTML request during the user's click; ClipboardItem promises retain user activation in supporting browsers.
      let safeHTMLPromise;
      const getHTML = () => safeHTMLPromise || (safeHTMLPromise = fetch(scoped(base + '/publish-html', copyOwner), { headers: { Accept: 'text/html' } }).then(async (response) => { if (!response.ok) throw new Error('无法获取排版正文（HTTP ' + response.status + '）'); const html = await response.text(); if (!html.trim()) throw new Error('排版正文为空'); if (!owned(copyOwner, copyToken) || state.selected !== copyId || state.contentDirty || state.mutation) throw new Error('稿件已切换或修改，请保存后重新复制'); return html; }));
      const plain = (html) => { const doc = new DOMParser().parseFromString(html, 'text/html'); doc.querySelectorAll('script,style,head').forEach((node) => node.remove()); doc.querySelectorAll('td,th').forEach((node) => node.after(doc.createTextNode('\t'))); doc.querySelectorAll('p,h1,h2,h3,li,blockquote,tr,br').forEach((node) => node.after(doc.createTextNode('\n\n'))); return ((doc.querySelector('article') || doc.body).textContent || '').replace(/\n{3,}/g, '\n\n').trim(); };
      (async () => {
        try {
          if (navigator.clipboard && navigator.clipboard.write && typeof ClipboardItem !== 'undefined' && (!ClipboardItem.supports || ClipboardItem.supports('text/html'))) {
            const html = getHTML();
            await navigator.clipboard.write([new ClipboardItem({ 'text/html': html.then((value) => new Blob([value], { type: 'text/html' })), 'text/plain': html.then((value) => new Blob([plain(value)], { type: 'text/plain' })) })]);
            showNotice('已复制公众号排版正文（HTML + 纯文本）。请粘贴到公众号后台并预览，平台可能调整样式。');
          } else {
            const html = await getHTML();
            if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('浏览器不支持剪贴板');
            await navigator.clipboard.writeText(plain(html)); showNotice('此浏览器仅复制了纯文本，未保留排版。需要样式时请打开「排版预览」手动选择正文复制。', true);
          }
        } catch (error) { showNotice('未能复制排版正文：' + error.message + '。请打开「排版预览」手动选择正文复制；格式是否保留请在公众号后台预览确认。', true); }
      })();
    }); wa.append(htmlButton, assetLink('打开排版预览 ↗', jobBase() + '/preview')); wechat.append(wa);
    wechat.append(el('p', 'help', '预览由后端提供安全HTML；标题单独复制。不会自动跳转官方站点，也不会自动发布。'));
    const frameWrap = el('div', 'preview-wrap'); const frame = el('iframe', 'wechat-preview'); frame.title = ready ? '公众号正文安全排版预览' : '资料包排版预览（非成稿）'; frame.loading = 'lazy'; frame.setAttribute('sandbox', 'allow-same-origin'); frame.referrerPolicy = 'same-origin'; if (!['running','failed'].includes(job.status)) frame.src = scoped(jobBase() + '/preview?view=' + Date.now()); else frameWrap.append(el('p','help','任务尚未交付内容，完成后才加载排版预览。')); frameWrap.append(frame);
    const unsaved = el('div', 'preview-unsaved', '内容已有未保存修改。请先在高级区域保存改稿，再查看最新排版、复制或下载。'); unsaved.hidden = true; frameWrap.append(unsaved); wechat.append(frameWrap);
    const xhs = el('section', 'panel platform-panel'); xhs.append(el('div', 'platform-kicker', '02 / 小红书'), el('h2', '', ready ? '整组图卡与文案' : '资料图卡 · 非成稿'), el('p', 'platform-steps', '① 按顺序上传图片 → ② 粘贴标题与文案 → ③ 小红书平台预览发布'));
    const xa = el('div', 'actions'); xa.append(assetLink('↓ 下载整组图片 ZIP', jobBase() + '/images', true)); xhs.append(xa, el('p', 'help', '按图卡顺序上传。点图片可在新窗口放大；下载包不代替人工核验。'));
    const cards = el('div', 'publish-cards');
    (Array.isArray(job.cards) ? job.cards : []).forEach((url, i) => {
      const src = safeURL(url, true); if (!src || new URL(src).origin !== location.origin) { cards.append(el('p', 'help', '卡片 ' + (i + 1) + ' 不是可用的同源图片地址')); return; }
      const card = el('figure', 'publish-card'); const a = el('a', 'card-zoom'); a.href = src; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.addEventListener('click', savedGuard);
      const img = el('img'); img.src = src; img.alt = '小红书第 ' + (i + 1) + ' 张图卡（点击放大）'; img.loading = 'lazy'; img.addEventListener('error', () => { img.replaceWith(el('p', 'error-text', '图卡加载失败，请下载资料核查')); }); a.append(img); card.append(a);
      const caption = el('figcaption'); const plan = Array.isArray(editorial.card_plan) ? editorial.card_plan[i] : null; caption.append(el('b', '', String(i + 1).padStart(2, '0') + ' / ' + (plan && plan.title || '内容图卡')), assetLink('下载此图', src, true)); if (plan && plan.source_ids) caption.append(el('small', 'help', sourceRefs(plan.source_ids))); card.append(caption); cards.append(card);
    }); if (!cards.childNodes.length) cards.append(el('p', 'help', '图卡尚未生成')); xhs.append(cards);
    if ((job.cards || []).length && job.cards.length !== 4) xhs.append(el('p', 'error-text', '预期4张图卡，当前' + job.cards.length + '张，请核验整组完整性。'));
    xhs.append(el('h3', 'publish-title', job.xhs_title || '暂无小红书标题'), el('pre', 'xhs-copy', job.xhs_text || '暂无小红书正文'));
    const xc = el('div', 'actions'); xc.append(copyButton('复制小红书标题', job.xhs_title), copyButton('复制小红书正文', job.xhs_text)); xhs.append(xc); platforms.append(wechat, xhs); root.append(platforms);
    const advanced = el('details', 'panel advanced-editor'); advanced.append(el('summary', '', '高级 · 编辑改稿、来源证据与版本记录'));
    const editor = el('div', 'editor-panel'); editor.append(el('h2', '', '手动改稿'), el('p', 'help', '改动任何字段后，请先保存再复制、预览或下载。保存会撤销已有批准；图卡不会随文字改动自动更新，需重新核验。'), makeLabel('公众号标题', 'wechat_title', job.wechat_title), makeLabel('公众号正文 · Markdown', 'wechat_markdown', job.wechat_markdown, true), makeLabel('小红书标题', 'xhs_title', job.xhs_title), makeLabel('小红书正文', 'xhs_text', job.xhs_text, true), detailButton('保存改稿', 'save', 'primary')); advanced.append(editor);
    const plan = Array.isArray(editorial.card_plan) ? editorial.card_plan : [];
    if (plan.length) {
      const cardEditor = el('section', 'editor-panel'); cardEditor.append(el('h2', '', '编辑小红书图卡'), el('p', 'help', '文章改动不会自动修改图卡；可在此直接修改图卡文字，保存后服务端重新渲染图片并撤销批准。请逐张核验数据与来源。'));
      plan.forEach((card, i) => { const titleLabel = makeLabel('第 ' + (i + 1) + ' 张标题（最多28字）', 'card-title-' + i, card.title); const bodyLabel = makeLabel('第 ' + (i + 1) + ' 张正文（最多240字）', 'card-body-' + i, card.body, true); titleLabel.querySelector('input').maxLength = 28; bodyLabel.querySelector('textarea').maxLength = 240; bodyLabel.querySelector('textarea').rows = 4; cardEditor.append(titleLabel, bodyLabel, el('p', 'help', sourceRefs(card.source_ids))); });
      advanced.append(cardEditor);
    }
    const evidence = el('section', 'editor-panel'); evidence.append(el('h2', '', editorial.analysis_stale ? 'AI初稿分析与证据（未随改稿更新）' : '选题分析与证据'));
    if (editorial.analysis_stale) evidence.append(el('p', 'readiness-warning', '正文/图卡已人工修改；此处为AI初稿分析，请以当前已保存正文和图卡复核。'));
    (Array.isArray(analysis.evidence) ? analysis.evidence : []).forEach((item) => { const p = el('div', 'evidence-item'); p.append(el('p', '', item.claim || ''), el('small', 'help', sourceRefs(item.source_ids))); evidence.append(p); }); addTextList(evidence, '反面观点 / 风险', analysis.counterpoints); addTextList(evidence, '后续观察点', analysis.watchpoints);
    if (topics.length) { evidence.append(el('h3', '', 'AI候选方向（已自动选定主推题，无需你再选）')); topics.forEach((t) => { const card = el('div', 'evidence-item'); card.append(el('b', '', (t.id === editorial.selected_topic_id ? '主推 · ' : '') + textValue(t.title)), el('p', '', t.angle || ''), el('small', 'help', sourceRefs(t.evidence_ids))); evidence.append(card); }); }
    advanced.append(evidence);
    const sources = el('section', 'editor-panel'); sources.append(el('h2', '', '来源详情 · 按ID核验'));
    const catalog = Array.isArray(editorial.source_catalog) && editorial.source_catalog.length ? editorial.source_catalog : Array.isArray(job.sources) ? job.sources : [];
    catalog.forEach((source, i) => { const item = el('div', 'source'); item.append(el('b', '', '[' + textValue(source.id, String(i + 1)) + '] ' + textValue(source.title, '未命名来源'))); const url = safeURL(source.url); if (url) { const a = el('a', 'source-url', url); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; item.append(a); } item.append(el('small', '', textValue(source.scope, source.published_at ? formatDate(source.published_at) : '范围 / 时间未提供'))); sources.append(item); }); if (!catalog.length) sources.append(el('p', 'help', '暂无来源，请先核实数据出处。')); advanced.append(sources);
    const versions = el('section', 'editor-panel'); versions.append(el('h2', '', '版本记录'), el('pre', 'version-list', Array.isArray(job.versions) && job.versions.length ? job.versions.map((v) => textValue(v)).join('\n\n') : '暂无历史版本')); advanced.append(versions); root.append(advanced);
    const review = el('section', 'panel review-panel final-review'); review.append(el('div', 'platform-kicker', '最后一步 / 人工复核'), el('h2', '', '审核与发布归档'));
    const checks = el('div', 'quality-checks'); (Array.isArray(job.checks) ? job.checks : []).forEach((c) => { const item = el('div', 'check-item'); item.append(el('b', '', textValue(c.level, '提示')), document.createTextNode(textValue(c.message))); checks.append(item); }); review.append(checks);
    const note = el('label', '', '审核意见 / 重生成要求'); const noteInput = el('textarea'); noteInput.id = 'review-note'; noteInput.rows = 3; noteInput.value = job.review_note || ''; noteInput.placeholder = '核查观点和来源，或写下重生成要求'; noteInput.addEventListener('input', () => { state.reviewDirty = true; }); note.append(noteInput); review.append(note);
    const cardConfirm = el('label', 'check-row'); const cardCheck = el('input'); cardCheck.type = 'checkbox'; cardCheck.id = 'confirm-cards'; cardCheck.checked = Boolean(job.cards_confirmed_at && ['approved', 'published'].includes(job.status)); cardCheck.addEventListener('change', () => { state.reviewDirty = true; }); cardConfirm.append(cardCheck, document.createTextNode('我已逐张核对图卡、文案与来源。文字改稿不会同步更新图片，事实或来源有误时应重新采集。')); review.append(cardConfirm);
    const reviewActions = el('div', 'actions'); reviewActions.append(detailButton('通过审核', 'approve', 'primary'), detailButton('退回修改', 'reject', 'danger'), detailButton('按意见重生成', 'regenerate')); review.append(reviewActions);
    const urlLabel = el('label', '', '手动发布URL（发布后填写）'); const urlInput = el('input'); urlInput.id = 'publish-url'; urlInput.type = 'url'; urlInput.placeholder = 'https://…'; urlInput.value = job.publish_url || ''; urlInput.addEventListener('input', () => { state.reviewDirty = true; }); urlLabel.append(urlInput); review.append(urlLabel);
    const archiveActions = el('div', 'actions'); archiveActions.append(detailButton('标记已发布', 'publish'), assetLink(ready ? '↓ 导出内容包 ZIP' : '↓ 导出资料包 ZIP（非成稿）', jobBase() + '/export', true)); review.append(archiveActions, el('p', 'help', ready ? '先通过审核，再到平台手动粘贴并预览发布，最后填写URL归档。本工具不会自动发布。人工改稿后请重新核验；ready状态不代表人工已批准。' : '本任务不可通过审核或标记发布。资料导出仅供研究，不是可发布成稿。')); root.append(review); updateDetailButtons();
  }
  function updateDetailButtons() { if (!state.detail) return; const status = state.detail.status; document.querySelectorAll('[data-detail-action]').forEach((b) => { const action = b.dataset.detailAction; b.disabled = state.mutation || status === 'running' || (action === 'save' && !state.contentDirty) || (['approve', 'reject', 'regenerate', 'publish'].includes(action) && state.contentDirty) || (['approve', 'publish'].includes(action) && !isReady(state.detail)) || (['approve', 'reject', 'publish'].includes(action) && ['failed', 'published'].includes(status)) || (action === 'publish' && status !== 'approved') || (action === 'approve' && status === 'approved') || (action === 'reject' && status === 'rejected'); }); document.querySelectorAll('[data-publish-copy]').forEach(b => { b.disabled = state.mutation || state.contentDirty || !isReady(state.detail); }); const overlay = document.querySelector('.preview-unsaved'); if (overlay) overlay.hidden = !state.contentDirty; const frame = document.querySelector('.wechat-preview'); if (frame) { frame.inert = state.contentDirty; frame.tabIndex = state.contentDirty ? -1 : 0; } }
  async function mutateDetail(action) {
    if (state.mutation || !state.detail) return; const button = document.querySelector(`[data-detail-action="${action}"]`); if (!button || button.disabled) return;
    const id = state.selected, wid = state.detail.workspace_id || owner(), token = state.scope; const base = '/api/jobs/' + encodeURIComponent(id); const note = $('review-note').value; const publishURL = $('publish-url').value.trim();
    if (action === 'approve' && !$('confirm-cards').checked) { showNotice('请先核对全部卡片，并勾选图片复核确认。', true); $('confirm-cards').focus(); return; }
    if (action === 'publish' && (!safeURL(publishURL) || !/^https?:\/\//i.test(publishURL))) { showNotice('请填写完整的 http:// 或 https:// 手工发布 URL。', true); $('publish-url').focus(); return; }
    if (action === 'publish' && !confirm('确认已在平台手动发布？此操作仅记录发布 URL，不会替你发布内容。')) return;
    if (action === 'regenerate' && !note.trim()) { showNotice('请先填写重生成意见，说明希望如何调整。', true); $('review-note').focus(); return; }
    state.mutation = true; updateDetailButtons(); let nextId = null;
    try {
      if (action === 'save') { const payload = {}; contentFields.forEach((name) => { payload[name] = $('edit-' + name).value; }); const plan = state.detail.editorial && state.detail.editorial.card_plan; if (Array.isArray(plan) && plan.length) payload.card_plan = plan.map((card, i) => ({ title: $('edit-card-title-' + i).value, body: $('edit-card-body-' + i).value, source_ids: Array.isArray(card.source_ids) ? card.source_ids : [] })); await api(scoped(base + '/content', wid), { method: 'PUT', body: JSON.stringify(payload) }); state.contentDirty = false; showNotice('改稿已保存，原批准状态已撤销。图卡如有修改已重新渲染，请重新审核。'); }
      else if (action === 'regenerate') { const result = await api(scoped(base + '/regenerate', wid), { method: 'POST', body: JSON.stringify({ note }) }); nextId = result && result.id; showNotice('已创建重生成任务，原任务保留。'); }
      else { await api(scoped(base + '/review', wid), { method: 'POST', body: JSON.stringify({ action, note, publish_url: publishURL, confirm_cards: $('confirm-cards').checked }) }); showNotice(action === 'publish' ? '已记录手工发布结果。此操作未调用平台发布接口。' : action === 'approve' ? '审核已通过。请导出内容并手动发布。' : '稿件已退回。可填写意见后重生成。'); }
    } catch (e) { showNotice('操作失败：' + e.message + ' 当前输入已保留。', true); return; }
    finally { state.mutation = false; updateDetailButtons(); }
    if (!owned(wid, token) || state.selected !== id) return;
    state.reviewDirty = false;
    if (nextId != null) location.hash = 'detail/' + encodeURIComponent(nextId); else await loadDetail(false); await loadJobs(); schedulePoll();
  }

  $('domain-filter').addEventListener('change',()=>{state.offset=0;state.all=false;renderDirections();});
  $('next-directions').addEventListener('click',()=>{state.all=false;state.offset+=3;renderDirections();});
  $('all-directions').addEventListener('click',()=>{state.all=!state.all;renderDirections();});
  $('reset-directions').addEventListener('click',()=>{state.excluded.clear();state.offset=0;renderDirections();});
  $('close-sample').addEventListener('click',()=>$('sample-dialog').close());
  $('profile-form').addEventListener('input',()=>{state.profileDirty=true;$('profile-state').textContent='有未保存微调';});
  $('profile-form').addEventListener('submit',async e=>{e.preventDefault();if(!owner())return;const f=e.currentTarget,wid=owner(),token=state.scope;const profile={};profileFields.forEach(k=>{if(k==='stock_list'&&!finance())return;profile[k]=k==='schedule_enabled'?f.elements[k].checked:k==='weekly_day'?Number(f.elements[k].value):f.elements[k].value;});const button=f.querySelector('button[type=submit]');button.disabled=true;f.inert=true;try{const w=await api('/api/workspaces/'+encodeURIComponent(wid),json('PUT',{name:f.elements.name.value,profile}));if(!owned(wid,token))return;state.workspace=upsertWorkspace(w);state.profileDirty=false;renderWorkspace();renderPlatform();showNotice('本空间偏好已保存。');}catch(error){if(owned(wid,token))showNotice('保存偏好失败：'+error.message,true);}finally{button.disabled=false;f.inert=false;}});
  $('feedback-form').addEventListener('input',()=>{state.feedbackDirty=true;});
  $('feedback-form').addEventListener('submit',async e=>{e.preventDefault();if(!owner())return;const f=e.currentTarget,wid=owner(),token=state.scope;const payload={published:f.elements.published.value,difficulty:f.elements.difficulty.value,question:f.elements.question.value};const button=f.querySelector('button');button.disabled=true;f.inert=true;try{const w=await api('/api/workspaces/'+encodeURIComponent(wid)+'/feedback',json('POST',payload));if(!owned(wid,token))return;state.workspace=upsertWorkspace(w);state.feedbackDirty=false;if(!state.profileDirty)renderWorkspace();showNotice('反馈已保存到当前空间。');}catch(error){if(owned(wid,token))showNotice('反馈保存失败：'+error.message,true);}finally{button.disabled=false;f.inert=false;}});
  $('provider-form').addEventListener('input',()=>{state.providerDirty=true;});
  $('provider-preset').addEventListener('change',()=>{const p=(state.platform.provider_presets||[]).find(x=>x.id===$('provider-preset').value);if(p){const f=$('provider-form');f.elements.name.value=p.name||'';f.elements.api_base.value=p.api_base||'';f.elements.model.value=p.model||'';f.elements.promotion_url.value=p.promotion_url||'';}presetLinks();});
  $('cancel-provider').addEventListener('click',()=>editProvider(null));
  $('provider-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const payload={};for(const k of ['preset','name','api_base','model','api_key','promotion_url'])payload[k]=f.elements[k].value.trim();payload.clear_api_key=f.elements.clear_api_key.checked;const id=state.editingProvider,button=f.querySelector('button[type=submit]');button.disabled=true;try{await api('/api/providers'+(id?'/'+encodeURIComponent(id):''),json(id?'PUT':'POST',payload));state.providerDirty=false;await refreshPlatform();renderProviders();editProvider(null);renderMode();showNotice('连接已保存，尚未测试。可手动测试一次真实请求（可能收费）。');}catch(error){showNotice('保存连接失败：'+error.message,true);}finally{button.disabled=false;f.inert=false;}});
  $('assignments-form').addEventListener('change',()=>{state.assignmentsDirty=true;});
  $('assignments-form').addEventListener('submit',async e=>{e.preventDefault();const f=e.currentTarget;const payload={topic_provider_id:f.elements.topic_provider_id.value||null,writing_provider_id:f.elements.writing_provider_id.value||null};f.inert=true;try{await api('/api/assignments',json('PUT',payload));state.assignmentsDirty=false;await refreshPlatform();renderProviders();renderMode();showNotice('默认任务分配已保存。');}catch(error){showNotice('保存分配失败：'+error.message,true);}finally{f.inert=false;}});
  $('config-form').addEventListener('input',()=>{state.dirty=true;$('config-state').textContent='高级配置有未保存修改';});
  $('config-form').addEventListener('submit',async e=>{e.preventDefault();if(!finance())return;const f=e.currentTarget,wid=owner(),token=state.scope;const payload={};configFields.forEach(k=>payload[k]=k==='dsa_run_enabled'?f.elements[k].checked:k==='dsa_timeout'?Number(f.elements[k].value):f.elements[k].value);$('save-config').disabled=true;try{await api('/api/config',json('PUT',payload));if(!owned(wid,token))return;state.dirty=false;showNotice('财经高级配置已保存。');await loadConfig();}catch(error){if(owned(wid,token))showNotice('保存高级配置失败：'+error.message,true);}finally{$('save-config').disabled=!finance();}});
  $('retry-config').addEventListener('click',loadConfig);
  $('refresh-jobs').addEventListener('click',refresh);
  document.querySelectorAll('.generate').forEach(button=>button.addEventListener('click',async()=>{const wid=owner(),token=state.scope;if(!wid){location.hash='explore';return;}if(state.generating.has(wid))return;if(hasEdits()){showNotice('请先保存或放弃当前未保存的修改，再生产任务。',true);return;}const ai=assignedAI();if(!ai&&!finance()){showNotice('此领域必须先配置选题与写作Key并保存默认分配；不会用样稿代替真实生成。',true);location.hash='providers';return;}if(!confirm(ai?'将创建真实AI任务，外部模型请求可能产生费用。确认开始？':'未配置完整AI连接，只会采集财经资料，不能批准或发布。继续采集？'))return;state.generating.add(wid);renderWorkspace();try{const job=await api('/api/jobs',json('POST',{workspace_id:wid,kind:button.dataset.kind}));if(!owned(wid,token))return;if(!job||!job.id||job.workspace_id!==wid)throw new Error('任务响应缺少ID或归属不正确，请刷新当前空间列表确认。');showNotice(ai?'真实任务已创建，结果以服务商与任务检查为准。':'资料任务已创建，不是可发布成稿。');await loadJobs();if(owned(wid,token)&&job&&job.id)location.hash='detail/'+encodeURIComponent(job.id);}catch(error){if(owned(wid,token))showNotice('创建任务失败：'+error.message,true);}finally{state.generating.delete(wid);if(owned(wid,token)){renderWorkspace();schedulePoll();}}}));
  window.addEventListener('hashchange',route);
  window.addEventListener('beforeunload',e=>{if(hasEdits()||state.mutation||state.transition){e.preventDefault();e.returnValue='';}});
  async function init(){route();try{const p=await refreshPlatform();state.workspace=p.workspaces.find(w=>w.id===p.active_workspace_id)||null;const filter=$('domain-filter');[...new Set(state.directions.map(d=>d.domain))].forEach(d=>filter.add(new Option(domainNames[d]||d,d)));renderPlatform();renderWorkspace();renderProviders();$('health').textContent='● 本地服务已连接 · v0.3';updateContext((location.hash.slice(1).split('/')[0])||'home');await loadJobs();route();schedulePoll();}catch(error){$('health').textContent='● 平台加载失败';showNotice('平台加载失败：'+error.message+'。请刷新重试；不会加载模拟状态。',true);}}
  init();
})();
