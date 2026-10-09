const $ = id => document.getElementById(id);
const widgets = {
  ai_list: ['AI 会话', '✦', 4], hw_cards: ['硬件四卡', '▦', 4],
  clock: ['时钟', '◷', 1], environment: ['温湿度', '◉', 1],
  reminder_list: ['提醒列表', '☷', 2], today_calendar: ['今日日历', '▣', 2],
  alarm: ['闹钟', '◴', 1], weather: ['天气', '☁', 1], countdown: ['倒计时', '⏳', 1],
  ai_cost: ['AI 用量', '∑', 1]
};
const stateNames = {THINKING:'思考中',GENERATING:'生成中',TOOL:'执行工具',BUILD:'构建中',DONE:'已完成',ERROR:'出错',WAIT_INPUT:'等你回复',ASK:'等待确认'};
// 主题预设：card 与 bg 均已拉开亮度差（>=1.5:1），sub 均可读。
// 修改配色时务必让 profile.validate_profile 的三项对比度校验通过。
const themePresets = {
  '深蓝科技':{bg:'0B1220',grad:'0E1B33',card:'274064',card_edge:'3E506E',accent:'22D3EE',text:'F3F7FF',sub:'A8BDD9'},
  '紫夜':{bg:'120B22',grad:'1D1136',card:'3A2D5C',card_edge:'54427F',accent:'A78BFA',text:'F5F3FF',sub:'C4B5E8'},
  '苍翠':{bg:'071A12',grad:'0C2A1B',card:'1D4A33',card_edge:'2A6B4A',accent:'34D399',text:'ECFDF5',sub:'A3D5BC'},
  '暖阳':{bg:'1A1207',grad:'2A1C0C',card:'4A3418',card_edge:'6B4C24',accent:'F59E0B',text:'FFF8EB',sub:'DEC9A3'},
  '玫瑰':{bg:'1A0B12',grad:'2A1120',card:'6E3E5E',card_edge:'8F4566',accent:'FB7185',text:'FFF1F3',sub:'E8B4C0'}
};
let draft = null, saved = null, live = {tasks:[],telemetry:{},device:'等待设备'};
let selected = 0, dirty = false, dragPage = -1, dragWidget = -1, toastTimer = null;


// ---------- 主题画廊：8 款程序绘制背景（一键推送+配色） ----------
function srng(seed){let s=seed;return()=>{s=(s*9301+49297)%233280;return s/233280}};
const galleryThemes = {
  aurora:{name:'极光',card_opa:70,colors:{bg:'060A14',grad:'0B1428',card:'13223C',card_edge:'1E3A5F',accent:'5EEAD4',text:'E9FFFA',sub:'8FB6D9'}},
  synthwave:{name:'赛博霓虹',card_opa:70,colors:{bg:'0D0221',grad:'1A0533',card:'260945',card_edge:'7B2CBF',accent:'F72585',text:'FFF8FE',sub:'B39DDB'}},
  dusk:{name:'山峦黄昏',card_opa:65,colors:{bg:'221028',grad:'40203A',card:'33203F',card_edge:'7A4A63',accent:'FF9E64',text:'FFF3E9',sub:'D4A88F'}},
  starry:{name:'星空',card_opa:70,colors:{bg:'050914',grad:'0A1230',card:'101C3C',card_edge:'27408B',accent:'8AB8FF',text:'EEF4FF',sub:'8FA3C9'}},
  garden:{name:'像素花园',card_opa:70,colors:{bg:'0C1810',grad:'122418',card:'1A3322',card_edge:'2E5C40',accent:'A3E635',text:'F0FDF0',sub:'94C9A3'}},
  waves:{name:'深海波浪',card_opa:70,colors:{bg:'03121E',grad:'062033',card:'0A2A42',card_edge:'1D5C7A',accent:'22D3EE',text:'E8FBFF',sub:'89B8C9'}},
  ink:{name:'墨韵',card_opa:88,colors:{bg:'F2EFE9',grad:'E8E4DA',card:'FFFFFF',card_edge:'C9C2B4',accent:'B03A2E',text:'2B2B2B',sub:'6B6B60'}},
  ember:{name:'熔岩余烬',card_opa:70,colors:{bg:'0F0505',grad:'1F0808',card:'2A0E0E',card_edge:'6B2020',accent:'FF6B35',text:'FFF0E8',sub:'C9947E'}}
};
function drawGallery(ctx,w,h,key){
  const R=srng(key.length*1337+42);
  const lg=ctx.createLinearGradient(0,0,0,h);
  const C=galleryThemes[key].colors;
  lg.addColorStop(0,'#'+C.bg); lg.addColorStop(1,'#'+C.grad);
  ctx.fillStyle=lg; ctx.fillRect(0,0,w,h);
  if(key==='aurora'||key==='starry'){
    for(let i=0;i<140;i++){ctx.globalAlpha=.2+R()*.7;ctx.fillStyle='#FFF';
      ctx.fillRect(R()*w,R()*h,R()>.9?2:1,R()>.9?2:1)}
    ctx.globalAlpha=1;
    const blob=(x,y,r,c)=>{const g=ctx.createRadialGradient(x,y,0,x,y,r);
      g.addColorStop(0,c);g.addColorStop(1,'transparent');ctx.fillStyle=g;
      ctx.fillRect(x-r,y-r,r*2,r*2)};
    if(key==='aurora'){blob(w*.7,h*.25,w*.8,'rgba(94,234,212,.16)');blob(w*.3,h*.35,w*.7,'rgba(167,139,250,.14)');blob(w*.5,h*.15,w*.6,'rgba(94,234,212,.10)')}
    else{blob(w*.3,h*.3,w*.7,'rgba(138,184,255,.10)');blob(w*.7,h*.6,w*.6,'rgba(167,139,250,.08)')}
  }
  if(key==='synthwave'){
    const g=ctx.createRadialGradient(w/2,h*.34,10,w/2,h*.34,h*.28);
    g.addColorStop(0,'#FFD319');g.addColorStop(.7,'#FF2975');g.addColorStop(1,'transparent');
    ctx.fillStyle=g;ctx.fillRect(0,0,w,h*.5);
    ctx.strokeStyle='rgba(247,37,133,.5)';ctx.lineWidth=1;
    for(let i=0;i<12;i++){const y=h*.52+i*i*1.1+8;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}
    for(let i=-6;i<=6;i++){ctx.beginPath();ctx.moveTo(w/2+i*8,h*.52);ctx.lineTo(w/2+i*w*.16,h);ctx.stroke()}
  }
  if(key==='dusk'){
    ctx.fillStyle='rgba(255,158,100,.35)';ctx.fillRect(0,h*.3,w,h*.25);
    const layers=['rgba(40,16,40,.55)','rgba(28,10,30,.75)','#160818'];
    layers.forEach((c,li)=>{ctx.fillStyle=c;ctx.beginPath();ctx.moveTo(0,h);
      const base=h*.55+li*h*.09;
      for(let x=0;x<=w;x+=w/8){const pk=base-R()*(h*.14);ctx.lineTo(x,pk);ctx.lineTo(x+w/16,base)}
      ctx.lineTo(w,h);ctx.fill()});
  }
  if(key==='garden'){
    const cols=['#F472B6','#FBBF24','#A78BFA','#5EEAD4'];
    for(let i=0;i<26;i++){const x=R()*w,y=h*.55+R()*h*.45,c=cols[i%4];
      ctx.fillStyle='#2E5C40';ctx.fillRect(x-1,y,3,10);
      ctx.fillStyle=c;ctx.fillRect(x-4,y-6,4,4);ctx.fillRect(x+1,y-6,4,4);
      ctx.fillRect(x-4,y-2,4,4);ctx.fillRect(x+1,y-2,4,4);
      ctx.fillStyle='#FFF3';ctx.fillRect(x-1,y-3,3,3)}
    for(let i=0;i<20;i++){ctx.globalAlpha=.3+R()*.5;ctx.fillStyle='#D9F99D';
      ctx.fillRect(R()*w,R()*h*.5,2,2)}
    ctx.globalAlpha=1;
  }
  if(key==='waves'){
    ctx.strokeStyle='rgba(232,251,255,.25)';ctx.lineWidth=2;
    for(let i=0;i<8;i++){ctx.beginPath();
      for(let x=0;x<=w;x+=8){const y=h*.2+i*h*.09+Math.sin(x*.03+i*1.7)*12;
        x===0?ctx.moveTo(x,y):ctx.lineTo(x,y)}
      ctx.stroke()}
    for(let i=0;i<24;i++){ctx.globalAlpha=.15+R()*.3;ctx.fillStyle='#A5F3FC';
      ctx.beginPath();ctx.arc(R()*w,h*.55+R()*h*.45,1+R()*2.5,0,7);ctx.fill()}
    ctx.globalAlpha=1;
  }
  if(key==='ink'){
    ctx.fillStyle='rgba(43,43,43,.14)';
    const layers=[[.62,.18],[.72,.26],[.84,.36]];
    layers.forEach(([base,amp],li)=>{ctx.globalAlpha=.35+li*.2;
      ctx.beginPath();ctx.moveTo(0,h);ctx.lineTo(0,h*base);
      for(let x=0;x<=w;x+=w/10){const pk=h*base-R()*h*amp;ctx.lineTo(x,pk);ctx.lineTo(x+w/20,h*base)}
      ctx.lineTo(w,h);ctx.fill()});
    ctx.globalAlpha=1;ctx.fillStyle='#B03A2E';ctx.fillRect(w-52,h-64,26,26);
  }
  if(key==='ember'){
    for(let i=0;i<60;i++){ctx.globalAlpha=.15+R()*.6;
      ctx.fillStyle=R()>.4?'#FF6B35':'#FFB86B';
      ctx.beginPath();ctx.arc(R()*w,h-R()*h*.8,1+R()*3,0,7);ctx.fill()}
    ctx.globalAlpha=1;ctx.strokeStyle='rgba(255,107,53,.25)';
    for(let i=0;i<6;i++){ctx.beginPath();ctx.moveTo(R()*w,h);
      ctx.lineTo(R()*w,h*.3+R()*h*.3);ctx.stroke()}
  }
}

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}
function notice(message, bad=false) {
  const box = $('message'); box.textContent = message;
  box.className = `message show${bad?' error':''}`;
  clearTimeout(toastTimer); toastTimer = setTimeout(() => box.className='message', 4200);
}
function markDirty() {
  dirty = true;
  localStorage.setItem('workbuddy-draft', JSON.stringify({base:saved?.version,draft}));
  $('sync-badge').textContent = '未保存草稿'; $('sync-badge').className = 'badge warn';
  render();
}
function current() { return draft.pages[selected] || draft.pages[0]; }
function uniqueId(prefix) { return `${prefix}${Date.now().toString(36).slice(-6)}${Math.random().toString(36).slice(2,5)}`.slice(0,16); }
function reorder(list, from, to) {
  if (from < 0 || to < 0 || from === to) return;
  const [item] = list.splice(from,1); list.splice(to,0,item); markDirty();
}
async function request(path, body) {
  const response = await fetch(path, body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

function renderPages() {
  const root=$('pages-list'); root.replaceChildren();
  draft.pages.forEach((page,i) => {
    const b=el('button',`page-item${i===selected?' active':''}`); b.draggable=true;
    b.append(el('span','page-grip','⠿'),el('span','page-number',String(i+1).padStart(2,'0')),el('strong','',page.title+(i===0?' · 首页':'')),el('small','',`${page.widgets.length} 组件`));
    b.onclick=()=>{selected=i;render()};
    b.ondragstart=()=>{dragPage=i;b.classList.add('dragging')};
    b.ondragend=()=>{dragPage=-1;b.classList.remove('dragging')};
    b.ondragover=e=>e.preventDefault();
    b.ondrop=e=>{e.preventDefault();reorder(draft.pages,dragPage,i);selected=i};
    root.append(b);
  });
  $('preview-page-number').textContent=`${selected+1} / ${draft.pages.length}`;
}
function renderPalette() {
  const root=$('widget-palette');root.replaceChildren();
  const used=current().widgets.reduce((n,w)=>n+widgets[w][2],0);
  Object.entries(widgets).forEach(([key,[name,icon,units]])=>{
    const b=el('button','palette-item');b.append(el('span','palette-symbol',icon),document.createTextNode(name));
    b.disabled=current().widgets.includes(key)||used+units>4||(units===4&&used>0)||(used===4);
    b.onclick=()=>{current().widgets.push(key);markDirty()}; root.append(b);
  });
}
function renderInspector() {
  const page=current();$('page-title').value=page.title;$('page-layout').value=page.layout;
  const root=$('selected-widgets');root.replaceChildren();
  page.widgets.forEach((key,i)=>{
    const chip=el('div','widget-chip');chip.draggable=true;
    chip.append(el('span','grip','⠿'),el('span','',widgets[key][0]));
    const remove=el('button','', '×');remove.title='移除组件';remove.onclick=()=>{
      if(page.widgets.length===1){notice('页面至少保留一个组件',true);return}
      page.widgets.splice(i,1);markDirty()
    };chip.append(remove);
    chip.ondragstart=()=>{dragWidget=i;chip.classList.add('dragging')};
    chip.ondragend=()=>{dragWidget=-1;chip.classList.remove('dragging')};
    chip.ondragover=e=>e.preventDefault();chip.ondrop=e=>{e.preventDefault();reorder(page.widgets,dragWidget,i)};
    root.append(chip);
  });
  $('weather-city').value=(draft.weather&&draft.weather.city)||'';
  $('idle-dim').value=draft.theme.idledim??60;$('idle-dim-val').textContent=(draft.theme.idledim??60)==0?'关闭':(draft.theme.idledim+' 秒');
  $('topclock-toggle').checked=!!Number(draft.theme.topclock??1);
  $('topwx-toggle').checked=!!Number(draft.theme.topwx??1);
  $('bright-auto').checked=!!Number(draft.theme.bright_auto??0);
  $('card-opa').value=draft.theme.card_opa??100;$('opa-value').textContent=(draft.theme.card_opa??100)+'%';
  $('saver-style').value=draft.theme.saver??0;
  $('saver-msg').value=draft.theme.savermsg||'';
  for(const key of ['bg','grad','card','accent','text']) $('theme-'+key).value='#'+draft.theme[key];
  $('theme-bright').value=draft.theme.bright;$('bright-value').textContent=draft.theme.bright+'%';
}
function card(overline,value,sub,wide=false){
  const n=el('div',`screen-card${wide?' wide':''}`);
  n.append(el('div','overline',overline),el('div','hero-value',String(value)),el('div','subline',sub));return n;
}
function clock(){return new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
function previewWidget(key, container) {
  const t=live.telemetry||{}, tasks=live.tasks||[];
  if(key==='ai_list'){
    const top=tasks[0];
    const focus=card(top?`${top.tool.toUpperCase()} · ${top.project||'未命名项目'} · #${top.id?.slice(0,4)}`:'AI 会话',
      top?(stateNames[top.state]||top.state):'暂无任务',top?`已运行 ${Math.floor(top.elapsed/60)} 分 · ${top.age} 秒前更新 · 日志推断`:'连接电脑后显示当前会话');
    focus.classList.add('ai-focus');container.append(focus);
    const others=el('div','mini-tasks');
    tasks.slice(1,4).forEach(x=>{const row=el('div','mini-task');row.append(el('span','',`${x.tool} · ${x.project||'未命名'} #${x.id?.slice(0,4)}`),el('em','',stateNames[x.state]||x.state));others.append(row)});
    if(!others.childElementCount)others.append(el('div','mini-task','其他会话会出现在这里'));
    container.append(others);return;
  }
  if(key==='hw_cards'){
    const grid=el('div','screen-grid');
    grid.append(card('处理器',t.cpu===undefined?'—':`${t.cpu}%`,'即时负载'),
      card('内存',t.mem===undefined?'—':`${t.mem}%`,t.memtotal?`${t.memused} / ${t.memtotal} GB`:'数据不可用'),
      card('磁盘空间',t.disk===undefined?'—':`${t.disk}%`,t.diskfree?`可用 ${t.diskfree} GB`:'数据不可用'),
      card('显卡',t.gpu===undefined?'未提供':`${t.gpu}%`,'即时负载'));
    container.append(grid);return;
  }
  if(key==='clock')container.append(card('本地时间',clock(),'电脑时间同步'));
  if(key==='environment')container.append(card('桌面环境',t.temp===undefined?'-- ℃':`${t.temp} ℃`,t.hum===undefined?'湿度不可用':`湿度 ${t.hum}%`));
  if(key==='reminder_list'){
    const items=draft.reminders.filter(x=>x.enabled).slice(0,2);
    container.append(card('提醒列表',items[0]?`${items[0].time} ${items[0].text}`:'未设置',items[1]?`${items[1].time} ${items[1].text}`:'在下方添加提醒',true));
  }
  if(key==='today_calendar'){
    const today=new Date().toLocaleDateString('en-CA');const items=draft.calendar.filter(x=>x.enabled&&x.date===today).slice(0,2);
    container.append(card('今日日历',items[0]?`${items[0].time} ${items[0].title}`:'今天无日程',items[1]?`${items[1].time} ${items[1].title}`:'仅显示当天条目',true));
  }
  if(key==='alarm'){
    const item=draft.alarms.find(x=>x.enabled);container.append(card('闹钟',item?item.time:'未设置',item?item.label:'到点屏幕提醒'));
  }
  if(key==='weather'){
    const w=live.telemetry||{};
    container.append(card('天气',w.wx_temp===undefined?'-- ℃':`${w.wx_temp} ℃`,'在工作台输入城市后显示'));
  }
  if(key==='countdown'){
    const item=(draft.countdowns||[]).find(x=>x.enabled);
    container.append(card('倒计时',item?`${item.date} ${item.time}`:'未设置',item?item.title:'添加目标日期'));
  }
  if(key==='ai_cost'){
    const t=live.telemetry||{};
    container.append(card('AI 用量',t.ait===undefined?'--':`今日 ${t.ait.split(',')[0]}k tok`,
      t.aiu?t.aiu.split(',').slice(0,2).join(' · '):'AI 会话活动后显示'));
  }
}
function renderPreview(){
  const page=current(),screen=$('screen'),body=$('screen-body');
  screen.style.background='#'+draft.theme.bg;screen.style.color='#'+draft.theme.text;
  $('screen-title').textContent=page.title;$('screen-clock').textContent=clock();
  $('screen-connection').textContent=live.device?.startsWith('connected:')?'设备在线':'设备离线';
  $('screen-source').textContent=live.tasks?.length?'实时会话预览':'配置预览';
  body.replaceChildren();
  if(page.widgets.length===1&&['ai_list','hw_cards'].includes(page.widgets[0]))previewWidget(page.widgets[0],body);
  else {const grid=el('div','screen-grid');page.widgets.forEach(key=>previewWidget(key,grid));body.append(grid)}
  screen.style.setProperty('--accent','#'+draft.theme.accent);
  const dots=$('screen-dots');dots.replaceChildren();draft.pages.forEach((_,i)=>dots.append(el('i',i===selected?'active':'')));
}
function renderTasks(){
  const root=$('task-list');root.replaceChildren();const tasks=live.tasks||[];
  $('task-count').textContent=`${tasks.length} 个会话`;
  if(!tasks.length){root.append(el('div','empty','暂无活跃会话。连接 Claude Code、Codex 或 ZCode 后将在这里显示。'));return}
  tasks.forEach(task=>{
    const item=el('div','task-item');const symbol=el('span','task-symbol',task.tool?.slice(0,1).toUpperCase());
    const main=el('div','task-item-main');main.append(el('strong','',`${task.project||'未命名项目'} · ${task.tool}`),el('small','',`会话 #${task.id} · ${task.age} 秒前更新`));
    item.append(symbol,main,el('span',`task-state${['ASK','ERROR','WAIT_INPUT'].includes(task.state)?' alert':''}`,stateNames[task.state]||task.state));root.append(item);
  });
}
function renderEntries(key){
  const root=$(key+'-list');root.replaceChildren();
  const items=draft[key];if(!items.length){root.append(el('div','empty',key==='calendar'?'暂无日程':key==='apps'?'尚未添加应用':key==='countdowns'?'尚未添加倒计时':'尚未添加'));return}
  items.forEach((item,i)=>{
    const row=el('div',`entry${key==='calendar'?' calendar':''}${key==='apps'?' app':key==='countdowns'?' countdowns':''}`);
    function input(type,value,field,placeholder){const n=el('input');n.type=type;n.value=value;n.placeholder=placeholder||'';n.onchange=()=>{item[field]=n.value;markDirty()};return n}
    if(key==='apps'){
      row.append(input('text',item.name,'name','应用名'),input('text',item.command,'command','启动命令，如 open -a Safari'));
    }else{
      if(key==='calendar'||key==='countdowns')row.append(input('date',item.date,'date'));
      row.append(input('time',item.time,'time'),input('text',item[key==='calendar'||key==='countdowns'?'title':key==='alarms'?'label':'text'],key==='calendar'||key==='countdowns'?'title':key==='alarms'?'label':'text'));
    }
    const del=el('button','', '×');del.title='删除';del.onclick=()=>{items.splice(i,1);markDirty()};row.append(del);
    if(key!=='calendar'&&key!=='apps'&&key!=='countdowns'){
      const repeat=el('label','repeat','重复');const sel=el('select');
      for(const [name,days] of [['每天',[1,2,3,4,5,6,7]],['工作日',[1,2,3,4,5]],['周末',[6,7]]]){
        const opt=el('option','',name);opt.value=days.join(',');sel.append(opt)
      }
      sel.value=item.weekdays.join(',');sel.onchange=()=>{item.weekdays=sel.value.split(',').map(Number);markDirty()};
      repeat.append(sel);row.append(repeat);
    }
    root.append(row);
  });
}
function render(){if(!draft)return;renderPages();renderPalette();renderInspector();renderPreview();renderTasks();for(const key of ['reminders','calendar','alarms','apps','countdowns'])renderEntries(key);$('version-label').textContent=`v${saved?.version||'—'}`}
async function poll(){
  try{
    const next=await request('/api/status');live=next;
    if(!draft){saved=next.profile;const local=JSON.parse(localStorage.getItem('workbuddy-draft')||'null');draft=local?.base===saved.version?local.draft:structuredClone(saved);dirty=!!local&&local.base===saved.version}
    const connected=next.device.startsWith('connected:');
    $('device-badge').textContent=connected?'设备已连接':'设备未连接';$('device-badge').className=`badge${connected?'':' muted'}`;
    if($('autostart-toggle').dataset.loaded!=='1'){
      try{const r=await request('/api/autostart-status',{});$('autostart-toggle').checked=r.enabled;$('autostart-toggle').dataset.loaded='1'}
      catch(e){/* 忽略：平台不支持时隐藏 */ const t=$('autostart-toggle');if(t.closest('.field'))t.closest('.field').style.display='none'}
    }
    $('sync-badge').textContent=dirty?'未保存草稿':next.profile_status;
    $('sync-badge').className=`badge${dirty||next.profile_status==='待同步'?' warn':next.profile_status==='同步失败'?' error':''}`;
    $('applied-label').textContent=next.applied_version?`设备已应用 v${next.applied_version}`:'设备尚未确认配置';
    render();
  }catch(e){notice('无法读取客户端状态：'+e.message,true)}
}
async function save(){
  const check=await request('/api/validate',draft);
  const result=await request('/api/save',check.profile);
  saved=result.profile;draft=structuredClone(saved);dirty=false;localStorage.removeItem('workbuddy-draft');render();notice('配置已保存，等待同步到设备');
}
async function applyGallery(key){
  const th=galleryThemes[key];
  try{
    if(live.device?.startsWith('connected:')){
      notice(`正在生成「${th.name}」背景并推送…`);
      const c=document.createElement('canvas');c.width=320;c.height=480;
      th.draw(c.getContext('2d'),320,480);
      const blob=await new Promise(ok=>c.toBlob(ok,'image/jpeg',0.82));
      const r=await fetch('/api/bgimg',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
      if(!r.ok){const d=await r.json();throw new Error(d.error||'背景推送失败')}
    }else{notice('设备未连接：已应用配色，背景图待连接后重新点击主题')}
    Object.assign(draft.theme,th.colors,{card_opa:th.card_opa});
    markDirty();
    await save();await request('/api/sync',{});
    notice(`「${th.name}」主题已应用：背景+配色+卡片透明度`);
    poll();
  }catch(err){notice(err.message,true)}
}
function bind(){
  $('add-page').onclick=()=>{if(draft.pages.length>=6)return notice('最多 6 页',true);draft.pages.push({id:uniqueId('p'),title:'新页面',layout:'grid_2x2',widgets:['clock']});selected=draft.pages.length-1;markDirty()};
  $('page-title').onchange=e=>{current().title=e.target.value;markDirty()};
  $('page-layout').onchange=e=>{current().layout=e.target.value;markDirty()};
  $('duplicate-page').onclick=()=>{if(draft.pages.length>=6)return notice('最多 6 页',true);const copy=structuredClone(current());copy.id=uniqueId('p');copy.title+=' 副本';draft.pages.splice(selected+1,0,copy);selected++;markDirty()};
  $('delete-page').onclick=()=>{if(draft.pages.length===1)return notice('至少保留 1 页',true);draft.pages.splice(selected,1);selected=Math.max(0,selected-1);markDirty()};
  $('preview-prev').onclick=()=>{selected=(selected+draft.pages.length-1)%draft.pages.length;render()};
  $('preview-next').onclick=()=>{selected=(selected+1)%draft.pages.length;render()};
  for(const key of ['bg','grad','card','accent','text'])$('theme-'+key).oninput=e=>{draft.theme[key]=e.target.value.slice(1).toUpperCase();markDirty()};
  $('theme-bright').oninput=e=>{draft.theme.bright=Number(e.target.value);markDirty()};
  $('idle-dim').oninput=e=>{draft.theme.idledim=Number(e.target.value);$('idle-dim-val').textContent=Number(e.target.value)==0?'关闭':(e.target.value+' 秒');markDirty()};
  $('saver-style').onchange=e=>{draft.theme.saver=Number(e.target.value);markDirty()};
  $('saver-msg').onchange=e=>{draft.theme.savermsg=e.target.value;markDirty()};
  $('topclock-toggle').onchange=e=>{draft.theme.topclock=e.target.checked?1:0;markDirty()};
  $('topwx-toggle').onchange=e=>{draft.theme.topwx=e.target.checked?1:0;markDirty()};
  $('card-opa').oninput=e=>{draft.theme.card_opa=Number(e.target.value);$('opa-value').textContent=e.target.value+'%';markDirty()};
  $('bright-auto').onchange=e=>{draft.theme.bright_auto=e.target.checked?1:0;markDirty();notice(e.target.checked?'已开启自动亮度（白天亮/夜间暗）':'已切回手动亮度')};
  const startTemplates={
    '专注模式':[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']}],
    '数据控':[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']},{id:'pc',title:'电脑',layout:'grid_2x2',widgets:['hw_cards']}],
    '陪伴模式':[{id:'ai',title:'AI',layout:'list',widgets:['ai_list']},{id:'env',title:'环境',layout:'grid_2x2',widgets:['environment','weather']},{id:'today',title:'今天',layout:'grid_2x2',widgets:['today_calendar','reminder_list']}],
    '极简时钟':[{id:'clock',title:'时钟',layout:'grid_2x2',widgets:['clock']}]
  };
  const gal=$('theme-gallery');gal.replaceChildren();
  Object.entries(galleryThemes).forEach(([key,th])=>{
    const cell=el('div','gallery-item');
    const thumb=document.createElement('canvas');thumb.width=64;thumb.height=96;
    thumb.className='gallery-thumb';
    const ctx=thumb.getContext('2d');ctx.save();ctx.scale(64/320,96/480);
    try{th.draw(ctx,320,480)}catch(e){}
    ctx.restore();
    cell.append(thumb,el('span','',th.name));
    cell.onclick=()=>applyGallery(key);
    cell.title='点击应用：推送背景+自动配色';
    gal.append(cell);
  });
    const presetRoot=$('start-templates');presetRoot.replaceChildren();
  Object.entries(startTemplates).forEach(([name,pages])=>{
    const b=el('button','button ghost',name);
    b.onclick=()=>{draft.pages=structuredClone(pages);selected=0;markDirty();notice(`已套用「${name}」模板，保存并同步生效`)};
    presetRoot.append(b);
  });
  const presets=$('theme-presets');presets.replaceChildren();
  Object.entries(themePresets).forEach(([name,colors])=>{
    const b=el('button','preset-swatch');b.title=name;
    b.style.background=`linear-gradient(160deg,#${colors.bg},#${colors.grad} 60%,#${colors.accent})`;
    b.onclick=()=>{Object.assign(draft.theme,colors,{bright:draft.theme.bright});markDirty();notice(`已应用「${name}」主题，保存并同步后生效`)};
    presets.append(b);
  });
  $('bgimg-file').onchange=async e=>{
    const file=e.target.files[0];e.target.value='';
    if(!file)return;
    if(!live.device?.startsWith('connected:')){notice('设备未连接，先等设备上线',true);return}
    notice('正在处理图片…');
    try{
      const img=await new Promise((ok,bad)=>{const i=new Image();i.onload=()=>ok(i);i.onerror=bad;i.src=URL.createObjectURL(file)});
      const canvas=document.createElement('canvas');canvas.width=320;canvas.height=480;
      const ctx=canvas.getContext('2d');
      const scale=Math.max(320/img.width,480/img.height);
      const w=img.width*scale,h=img.height*scale;
      ctx.drawImage(img,(320-w)/2,(480-h)/2,w,h);   // cover 裁剪居中
      const blob=await new Promise(ok=>canvas.toBlob(ok,'image/jpeg',0.72));
      const r=await fetch('/api/bgimg',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
      const d=await r.json();
      if(!r.ok)throw new Error(d.error||'发送失败');
      notice('背景图已发送，等待设备应用');
    }catch(err){notice('背景图失败：'+err.message,true)}
  };
  $('bgimg-clear').onclick=async()=>{
    try{const r=await fetch('/api/bgimg-clear',{method:'POST'});const d=await r.json();
    if(!r.ok)throw new Error(d.error||'失败');notice(d.status)}
    catch(err){notice(err.message,true)}
  };
  $('autostart-toggle').onchange=async e=>{
    try{const r=await request('/api/autostart',{enable:e.target.checked});
      e.target.checked=r.enabled;notice(r.enabled?'已开启开机自启':'已关闭开机自启')}
    catch(err){e.target.checked=!e.target.checked;notice(err.message,true)}
  };
  document.querySelectorAll('[data-add]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.add, today=new Date().toLocaleDateString('en-CA');
    draft[key].push(key==='calendar'?{id:uniqueId('e'),date:today,time:'09:00',title:'新日程',enabled:true}:
      key==='alarms'?{id:uniqueId('a'),time:'09:00',label:'新闹钟',weekdays:[1,2,3,4,5,6,7],enabled:true}:
      key==='apps'?{id:uniqueId('app'),name:'新应用',command:'open -a Finder'}:
      key==='countdowns'?{id:uniqueId('c'),date:today,time:'20:00',title:'新目标',enabled:true}:
        {id:uniqueId('r'),time:'09:00',text:'新提醒',weekdays:[1,2,3,4,5,6,7],enabled:true});markDirty()
  });
  $('validate-btn').onclick=async()=>{try{await request('/api/validate',draft);notice('配置检查通过，可以保存')}catch(e){notice(e.message,true)}};
  $('save-btn').onclick=async()=>{try{await save()}catch(e){notice(e.message,true)}};
  $('sync-btn').onclick=async()=>{try{if(dirty)await save();await request('/api/sync',{});notice('已请求同步，等待设备应用回执');poll()}catch(e){notice(e.message,true)}};
  $('weather-city').onchange=e=>{if(!draft.weather)draft.weather={};draft.weather.city=e.target.value.trim();markDirty()};
  $('reset-btn').onclick=()=>{if(!confirm('恢复默认档案？当前未保存修改会丢失。'))return;draft={schema:1,version:saved.version,pages:[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']},{id:'computer',title:'电脑',layout:'grid_2x2',widgets:['hw_cards']},{id:'environment',title:'环境',layout:'grid_2x2',widgets:['environment','clock']}],reminders:[],calendar:[],alarms:[],apps:[],theme:{bg:'0B1220',grad:'0E1B33',card:'274064',card_edge:'3E506E',accent:'22D3EE',text:'F3F7FF',sub:'A8BDD9',bright:90}};selected=0;markDirty()};
}
bind();poll();setInterval(poll,2500);setInterval(()=>{if(draft)renderPreview()},15000);
