 'use strict';
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pretty = v => JSON.stringify(v,null,2);
const fmt = n => Number(n).toLocaleString('en-US');
const strategies = {keep:'完整保留',recent:'最近 N 项 / 轮',truncate:'字符截断',exclude:'排除组件'};
const roles = {system:'系统指令',context:'上下文数据',conversation:'对话记录'};
const sourceNames = {blocks:'文本块',items:'结构化条目',fields:'状态字段',tools:'外部工具目录',messages:'发言',calls:'工具调用与结果',traces:'Agent Loop / 思考',memories:'记忆'};
let sessionId=null, sessionData=null, artifact=null, sessions=[], editorSave=null, loading=false, refreshGeneration=0, toastTimer, dragId=null;
let databaseLabel='存储';
let templateState=null, templateRevision=null;
let templates=[], objectType='instance', view='templates', activeTemplateId=null;
let toolBindingId=null;
async function api(path,options={}){
 const res=await fetch('/api'+path,{headers:{'Content-Type':'application/json'},...options});const body=await res.json();
 if(!res.ok)throw new Error(typeof body.detail==='string'?body.detail:pretty(body.detail||body));return body;
}
const post=(path,body)=>api(path,{method:'POST',body:JSON.stringify(body)});
const base=()=> (objectType==='template'?'/templates/':'/sessions/')+sessionId;
function notify(message){$('#toast').textContent=message;$('#toast').classList.remove('hidden');clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').classList.add('hidden'),4500);}
const report=e=>notify(e.message||String(e));
function bind(id,fn){$(id).addEventListener('click',()=>Promise.resolve().then(fn).catch(report));}
async function append(type,payload,turnId=null){return post(base()+'/updates',{protocol_version:'0.1',update_id:crypto.randomUUID(),type,source:'workbench',turn_id:turnId,payload,expected_revision:sessionData.revision});}
function group(id){return sessionData.state.groups.find(g=>g.id===id);}
async function init(){try{
 const h=await api('/health');databaseLabel={postgresql:'PostgreSQL',sqlite:'SQLite',mysql:'MySQL'}[h.database]||h.database;$('#db-status').textContent=databaseLabel+' 已连接';$('#db-status').title=h.storage?.database||'';
 await Promise.all([loadSessions(),loadTemplates()]);
 const params=new URLSearchParams(location.search);
 if(params.get('template'))await selectTemplate(params.get('template'));
 else if(params.get('instance')||params.get('session'))await selectSession(params.get('instance')||params.get('session'));
 else await setView('templates');
}catch(e){$('#db-dot').classList.add('error');$('#db-status').textContent='存储未就绪';$('#offline').textContent=e.message;$('#offline').classList.remove('hidden');}}
function empty(){$('#empty').classList.remove('hidden');$('#session-content').classList.add('hidden');}
async function loadSessions(){sessions=await api('/sessions');renderUnassigned();renderScope();renderTemplates();}
function renderUnassigned(){const old=sessions.filter(s=>!s.template_id);$('#unassigned-panel').classList.toggle('hidden',!old.length);$('#unassigned-title').textContent='待归属旧实例 · '+old.length;$('#unassigned-list').innerHTML=old.map(s=>`<div class="legacy-row"><span>${esc(s.name)}</span><button class="text-button" data-session="${s.id}">查看内容</button><button class="button" data-assign-instance="${s.id}">设置归属</button></div>`).join('');}
async function loadTemplates(){templates=await api('/templates');renderTemplates();renderScope();}
function renderTemplates(){
 $('#template-nav').innerHTML=templates.map(t=>`<button class="session-item ${t.id===activeTemplateId?'active':''}" data-edit-template="${t.id}" title="${esc(t.name)}">${esc(t.name)}</button>`).join('');
 $('#template-list').innerHTML=templates.map(t=>`<article class="template-card"><span class="tag">${sessions.filter(s=>s.template_id===t.id).length} 个实例</span><h3>${esc(t.name)}</h3><p>${esc(t.description||'自定义上下文组成、默认内容与策略。')}</p><small>修订 ${t.revision}</small><div class="template-card-actions"><button class="button" data-edit-template="${t.id}">打开模板与实例</button><button class="text-button" data-duplicate-template="${t.id}">复制</button><button class="button primary" data-use-template="${t.id}">新建实例 ↗</button></div></article>`).join('')||'<p class="muted">还没有模板，点击“新建模板”开始。</p>';
}
function renderScope(){const template=templates.find(t=>t.id===activeTemplateId);const rows=sessions.filter(s=>s.template_id===activeTemplateId);
 $('#template-instances-panel').classList.toggle('hidden',!template||view!=='workspace');
 if(!template)return;
 $('#template-instances-panel').innerHTML=`<div class="section-heading"><div><h2>${esc(template.name)} <span class="tag">模板 / 实例</span></h2><p>实例按模板归属管理，简名在本模板内唯一。</p></div><div class="scope-actions"><button class="button" data-edit-template="${template.id}">模板配置</button><button class="button primary" data-use-template="${template.id}">＋ 新建实例</button></div></div><div class="instance-table-wrap"><table class="instance-table"><thead><tr><th>实例简名</th><th>名称</th><th>Agent 标记</th><th>操作</th></tr></thead><tbody>${rows.map(r=>`<tr class="${objectType==='instance'&&r.id===sessionId?'selected':''}"><td><code>${esc(r.slug||'待设置')}</code></td><td>${esc(r.name)}</td><td>${esc(r.agent_id||'未指定')}</td><td><button class="text-button" data-session="${r.id}">查看上下文</button><button class="text-button" data-connect-instance="${r.id}">接入代码</button></td></tr>`).join('')||'<tr><td colspan="4" class="muted">还没有实例。新建实例并设置简名，再让 Agent 接入。</td></tr>'}</tbody></table></div>`;
}
async function selectSession(id){objectType='instance';sessionId=id;toolBindingId=null;await refresh();history.replaceState(null,'','?instance='+id);await setView('workspace');await loadSessions();}
async function selectTemplate(id){objectType='template';sessionId=id;activeTemplateId=id;toolBindingId=null;await refresh();history.replaceState(null,'','?template='+id);await setView('workspace');await loadSessions();}
async function refresh(){const generation=++refreshGeneration,sid=sessionId,kind=objectType;
 const current=await api(base());
 const bindings=Object.values(current.state.host_bindings||{});
 if(kind==='instance'&&bindings.length&&!bindings.some(b=>b.id===toolBindingId))toolBindingId=(bindings.find(b=>b.active&&Date.parse(b.expires_at)>Date.now())||bindings[0]).id;
 const compiled=await api(base()+'/preview?budget_chars='+Number($('#budget').value||24000)+(kind==='instance'&&toolBindingId?'&binding_id='+encodeURIComponent(toolBindingId):''));
 if(generation!==refreshGeneration||sid!==sessionId||kind!==objectType)return;
 if(current.revision!==compiled.revision){return refresh();}
 const templateDocument=kind==='template'?current:current.binding?await api('/templates/'+current.binding.template_id):null;
 templateState=templateDocument?.state||null;templateRevision=templateDocument?.revision??null;
 if(generation!==refreshGeneration)return;
 sessionData={...current,session:current.template||current.session};artifact=compiled;renderStatePanel();renderToolPanel();renderStats();renderGroups();renderPreview();renderProtocol();renderObject();renderScope();}

function toolDefinitions(){
 const result=new Map(Object.entries(sessionData.state.tool_catalog||{}));const binding=sessionData.state.host_bindings?.[toolBindingId];
 if(binding){for(const [pid,p] of Object.entries(binding.providers)){for(const t of p.tools)result.set(pid+'/'+t.name,t);}}
 else{for(const t of Object.values(sessionData.state.tools||{}))result.set('legacy/'+t.name,t);}
 return result;
}
function renderToolPanel(){
 const bindings=Object.values(sessionData.state.host_bindings||{}),binding=sessionData.state.host_bindings?.[toolBindingId];
 const definitions=toolDefinitions(),prompts=sessionData.state.tool_prompts||{};
 const refs=[...new Set([...definitions.keys(),...Object.keys(prompts)])];
 const live=binding?.active&&Date.parse(binding.expires_at)>Date.now();
 const options=bindings.map(b=>`<option value="${esc(b.id)}" ${b.id===toolBindingId?'selected':''}>${esc(b.host_id)} · ${esc(b.id.slice(0,8))} · ${b.active&&Date.parse(b.expires_at)>Date.now()?'连接有效':'已关闭或过期'}</option>`).join('');
 const providerStatus=binding?Object.entries(binding.providers).map(([id,p])=>`${id} (${p.kind}): ${{pending:'待发现',ready:'已确认',error:'发现失败'}[p.status]} · ${p.tools.length} 个工具`).join('；'):'';
 $('#tool-panel').innerHTML=`<div class="section-heading"><div><h2>工具绑定与提示词 <span class="tag">MCP 优先</span></h2><p>连接 MCP 或注册本地方法后即可编辑，无需先运行模型。描述配置可复用，实际工具按宿主连接隔离。</p></div><button class="button" data-new-tool-prompt="1">＋ 工具提示词</button></div>${bindings.length?`<label class="field">预览宿主绑定<select id="tool-binding">${options}</select></label><p>${esc(providerStatus||'尚未同步任何提供方目录')} · 修订 ${binding?.revision??0}</p>`:'<p class="muted">尚无宿主绑定。可先填写工具提示词；MCP 接入与本地注册代码见协议文档。当前显示旧版目录预览。</p>'}<div class="instance-table-wrap"><table class="instance-table"><thead><tr><th>工具身份</th><th>当前绑定</th><th>描述配置</th><th>操作</th></tr></thead><tbody>${refs.map(ref=>{const p=prompts[ref],t=definitions.get(ref),provider=binding?.providers[ref.split('/')[0]];const status=binding?(!live?'绑定已失效':!provider?'缺失':provider.status!=='ready'?'未确认':provider.tools.some(item=>item.name===ref.slice(ref.indexOf('/')+1))?'已发现':'缺失'):(t?'目录已知':'未绑定');return `<tr><td><code>${esc(ref)}</code><small>${esc(t?.description||'可提前配置，等待宿主注册')}</small></td><td>${status}${p?.required?' · 必需':''}</td><td>${p?(p.enabled?'已配置':'已禁用'):'使用来源描述'}</td><td><button class="text-button" data-tool-prompt="${esc(ref)}">编辑描述与参数解释</button>${p?`<button class="text-button" data-delete-tool-prompt="${esc(ref)}">恢复来源描述</button>`:''}</td></tr>`;}).join('')||'<tr><td colspan="4" class="muted">暂无工具。推荐通过 MCP 自动发现，或在宿主中注册本地方法。</td></tr>'}</tbody></table></div>`;
 $('#tool-binding')?.addEventListener('change',e=>{toolBindingId=e.target.value;refresh().catch(report);});
}
function editToolPrompt(ref=''){
 captureScopeRevision();const t=toolDefinitions().get(ref),p=sessionData.state.tool_prompts?.[ref];
 dialog('工具提示词配置',scopeField()+field('工具身份（provider_id/tool_name）','tool_ref',ref)+select('工具启用','enabled',{true:'启用',false:'禁用'},String(p?.enabled??true))+select('工具要求','required',{false:'可选：缺失时不暴露给模型',true:'必需：缺失时阻止模型调用'},String(p?.required??false))+field('模型看到的工具描述','description',p?.description??t?.description??'','textarea')+field('参数解释：JSON Pointer → 描述','parameter_descriptions',pretty(p?.parameter_descriptions||{}),'textarea','例如 {"/properties/query":"包含模块名或错误关键词的检索词"}。只修改解释，不改变类型和必填项。')+(t?`<details><summary>原始参数 Schema</summary><pre>${esc(pretty(t.input_schema))}</pre></details>`:'<p class="dialog-note">当前没有此工具的执行定义。配置会保留，工具接入后自动匹配。</p>'),async d=>{
  if(ref&&d.tool_ref!==ref)throw new Error('已有工具身份不能修改，请新增配置。');
  const descriptions=JSON.parse(d.parameter_descriptions);if(!descriptions||Array.isArray(descriptions)||typeof descriptions!=='object'||Object.values(descriptions).some(v=>typeof v!=='string'))throw new Error('参数解释必须为字符串值的 JSON 对象。');
  const payload={tool_ref:d.tool_ref,description:d.description,parameter_descriptions:descriptions,enabled:d.enabled==='true',required:d.required==='true'};
  if(t)payload.base_schema=t.input_schema;else if(p?.contract_sha256)payload.contract_sha256=p.contract_sha256;
  await scopedAppend(d.scope,'tool.prompt.set',payload);
 });
}
function renderStats(){$('#stat-session').textContent=sessionData.session.name;$('#stat-session').title=sessionData.session.name;$('#stat-revision').textContent='修订 '+sessionData.revision+' · 用于防止并发覆盖';$('#stat-groups').textContent=artifact.groups.length;$('#stat-chars').textContent=fmt(artifact.chars);$('#stat-budget').textContent='预算 '+fmt(artifact.budget_chars)+' 字符';$('#stat-messages').innerHTML=artifact.model_input.messages.length+' <i>条消息</i>';$('#stat-tools').textContent=artifact.model_input.tools.length+' 个工具定义';}
function renderPreview(){$('#prompt').textContent=pretty(artifact.model_input);$('#warnings').innerHTML=artifact.warnings.map(w=>`<div class="warning">${esc(w)}</div>`).join('');}
function inputMessage(m,g){
 const record=sessionData.state.messages.find(r=>r.seq===m.seq);
 const title=m.kind==='reasoning'?'可获取的思考记录':m.role==='system'?'SYSTEM · 系统提示词':m.role==='user'?(record?.role==='user'?'USER · 用户发言':'USER · 上下文数据'):m.role==='tool'?'TOOL · 工具结果':m.tool_calls?.length?'ASSISTANT · 工具调用':'ASSISTANT · 最终输出';
 const editable=record?.role==='user'&&g.role==='conversation'&&!g.truncated;
 return `<div class="input-message role-${m.role}"><div class="input-message-heading"><strong>${esc(title)}</strong>${record?.loop_id?`<span class="tag">Loop ${esc(record.loop_id.slice(0,8))}</span>`:''}${editable?`<button class="text-button" data-edit-message="${m.seq}">编辑</button>`:''}</div>${m.name?`<div class="tool-link"><code>${esc(m.name)}</code> · ${esc(m.call_id)}</div>`:''}${m.content?`<pre class="message-text">${esc(m.content)}</pre>`:''}${(m.tool_calls||[]).map(c=>`<div class="native-call"><div><code>${esc(c.name)}</code><small>${esc(c.id)}</small></div><pre>${esc(pretty(c.arguments))}</pre></div>`).join('')}</div>`;
}
function renderGroups(){
 $('#groups').innerHTML=artifact.groups.map((source,i)=>{
 const g={...source,label:source.label==='会话与 Agent Loop'?'对话与 Agent Loop':source.label};
 const external=g.origin==='external';const tools=g.items.filter(t=>t.type==='tool');
 const catalog=tools.length?`<div class="selected-tools">${tools.map(t=>`<details class="tool-definition" open><summary><code>${esc(t.content.name)}</code><span class="origin-badge external">来源定义 · 描述可配置</span></summary><p>${esc(t.content.description)}</p><pre>${esc(pretty(t.content.input_schema))}</pre></details>`).join('')}</div>`:'';
 const messages=g.input_messages.map(m=>inputMessage(m,g)).join('');
 const body=tools.length?catalog+`<details class="transport-content"><summary>此组件对应的消息内容</summary>${messages}</details>`:messages||`<p class="empty-component">${esc(g.reason||'当前没有输入内容，可编辑组件或绑定数据。')}</p>`;
 return `<article class="group-card ${!g.enabled?'group-disabled':''}" data-component="${g.id}"><div class="group-header"><div class="group-name"><span class="drag-handle" draggable="true" data-drag="${g.id}" title="拖动排序">⠿</span><div><strong>${esc(g.label)}</strong><div class="group-id">${String(i+1).padStart(2,'0')} / ${esc(g.id)} · ${esc(g.kind)}</div></div></div><div class="group-actions"><button class="text-button" data-move="${g.id}" data-delta="-1" aria-label="上移${esc(g.label)}" ${i===0?'disabled':''}>↑</button><button class="text-button" data-move="${g.id}" data-delta="1" aria-label="下移${esc(g.label)}" ${i===artifact.groups.length-1?'disabled':''}>↓</button><button class="switch ${g.enabled?'on':''}" data-toggle="${g.id}" aria-label="${g.enabled?'禁用':'启用'}${esc(g.label)}" aria-pressed="${g.enabled}"></button></div></div><div class="component-meta"><span class="origin-badge ${external?'external':'internal'}">${external?(g.sources.includes('tools')?'执行契约来自提供方 · 描述可配置':'外部 · 内容只读'):'内部 · 可编辑'}</span><span>${roles[g.role]}</span><button class="text-button strategy" data-settings="${g.id}">${strategies[g.policy.strategy]}</button>${g.reason?`<span>${esc(g.reason)}</span>`:''}</div><details class="component-input" open><summary>模型输入 · ${esc(g.label)}</summary>${body}</details><div class="group-bottom"><span>${fmt(g.chars)} 字符 · ${g.included_count}/${g.original_count} 项</span><div><button class="text-button" data-settings="${g.id}">组件设置</button><button class="text-button" data-content="${g.id}">${external?'查看来源':g.role==='conversation'?(objectType==='template'?'运行时数据来源':'添加发言'):'编辑内容'} ↗</button></div></div></article>`;
 }).join('');
}
function renderObject(){const isTemplate=objectType==='template';activeTemplateId=isTemplate?sessionId:sessionData.binding?.template_id||null;renderTemplates();
 $('#object-label').textContent=isTemplate?'正在编辑模板':'运行上下文实例';
 $('#composition-title').textContent=isTemplate?'模板组成与默认输入':'给 AI 的上下文';
 $('#object-note').textContent=isTemplate?'模板配置会同步到本模板的所有实例。字段默认值与实例当前值分开保存；下方仍展示实际编译后的默认输入。':sessionData.binding?(sessionData.binding.origin==='assigned'?'此旧实例已归属于模板「':'此实例创建自模板「')+(templates.find(t=>t.id===sessionData.binding.template_id)?.name||sessionData.binding.template_id)+'」。实例简名：'+sessionData.binding.slug+'。'+(sessionData.binding.agent_id?' Agent 标记：'+sessionData.binding.agent_id+'。':'')+'编辑时可选择仅当前实例，或更新模板及全部实例。':'这是尚未归属模板的旧实例，请设置归属模板和简名；原有内容会保留。';
 $('#instance-info').classList.toggle('hidden',isTemplate||!sessionData.binding);$('#agent-connect').classList.toggle('hidden',isTemplate||!sessionData.binding);$('#assign-instance').classList.toggle('hidden',isTemplate||!!sessionData.binding);
 $('#add-message').classList.toggle('hidden',isTemplate);$('#rename-template').classList.toggle('hidden',!isTemplate);
 $('#use-template').classList.toggle('hidden',!isTemplate);$('#save-template').classList.toggle('hidden',isTemplate);
 $('#page-title').textContent=isTemplate?'编辑上下文模板':'运行上下文';
 $('#page-description').textContent=isTemplate?'规划模板的组成、默认提示词、字段与策略，供多个 Agent 复用。':'查看和修改这个实例实际交给 AI 的上下文。';
}
async function setView(v){view=v;$('#view-templates').classList.toggle('hidden',v!=='templates');$('#session-content').classList.toggle('hidden',v==='templates'||(!sessionData&&v==='workspace'));$('#empty').classList.add('hidden');
 ['workspace','protocol'].forEach(k=>$('#view-'+k).classList.toggle('hidden',k!==v));document.querySelectorAll('[data-view]').forEach(b=>b.classList.toggle('active',b.dataset.view===v));
 $('#breadcrumb').textContent=v==='templates'?'模板库':v==='workspace'?'上下文编排':'协议与接入';
 $('#build').classList.toggle('hidden',v!=='workspace');$('#export').classList.toggle('hidden',v!=='workspace');
 for(const id of ['#rename-template','#use-template','#save-template','#instance-info','#agent-connect','#assign-instance'])$(id).classList.add('hidden');
 renderScope();
 if(v==='templates'){activeTemplateId=null;renderTemplates();history.replaceState(null,'','/');$('#page-title').textContent='上下文模板库';$('#page-description').textContent='维护多个模板，在接入 Agent 时选择使用。';await loadTemplates();}
 else if(v==='workspace'&&sessionData)renderObject();
 else if(v==='workspace'){await setView('templates');}
 else {renderProtocol();$('#page-title').textContent='协议与接入';$('#page-description').textContent='模板定义配置，实例承载运行数据。';}
 renderScope();
}
function renderProtocol(){
 const target=objectType==='instance'&&sessionId?base():'/sessions/CONTEXT_ID';
 const bindingId=toolBindingId||'CONNECTION_ID';
 const endpoint='POST '+location.origin+'/api'+target+'/ingest';
 $('#protocol-example').textContent='GET '+location.origin+'/api/protocol\n\n'+endpoint+'\n'+pretty({protocol_version:'0.1',type:'host.open',update_id:'open-unique-id',source:'your-host',payload:{binding_id:bindingId,host_id:'your-environment'}})+'\n\n'+endpoint+'\n'+pretty({protocol_version:'0.1',type:'provider.sync',update_id:'sync-unique-id',source:'your-host',payload:{binding_id:bindingId,provider_id:'documents',kind:'mcp',status:'ready',tools:[{name:'search',description:'检索文档',input_schema:{type:'object',properties:{query:{type:'string',description:'检索关键词'}},required:['query']}}]}});
 $('#build-example').textContent='POST '+location.origin+'/api'+target+'/prepare\n\n'+pretty({binding_id:bindingId,supported_versions:['0.1'],budget_chars:24000});
 $('#state-example').textContent=pretty({type:'state.set',update_id:'state-1',payload:{key:'phase',value:'实现',value_type:'string'}});
}
function field(label,name,value='',kind='input',note=''){return `<label class="field">${label}${kind==='textarea'?`<textarea id="f-${name}" name="${name}">${esc(value)}</textarea>`:`<input id="f-${name}" name="${name}" value="${esc(value)}">`}${note?`<small>${note}</small>`:''}</label>`;}
function select(label,name,choices,value){return `<label class="field">${label}<select name="${name}">${Object.entries(choices).map(([k,v])=>`<option value="${esc(k)}" ${k===value?'selected':''}>${esc(v)}</option>`).join('')}</select></label>`;}
function dialog(title,html,save,readOnly=false){$('#dialog-title').textContent=title;$('#editor-fields').innerHTML=html;$('#form-error').textContent='';$('#save-dialog').classList.toggle('hidden',readOnly);editorSave=save;if(!$('#editor').open)$('#editor').showModal();}
function closeDialog(){$('#editor').close();editorSave=null;}
function scopeField(value='instance'){
 if(objectType==='template')return '<p class="scope-note">作用范围：当前模板及其全部实例。</p>';
 if(!sessionData.binding)return '<p class="scope-note">作用范围：仅当前实例。</p>';
 return select('作用范围','scope',{instance:'仅当前实例',template:'基于模板 · 同步全部实例'},value)+'<p class="scope-note">模板级提示词会更新所有实例中的同名内容；字段定义同步但保留各实例当前值。实例专属的其他内容会保留。</p>';
}
async function scopedAppend(scope,type,payload){
 if(objectType==='template'||scope!=='template')return append(type,payload);
 const tid=sessionData.binding.template_id;
 if(payload.group_id&&!templateState.groups.some(g=>g.id===payload.group_id))throw new Error('此组件仅存在于当前实例，请先在模板中创建同 ID 组件，再添加模板级提示词。');
 const result=await post('/templates/'+tid+'/updates',{update_id:crypto.randomUUID(),type,source:'workbench',payload,expected_revision:scopeRevision});savedScope='template';return result;
}
let scopeRevision=null, savedScope=null;
function captureScopeRevision(){scopeRevision=templateRevision;}
function fieldDefinition(key){return sessionData.state.field_definitions?.[key]||sessionData.state.fields[key];}
function ownership(collection,key){
 if(objectType==='template')return '模板共享';
 const shared=templateState?.[collection]?.[key];
 if(!shared)return '仅此实例';
 const local=sessionData.state[collection][key];
 if(collection==='field_definitions')return pretty(shared)===pretty(local)?'模板共享':'实例定义覆盖';
 return ['content','group_id','schema','source_uri','origin'].every(name=>pretty(shared[name])===pretty(local[name]))?'模板共享':'实例内容覆盖';
}
function renderStatePanel(){
 const entries=Object.entries(sessionData.state.field_definitions||{});
 $('#state-panel').innerHTML=`<div class="section-heading"><div><h2>状态定义与当前状态 <span class="tag">STATE</span></h2><p>定义、默认值与运行值在这里管理；它们在下方状态组件中的输入格式和位置保持原样。</p></div><button class="button primary" data-field="">＋ 新建状态</button></div><div class="instance-table-wrap"><table class="instance-table state-table"><thead><tr><th>字段定义 / 说明</th><th>归属</th><th>默认值</th><th>${objectType==='template'?'默认输入值':'当前状态'}</th><th>操作</th></tr></thead><tbody>${entries.map(([key,f])=>`<tr><td><code>${esc(key)}</code><small>${esc(f.value_type)}${f.description?' · '+esc(f.description):''}</small></td><td><span class="tag">${ownership('field_definitions',key)}</span></td><td><pre>${esc(pretty(f.value))}</pre></td><td><pre>${esc(pretty(sessionData.state.fields[key]?.value))}</pre></td><td><button class="text-button" data-field="${esc(key)}">编辑定义</button>${objectType==='instance'?`<button class="text-button" data-field-value="${esc(key)}">修改当前值</button>`:''}<button class="text-button danger" data-remove-field="${esc(key)}">删除</button></td></tr>`).join('')||'<tr><td colspan="5" class="muted">还没有状态。可新增字段定义，Agent 也可通过内置工具创建实例字段。</td></tr>'}</tbody></table></div>`;
}
function componentDialog(id=null){
 captureScopeRevision();
  const g=id?group(id):{id:'',label:'',kind:'text',role:'context',origin:'internal',sources:['blocks','items'],enabled:true,policy:{strategy:'keep',limit:12,max_chars:6000}};
  dialog(id?'组件设置 · '+g.label:'新建上下文组件',scopeField()+field('组件 ID','id',g.id)+field('显示名称','label',g.label)+'<div class="field-row">'+field('组件类型（可自定义）','kind',g.kind)+select('消息角色','role',roles,g.role)+'</div>'+field('数据绑定（逗号分隔，可增减）','sources',g.sources.join(', '),'input',Object.entries(sourceNames).map(([k,v])=>k+' = '+v).join('；'))+select('压缩策略','strategy',strategies,g.policy.strategy)+select('最近 N 的保留单位','unit',{auto:'自动（对话按轮，其余按项）',item:'条目',turn:'完整轮次',loop:'循环'},g.policy.unit||'auto')+'<div class="field-row">'+field('最近项数','limit',g.policy.limit)+field('截断字符数','max_chars',g.policy.max_chars)+'</div>'+`<p class="dialog-note">来源归属：${g.origin==='external'?'外部，只读内容可由提供方同步':'内部，可编辑内容'}。排序、标签、绑定、角色和构建策略可配置。</p>`+(id?`<button type="button" class="text-button danger" data-delete-component="${g.id}">移除此组件（原始内容保留，可重新绑定）</button>`:''),async d=>{if(id&&d.id!==id)throw new Error('已有组件 ID 不能修改，请新建组件。');if(!id&&group(d.id))throw new Error('组件 ID 已存在。');await scopedAppend(d.scope,'group.upsert',{id:d.id,label:d.label,kind:d.kind,role:d.role,origin:g.origin,enabled:g.enabled,sources:d.sources.split(',').map(s=>s.trim()).filter(Boolean),policy:{strategy:d.strategy,unit:d.unit||'auto',limit:Number(d.limit),max_chars:Number(d.max_chars)}});});
  if(id)$('#f-id').readOnly=true;
}
function contentDialog(id){
  const g=group(id);if(g.origin==='external'||g.sources.includes('tools'))return showExternal(id);
  if(g.sources.includes('fields'))return manageFields();
  if(g.role==='conversation')return objectType==='template'?notify('对话由 Agent 写入实例；模板中可配置此组件的来源、位置与压缩策略。'):messageDialog();
  const items=[...Object.values(sessionData.state.blocks).map(i=>({...i,collection:'block'})),...Object.values(sessionData.state.items).map(i=>({...i,collection:'item'}))].filter(i=>i.group_id===id);
  dialog('内容 · '+g.label,items.map(i=>`<div class="field-entry"><code>${esc(i.id)}</code><small>${ownership(i.collection==='block'?'blocks':'items',i.id)}</small><span>${esc(typeof i.content==='string'?i.content:JSON.stringify(i.content))}</span>${i.origin==='external'?'<small>外部只读</small>':`<button type="button" class="text-button" data-edit-item="${esc(i.id)}" data-collection="${i.collection}">编辑</button><button type="button" class="text-button danger" data-delete-item="${esc(i.id)}" data-collection="${i.collection}">删除</button>`}</div>`).join('')+`<button type="button" class="button" data-new-item="${id}" style="margin-top:16px">＋ 添加内容</button>`,null,true);
}
function showExternal(id){const g=group(id),items=g.sources.includes('tools')?Object.values(sessionData.state.tools):[...Object.values(sessionData.state.items),...Object.values(sessionData.state.blocks)].filter(i=>i.group_id===id);dialog('外部来源 · '+g.label,'<p class="dialog-note">此处展示提供方同步的数据，编辑端不能覆盖内容。可配置组件顺序、启用状态与策略。</p><pre>'+esc(pretty(items))+'</pre>',null,true);}
function editItem(groupId,itemId=null,collection='item'){
  captureScopeRevision();
  const old=itemId?sessionData.state[collection==='block'?'blocks':'items'][itemId]:null;
  if(old?.origin==='external')throw new Error('外部条目只读。');
  const gid=old?.group_id||groupId;
  if(!old&&!group(gid).sources.includes('items')&&group(gid).sources.includes('blocks'))collection='block';
  dialog(itemId?'编辑内容':'添加提示词',scopeField()+field('条目 ID','id',itemId||'item_'+Date.now())+select('内容格式','format',{text:'文本',json:'结构化 JSON'},typeof old?.content==='object'?'json':'text')+field('内容','content',old?(typeof old.content==='string'?old.content:pretty(old.content)):'','textarea')+field('结构化 Schema（可选 JSON）','schema',old?.schema?pretty(old.schema):'','textarea'),async d=>{if(itemId&&d.id!==itemId)throw new Error('已有条目 ID 不能修改。');const payload={id:d.id,group_id:gid,content:d.format==='json'?JSON.parse(d.content):d.content,origin:'internal'};if(d.schema.trim())payload.schema=JSON.parse(d.schema);if(collection==='block'&&d.format==='json')throw new Error('文本块需保留文本格式；请新建结构化条目。');await scopedAppend(d.scope,collection==='block'?'block.upsert':'item.upsert',payload);});
  if(itemId)$('#f-id').readOnly=true;
}
function manageFields(){document.querySelector('#state-panel').scrollIntoView({behavior:'smooth',block:'start'});}
function editField(key=''){
 captureScopeRevision();
 const f=fieldDefinition(key)||{value:'',value_type:'string',description:''};
 dialog(key?'编辑状态定义':'新建状态定义',scopeField()+field('字段名','key',key)+select('类型','value_type',{string:'字符串',number:'数字',boolean:'布尔值',object:'对象',array:'数组',json:'JSON'},f.value_type)+field('默认值','value',f.value_type==='string'?f.value:pretty(f.value),'textarea','已有实例保留当前值；新增字段以此值初始化。')+field('说明','description',f.description),async d=>{
 if(key&&d.key!==key)throw new Error('不能修改字段名。');
 await scopedAppend(d.scope,'state.define',{key:d.key,value_type:d.value_type,description:d.description,value:d.value_type==='string'?d.value:JSON.parse(d.value)});
 });if(key)$('#f-key').readOnly=true;
}
function editFieldValue(key){const f=sessionData.state.fields[key];dialog('修改当前状态 · '+key,'<p class="scope-note">仅修改当前实例的运行值，字段定义和模板默认值保持不变。</p>'+field('当前值','value',f.value_type==='string'?f.value:pretty(f.value),'textarea'),async d=>{await append('state.set',{key,value_type:f.value_type,description:f.description,value:f.value_type==='string'?d.value:JSON.parse(d.value)});});}
function deleteScoped(type,payload,label){captureScopeRevision();dialog('删除 · '+label,scopeField()+'<p class="dialog-note">模板级删除会移除全部实例中的同名字段、提示词或组件；实例级删除仅影响当前实例。组件删除保留原始内容，可重新绑定。</p>',async d=>{await scopedAppend(d.scope,type,payload);});}
function promptDialog(){const options=sessionData.state.groups.filter(g=>g.origin==='internal'&&g.role!=='conversation'&&g.sources.some(s=>s==='items'||s==='blocks'));if(!options.length)return notify('请先新建一个绑定文本块或结构化条目的内部组件。');dialog('新增提示词 · 选择组件','<p class="dialog-note">选择提示词的目标位置，随后编辑内容与作用范围。</p>'+options.map(g=>`<div class="field-entry"><code>${esc(g.id)}</code><span>${esc(g.label)}</span><button type="button" class="button" data-new-item="${g.id}">添加到此组件</button></div>`).join(''),null,true);}

function messageDialog(messageSeq=null){const m=messageSeq?sessionData.state.messages.find(m=>m.seq===messageSeq):null;dialog(m?'编辑上下文中的用户发言':'添加用户发言',field('用户发言','content',m?.content||'','textarea'),async d=>{if(m)await append('message.update',{message_seq:m.seq,content:d.content});else await append('message.append',{role:'user',content:d.content},crypto.randomUUID());});}
async function reorder(id,target){const ids=sessionData.state.groups.map(g=>g.id),from=ids.indexOf(id);if(from<0||target<0||target>=ids.length||from===target)return;ids.splice(target,0,ids.splice(from,1)[0]);await append('group.reorder',{ids});await refresh();}
$('#editor-form').addEventListener('submit',async e=>{e.preventDefault();if(!editorSave||loading)return;loading=true;$('#save-dialog').disabled=true;$('#form-error').textContent='';try{savedScope=objectType;await editorSave(Object.fromEntries(new FormData(e.target)));closeDialog();await refresh();await Promise.all([loadSessions(),loadTemplates()]);notify((savedScope==='template'?'已保存模板并同步全部实例':'已保存当前实例')+' · '+databaseLabel);}catch(error){$('#form-error').textContent=error.message;}finally{loading=false;$('#save-dialog').disabled=false;}});
bind('#close-dialog',closeDialog);bind('#cancel-dialog',closeDialog);
function newTemplateDialog(sourceTemplateId=null,sourceContextId=null){
 const original=templates.find(t=>t.id===sourceTemplateId);
 dialog(sourceContextId?'将实例配置另存为模板':original?'复制模板':'新建上下文模板',field('模板名称','name',original?original.name+' · 副本':'新的模板')+field('用途说明','description',original?.description||'','textarea')+(sourceContextId?'<p class="dialog-note">保留组件、内部提示词与字段结构；字段值重置为空值，不复制对话、工具结果、记忆或外部定义。保存后可设置需要的字段默认值。</p>':''),async d=>{const result=await post('/templates',{...d,source_template_id:sourceTemplateId,source_context_id:sourceContextId});await loadTemplates();await selectTemplate(result.template.id);});
}
function useTemplateDialog(tid=null){
 const selected=tid||activeTemplateId||templates[0]?.id;if(!selected)return notify('请先创建模板。');
 const template=templates.find(t=>t.id===selected);const rows=sessions.filter(s=>s.template_id===selected);const suggested=rows.length?'run-'+(rows.length+1):'main';
 dialog('新建实例 · '+template.name,field('实例简名','slug',suggested,'input','Agent 通过模板 ID + 此简名接入。同一模板内唯一，例如 main、researcher；仅小写字母、数字、下划线、连字符。')+field('实例名称','name',template.name+' · '+suggested)+field('Agent 标记（可选）','agent_id','','input','说明使用方，例如 researcher；这不是认证凭据。'),async d=>{const result=await post('/templates/'+selected+'/instances',{...d,agent_id:d.agent_id.trim()||null});await selectSession(result.id);});
}
function assignInstanceDialog(sid){const current=sessions.find(s=>s.id===sid);dialog('设置归属模板',select('归属模板','template_id',Object.fromEntries(templates.map(t=>[t.id,t.name])),activeTemplateId||templates[0]?.id)+field('实例简名','slug','legacy-'+sid.slice(0,8))+field('Agent 标记（可选）','agent_id',current?.agent_id||'')+'<p class="dialog-note">只设置归属与简名，不替换旧实例的提示词、字段或工具记录。</p>',async d=>{await post('/sessions/'+sid+'/assign',{...d,agent_id:d.agent_id.trim()||null});await selectSession(sid);});}
function instanceInfoDialog(){const binding=sessionData.binding;dialog('实例信息',field('实例简名','slug',binding.slug,'input','修改简名后，接入方的配置需要同步更新。')+field('实例名称','name',sessionData.session.name)+field('Agent 标记（可选）','agent_id',binding.agent_id||''),async d=>{await post(base()+'/metadata',{...d,agent_id:d.agent_id.trim()||null,expected_revision:sessionData.revision});});}
async function connectionDialog(sid){const current=sid===sessionId&&objectType==='instance'?sessionData:await api('/sessions/'+sid);const b=current.binding;if(!b?.slug)throw new Error('请先为实例设置归属模板和简名。');
 const code='from context_protocol.client import ContextClient\n\ncontext = ContextClient.connect(\n    http,\n    template_id='+JSON.stringify(b.template_id)+',\n    slug='+JSON.stringify(b.slug)+',\n    create=False,\n)\n\n# 将 context 传给你的 Agent 适配器；后续调用会使用同一实例。';
 const request='POST '+location.origin+'/api/templates/'+b.template_id+'/instances/connect\n'+pretty({slug:b.slug,create:false});
 dialog('Agent 接入 · '+b.slug,'<p class="dialog-note">使用模板 ID 与实例简名定位。下面的 create=False 只连接已有实例，避免误建。http 的 base_url 设置为 '+esc(location.origin)+'/api。</p><pre class="connect-code">'+esc(code)+'</pre><pre class="connect-code">'+esc(request)+'</pre>',null,true);
}
bind('#new-template',()=>newTemplateDialog());bind('#new-template-side',()=>newTemplateDialog());
bind('#instance-info',instanceInfoDialog);bind('#agent-connect',()=>connectionDialog(sessionId));bind('#assign-instance',()=>assignInstanceDialog(sessionId));
bind('#demo',()=>setView('templates'));bind('#use-template',()=>useTemplateDialog(sessionId));
bind('#save-template',()=>newTemplateDialog(null,sessionId));
bind('#rename-template',()=>dialog('模板信息',field('模板名称','name',sessionData.session.name)+field('用途说明','description',sessionData.session.description||'','textarea'),async d=>{await post(base()+'/metadata',{...d,expected_revision:sessionData.revision});}));
bind('#add-prompt',promptDialog);bind('#add-group',()=>componentDialog());bind('#add-message',()=>messageDialog());
bind('#build',async()=>{await refresh();notify('已按当前配置重新编译');});
bind('#export',async()=>{const data=await api(base()+'/export'+(objectType==='instance'&&toolBindingId?'?binding_id='+encodeURIComponent(toolBindingId):''));const url=URL.createObjectURL(new Blob([pretty(data)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=(objectType==='template'?'template-':'context-')+sessionId+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
bind('#copy-prompt',async()=>{await navigator.clipboard.writeText(pretty(artifact.model_input));notify('已复制模型输入');});
$('#budget').addEventListener('change',()=>refresh().catch(report));
document.addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;Promise.resolve().then(async()=>{const d=b.dataset;
 if(d.connectInstance)await connectionDialog(d.connectInstance);else if(d.assignInstance)assignInstanceDialog(d.assignInstance);else if(d.editTemplate)await selectTemplate(d.editTemplate);else if(d.useTemplate)useTemplateDialog(d.useTemplate);else if(d.duplicateTemplate)newTemplateDialog(d.duplicateTemplate);else if(d.session)await selectSession(d.session);else if(d.view)await setView(d.view);else if(d.settings)componentDialog(d.settings);else if(d.content)contentDialog(d.content);
 else if(d.move){const i=sessionData.state.groups.findIndex(g=>g.id===d.move);await reorder(d.move,i+Number(d.delta));}
 else if(d.toggle){await append('group.upsert',{...group(d.toggle),enabled:!group(d.toggle).enabled});await refresh();}
 else if(d.deleteComponent)deleteScoped('group.delete',{id:d.deleteComponent},d.deleteComponent);
 else if(d.newItem)editItem(d.newItem);else if(d.editItem)editItem(null,d.editItem,d.collection);else if(d.deleteItem)deleteScoped(d.collection==='block'?'block.delete':'item.delete',{id:d.deleteItem},d.deleteItem);
 else if(d.field!==undefined)editField(d.field);else if(d.removeField)deleteScoped('state.delete',{key:d.removeField},d.removeField);
 else if(d.fieldValue)editFieldValue(d.fieldValue);
 else if(d.editMessage)messageDialog(Number(d.editMessage));
 else if(d.newToolPrompt)editToolPrompt();else if(d.toolPrompt)editToolPrompt(d.toolPrompt);
 else if(d.deleteToolPrompt)deleteScoped('tool.prompt.delete',{id:d.deleteToolPrompt},d.deleteToolPrompt);
 }).catch(report);});
$('#groups').addEventListener('dragstart',e=>{dragId=e.target.dataset.drag;if(!dragId)e.preventDefault();});
$('#groups').addEventListener('dragover',e=>{if(dragId)e.preventDefault();});
$('#groups').addEventListener('drop',e=>{e.preventDefault();const target=e.target.closest('[data-component]')?.dataset.component;if(dragId&&target)reorder(dragId,sessionData.state.groups.findIndex(g=>g.id===target)).catch(report);dragId=null;});
$('#groups').addEventListener('dragend',()=>{dragId=null;});
init();
