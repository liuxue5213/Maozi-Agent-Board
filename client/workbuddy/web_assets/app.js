const $ = id => document.getElementById(id);
const widgets = {
  ai_list: ['AI 会话', '✦', 4], hw_cards: ['硬件四卡', '▦', 4],
  clock: ['时钟', '◷', 1], environment: ['温湿度', '◉', 1],
  reminder_list: ['提醒列表', '☷', 2], today_calendar: ['今日日历', '▣', 2],
  alarm: ['闹钟', '◴', 1], weather: ['天气', '☁', 1], countdown: ['倒计时', '⏳', 1],
  ai_cost: ['AI 用量', '∑', 1]
};
const stateNames = {THINKING:'思考中',GENERATING:'生成中',TOOL:'执行工具',BUILD:'构建中',DONE:'已完成',ERROR:'出错',WAIT_INPUT:'等你回复',ASK:'进行中待确认'};
const RUNNING_STATES = ['THINKING','GENERATING','TOOL','BUILD'];
// 主题预设：card 与 bg 均已拉开亮度差（>=1.5:1），sub 均可读。
// 修改配色时务必让 profile.validate_profile 的三项对比度校验通过。
const themePresets = {
  '极光石板':{bg:'060A12',grad:'0E1726',card:'28384E',card_edge:'3C5070',accent:'4CC9F0',text:'F1F5F9',sub:'A6B6C9'},
  '墨蓝夜':{bg:'05080F',grad:'0A1220',card:'223650',card_edge:'36588C',accent:'6AA0FF',text:'EEF3FB',sub:'9FB4D2'},
  '深蓝科技':{bg:'0B1220',grad:'0E1B33',card:'274064',card_edge:'3E506E',accent:'22D3EE',text:'F3F7FF',sub:'A8BDD9'},
  '紫夜':{bg:'120B22',grad:'1D1136',card:'3A2D5C',card_edge:'54427F',accent:'A78BFA',text:'F5F3FF',sub:'C4B5E8'},
  '苍翠':{bg:'071A12',grad:'0C2A1B',card:'1D4A33',card_edge:'2A6B4A',accent:'34D399',text:'ECFDF5',sub:'A3D5BC'},
  '暖阳':{bg:'1A1207',grad:'2A1C0C',card:'4A3418',card_edge:'6B4C24',accent:'F59E0B',text:'FFF8EB',sub:'DEC9A3'},
  '玫瑰':{bg:'1A0B12',grad:'2A1120',card:'6E3E5E',card_edge:'8F4566',accent:'FB7185',text:'FFF1F3',sub:'E8B4C0'}
};
// 主题库（任务视图 → 主题库）的卡片清单在 galleryThemes 定义之后初始化
// （见 themeLibrary），避免 const 初始化前读取的 TDZ 崩溃。
let draft = null, saved = null, live = {tasks:[],telemetry:{},device:'等待设备'};
let selected = 0, dirty = false, dragPage = -1, dragWidget = -1, toastTimer = null;
let polling = false, lastOkAt = 0, failStreak = 0, tickTimer = null;
let currentView = 'overview';
let selectedThemeKey = null, themeTab = 'preset';

/* ---------- 颜色工具：预览按「半透明卡片叠在背景上」的真实合成色校验对比度 ---------- */
function hexToRgb(hex){
  const h = String(hex||'').replace('#','');
  const n = h.length===3 ? h.split('').map(c=>c+c).join('') : h;
  return [parseInt(n.slice(0,2),16)||0, parseInt(n.slice(2,4),16)||0, parseInt(n.slice(4,6),16)||0];
}
function relLuminance(rgb){
  const [r,g,b] = rgb.map(v=>{
    const c = v/255;
    return c<=0.04045 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4);
  });
  return 0.2126*r + 0.7152*g + 0.0722*b;
}
function contrastRatio(a, b){
  const la = relLuminance(a), lb = relLuminance(b);
  return (Math.max(la,lb)+0.05)/(Math.min(la,lb)+0.05);
}
function composite(fg, bg, alpha){
  return fg.map((v,i)=>Math.round(v*alpha + bg[i]*(1-alpha)));
}
function ensureContrast(candidates, bg, target){
  let best = candidates[0], bestRatio = 0;
  for(const c of candidates){
    const ratio = contrastRatio(hexToRgb(c), bg);
    if(ratio > bestRatio){ bestRatio = ratio; best = c; }
  }
  if(bestRatio >= target) return best;
  const white = '#ffffff', black = '#000000';
  const pick = contrastRatio(hexToRgb(white), bg) >= contrastRatio(hexToRgb(black), bg) ? white : black;
  return pick;
}
function applyDeviceTheme(theme, root){
  const screen = root || $('screen');
  if (!screen) return;
  const bg = hexToRgb(theme.bg), grad = hexToRgb(theme.grad);
  const opa = Math.max(0.3, Math.min(1, (theme.card_opa ?? 100) / 100));
  const backdrop = composite(bg, grad, 0.5);
  const cardBg = composite(hexToRgb(theme.card), backdrop, opa);
  screen.style.setProperty('--dev-bg', '#'+theme.bg);
  screen.style.setProperty('--dev-grad', '#'+theme.grad);
  screen.style.setProperty('--dev-card', '#'+theme.card);
  screen.style.setProperty('--dev-edge', '#'+(theme.card_edge||theme.card));
  screen.style.setProperty('--dev-accent', '#'+theme.accent);
  screen.style.setProperty('--dev-text', '#'+theme.text);
  screen.style.setProperty('--dev-sub', '#'+theme.sub);
  screen.style.setProperty('--dev-card-opa', String(opa));
  screen.style.setProperty('--dev-card-ink', ensureContrast([theme.text, '#ffffff'], cardBg, 4.5));
  screen.style.setProperty('--dev-sub-ink', ensureContrast([theme.sub, theme.text], cardBg, 3));
}

// ---------- 主题画廊：程序绘制背景 ----------
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
function galleryColors(key){
  return galleryThemes[key] ? galleryThemes[key].colors : themePresets['深蓝科技'];
}
// 主题库（任务视图 → 主题库）：预设 + 精选画廊的合一清单。
// 必须在 galleryThemes 之后初始化（galleryColors 会读它）。
const themeLibrary = [
  {key:'preset-深蓝科技', name:'深蓝科技', colors:themePresets['深蓝科技'], opa:70},
  {key:'preset-紫夜', name:'紫夜', colors:themePresets['紫夜'], opa:72},
  {key:'preset-苍翠', name:'苍翠', colors:themePresets['苍翠'], opa:70},
  {key:'preset-暖阳', name:'暖阳', colors:themePresets['暖阳'], opa:66},
  {key:'preset-玫瑰', name:'玫瑰', colors:themePresets['玫瑰'], opa:68},
  {key:'gallery-aurora', name:'极光', colors:galleryColors('aurora'), opa:70, gallery:'aurora'},
  {key:'gallery-starry', name:'星空', colors:galleryColors('starry'), opa:70, gallery:'starry'},
  {key:'gallery-garden', name:'像素花园', colors:galleryColors('garden'), opa:70, gallery:'garden'},
  {key:'gallery-waves', name:'深海波浪', colors:galleryColors('waves'), opa:70, gallery:'waves'},
  {key:'gallery-ink', name:'墨韵', colors:galleryColors('ink'), opa:88, gallery:'ink'},
  {key:'gallery-dusk', name:'山峦黄昏', colors:galleryColors('dusk'), opa:65, gallery:'dusk'},
  {key:'gallery-ember', name:'熔岩余烬', colors:galleryColors('ember'), opa:70, gallery:'ember'}
];
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
  renderSyncState();
  if(currentView==='settings')renderPages();
}
function current() { return draft.pages[selected] || draft.pages[0]; }
function uniqueId(prefix) { return `${prefix}${Date.now().toString(36).slice(-6)}${Math.random().toString(36).slice(2,5)}`.slice(0,16); }
function reorder(list, from, to) {
  if (from < 0 || to < 0 || from === to) return;
  const [item] = list.splice(from,1); list.splice(to,0,item); markDirty();
}
/* ================= 命令面板（优化#6）与键盘快捷键（优化#5） ================= */
function commandDefs(){
  const views=[['overview','概览'],['tasks','任务视图'],['saver','息屏显示'],
               ['extras','其他视图'],['history','历史记录'],['settings','设置']];
  const cmds=views.map(([id,name],i)=>({label:`打开：${name}`,key:String(i+1),run:()=>switchView(id)}));
  cmds.push(
    {label:'保存配置',key:'S',run:()=>save().catch(err=>notice(err.message,true))},
    {label:'同步到设备',run:async()=>{try{if(dirty)await save();await request('/api/sync',{});logHist('sync','已请求同步到设备');notice('已请求同步，等待设备应用回执');poll()}catch(e){notice(e.message,true)}}},
    {label:'刷新数据',key:'R',run:()=>{poll();notice('已刷新')}},
    {label:'导出诊断包',run:()=>downloadFile('/api/diagnose','workbuddy-diagnose.zip').then(()=>{logHist('diag','诊断包已导出');notice('诊断包已开始下载')}).catch(e=>notice(e.message,true))},
    {label:'切换深浅外观',run:()=>$('color-mode-btn').click()},
    {label:'上传背景图（其他视图）',run:()=>{switchView('extras');$('bgimg-file').click()}},
    {label:'导出配置 JSON',run:()=>$('btn-export').click()},
    {label:'导出布局 JSON',run:()=>$('btn-layout-export').click()},
    {label:'撤销上一步配置（Cmd/Ctrl+Z）',run:()=>undoChange()},
    {label:'任务视图：多窗格预览',run:()=>{switchView('tasks');switchTaskTab('multi')}},
    {label:'查看键盘快捷键',key:'?',run:()=>toggleKbd(true)}
  );
  // 优化#17：索引当前配置——页面/提醒/应用都能搜到并直达（或直接执行）
  if(draft){
    (draft.pages||[]).forEach((page,i)=>{
      cmds.push({label:`编辑页面：${i+1} ${page.title}`,run:()=>{
        switchView('settings');selected=i;renderSettings()}});
    });
    (draft.reminders||[]).slice(0,8).forEach(r=>{
      if(!r.enabled)return;
      cmds.push({label:`提醒：${r.time} ${r.text}`,run:()=>switchView('extras')});
    });
    (draft.apps||[]).slice(0,12).forEach(a=>{
      cmds.push({label:`启动应用：${a.name}`,run:()=>request('/api/launch',{id:a.id})
        .then(r=>notice(r.status||'已启动'))
        .catch(err=>notice(err.message,true))});
    });
  }
  return cmds;
}
let cmdIndex=0, cmdItems=[];
function openCmd(open){
  $('cmd-overlay').hidden=!open;
  if(open){ $('cmd-input').value=''; renderCmdList(''); setTimeout(()=>$('cmd-input').focus(),0); }
}
function renderCmdList(q){
  const root=$('cmd-list');root.replaceChildren();
  const query=String(q||'').trim().toLowerCase();
  cmdItems=commandDefs().filter(c=>!query||c.label.toLowerCase().includes(query));
  cmdIndex=0;
  if(!cmdItems.length){root.append(el('div','empty','没有匹配的命令'));return}
  cmdItems.forEach((c,i)=>{
    const row=el('button','cmd-item'+(i===cmdIndex?' active':''));
    row.type='button';row.setAttribute('role','option');
    row.append(el('span','',c.label));
    if(c.key)row.append(el('kbd','',c.key));
    row.onclick=()=>{openCmd(false);c.run()};
    root.append(row);
  });
}
function cmdMove(delta){
  if(!cmdItems.length)return;
  cmdIndex=(cmdIndex+delta+cmdItems.length)%cmdItems.length;
  const rows=$('cmd-list').children;
  [...rows].forEach((n,i)=>n.classList.toggle('active',i===cmdIndex));
  if(rows[cmdIndex])rows[cmdIndex].scrollIntoView({block:'nearest'});
}
const KBD_BINDINGS=[
  {keys:['1','…','6'],desc:'切换视图（概览/任务/息屏/其他/历史/设置）'},
  {keys:['R'],desc:'刷新数据'},
  {keys:['S'],desc:'保存配置'},
  {keys:['⌘/Ctrl','Z'],desc:'撤销上一步配置改动'},
  {keys:['⌘/Ctrl','K'],desc:'命令面板'},
  {keys:['?'],desc:'快捷键帮助'},
  {keys:['Esc'],desc:'退出聚焦 / 关闭抽屉与弹层'}
];
function toggleKbd(open){
  const ov=$('kbd-overlay');
  if(open&&ov.hidden){
    const root=$('kbd-rows');root.replaceChildren();
    KBD_BINDINGS.forEach(b=>{
      const row=el('div','kbd-row');
      const keys=el('span','cmd-keys');
      b.keys.forEach(k=>keys.append(el('kbd','',k)));
      row.append(keys,el('span','',b.desc));
      root.append(row);
    });
  }
  ov.hidden=!open;
}
function overlayOpen(){ return !$('cmd-overlay').hidden || !$('kbd-overlay').hidden; }
function typingTarget(e){
  const t=e.target;
  return !!(t&&(t.tagName==='INPUT'||t.tagName==='TEXTAREA'||t.tagName==='SELECT'||t.isContentEditable));
}
/* 优化#20：配置撤销栈。每 2s 对 draft 做一次快照（内容变化才入栈，深 15），
   Cmd/Ctrl+Z 弹出上一份并回灌。粗粒度但鲁棒——不需要侵入每个编辑回调。 */
let undoStack=[], lastDraftSnap='';
setInterval(()=>{
  if(!draft)return;
  const snap=JSON.stringify(draft);
  if(snap===lastDraftSnap)return;
  if(lastDraftSnap)undoStack.push(lastDraftSnap);
  if(undoStack.length>15)undoStack.shift();
  lastDraftSnap=snap;
},2000);
function undoChange(){
  if(!undoStack.length){notice('没有可撤销的更改');return}
  lastDraftSnap=undoStack.pop();
  try{draft=JSON.parse(lastDraftSnap)}catch(e){notice('撤销失败：快照损坏',true);return}
  markDirty();
  notice('已撤销上一步配置改动（Cmd/Ctrl+Z）');
}
document.addEventListener('keydown',e=>{
  if((e.metaKey||e.ctrlKey)&&(e.key==='k'||e.key==='K')){
    e.preventDefault();closeTaskDrawer();closeFocus();toggleKbd(false);openCmd($('cmd-overlay').hidden);return;
  }
  if((e.metaKey||e.ctrlKey)&&(e.key==='z'||e.key==='Z')&&!e.shiftKey){
    e.preventDefault();undoChange();return;
  }
  if(e.key==='Escape'){
    e.preventDefault();
    if(!$('focus-stage').hidden){closeFocus();return}
    if(!$('task-drawer').hidden){closeTaskDrawer();return}
    if(overlayOpen()){openCmd(false);toggleKbd(false)}
    return;
  }
  if($('cmd-overlay').hidden){
    if(typingTarget(e))return;
    if(e.metaKey||e.ctrlKey||e.altKey)return;
    const views=['overview','tasks','saver','extras','history','settings'];
    if(e.key>='1'&&e.key<='6'&&!e.shiftKey){switchView(views[Number(e.key)-1]);return}
    const k=(e.key||'').toLowerCase();
    if(k==='r'){poll();notice('已刷新');return}
    if(k==='s'&&!e.shiftKey){save().catch(err=>notice(err.message,true));return}
    if(e.key==='?'){toggleKbd(true);return}
  }else if(e.target===$('cmd-input')){
    if(e.key==='ArrowDown'){e.preventDefault();cmdMove(1)}
    else if(e.key==='ArrowUp'){e.preventDefault();cmdMove(-1)}
    else if(e.key==='Enter'){e.preventDefault();const c=cmdItems[cmdIndex];if(c){openCmd(false);c.run()}}
  }
});

async function request(path, body) {
  const response = await fetch(path, body===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}

/* ================= 历史记录（本机浏览器） ================= */
const HIST_KEY = 'workbuddy-history';
function logHist(kind, text, ok=true){
  try{
    const list = JSON.parse(localStorage.getItem(HIST_KEY) || '[]');
    list.unshift({t:Date.now(), kind, text, ok});
    localStorage.setItem(HIST_KEY, JSON.stringify(list.slice(0,100)));
  }catch(e){/* 存储不可用时静默 */}
  if(currentView==='history')renderHistory();
}
function renderHistory(){
  const root = $('history-list'); root.replaceChildren();
  let list = [];
  try{ list = JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); }catch(e){}
  if(!list.length){ root.append(el('div','empty','暂无记录。保存、同步、主题应用等操作会出现在这里。')); return }
  const kindNames = {save:'保存', sync:'同步', theme:'主题', bg:'背景图', ota:'固件', diag:'诊断', import:'导入', export:'导出', layout:'布局', saver:'息屏'};
  list.forEach(item=>{
    const row = el('div',`history-item${item.ok?'':' failed'}`);
    const time = new Date(item.t).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    row.append(el('span','history-kind',kindNames[item.kind]||item.kind),
               el('span','history-text',item.text),
               el('time','history-time',time));
    root.append(row);
  });
}

/* ================= 任务视图配置（布局方案） ================= */
const TV_KEY = 'workbuddy-taskview';
const tvDefaults = {
  layout:'focus',
  fv:{supplement:'steps', value:'elapsed', others:'show', idle:'focus'},
  tv:{range:'all', time:'event'},
  crown:{mode:'auto', text:''},
  cp:{scene:'yard', daynight:true, weather:false, fx:false, action:true, feedback:true, rest:false,
      disp:{name:true, elapsed:true, model:false, tok:false, state:true}},
  dv:{combo:'auto', middle:'usage',
      metrics:{tok:true, model:true, task:false, elapsed:false, cpu:true, mem:true, disk:true, net:false}}
};
function loadTV(){
  try{
    const raw = JSON.parse(localStorage.getItem(TV_KEY) || 'null');
    if(!raw) return structuredClone(tvDefaults);
    return structuredClone({...tvDefaults, ...raw,
      fv:{...tvDefaults.fv, ...(raw.fv||{})},
      tv:{...tvDefaults.tv, ...(raw.tv||{})},
      crown:{...tvDefaults.crown, ...(raw.crown||{})},
      cp:{...tvDefaults.cp, ...(raw.cp||{}), disp:{...tvDefaults.cp.disp, ...(raw.cp?.disp||{})}},
      dv:{...tvDefaults.dv, ...(raw.dv||{}), metrics:{...tvDefaults.dv.metrics, ...(raw.dv?.metrics||{})}}});
  }catch(e){ return structuredClone(tvDefaults) }
}
let tv = loadTV();
function saveTV(){ localStorage.setItem(TV_KEY, JSON.stringify(tv)); }

const schemeNames = {live:'实时状态', focus:'状态聚焦', timeline:'任务时间线', companion:'角色陪伴', dashboard:'数据看板'};
function crownName(){
  if(tv.crown.mode==='custom' && tv.crown.text.trim()) return tv.crown.text.trim();
  return live.tasks?.[0]?.tool ? live.tasks[0].tool.toUpperCase() : 'WorkBuddy';
}
function isConnected(){ return live.device?.startsWith('connected:'); }

/* ---------- 小屏预览：卡片构件 ---------- */
function card(overline,value,sub,wide=false){
  const n=el('div',`screen-card${wide?' wide':''}`);
  n.append(el('div','overline',overline),el('div','hero-value',String(value)),el('div','subline',sub));return n;
}
function clock(){return new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
function fmtElapsed(sec){
  sec = Math.max(0, Math.round(sec||0));
  const m = Math.floor(sec/60), s = sec%60;
  return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}
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
    container.append(card('天气',t.wx_temp===undefined?'-- ℃':`${t.wx_temp} ℃`,'在其他视图输入城市后显示'));
  }
  if(key==='countdown'){
    const item=(draft.countdowns||[]).find(x=>x.enabled);
    container.append(card('倒计时',item?`${item.date} ${item.time}`:'未设置',item?item.title:'添加目标日期'));
  }
  if(key==='ai_cost'){
    container.append(card('AI 用量',t.ait===undefined?'--':`今日 ${String(t.ait).split(',')[0]}k tok`,
      t.aiu?t.aiu.split(',').slice(0,2).join(' · '):'AI 会话活动后显示'));
  }
}

/* ---------- 四大布局渲染器 ---------- */
function visibleTasks(){
  const tasks = live.tasks||[];
  return tv.tv.range==='running' ? tasks.filter(x=>RUNNING_STATES.includes(x.state)) : tasks;
}
function renderFocus(body){
  const tasks=visibleTasks(), top=tasks[0];
  const pill=el('div','focus-pill');
  pill.append(el('span','focus-pill-icon',top&&top.state==='ASK'?'⏸':'◉'));
  pill.append(el('span','',top?(stateNames[top.state]||top.state):'空闲中'));
  body.append(pill);
  body.append(el('div','focus-state',top?(stateNames[top.state]||top.state):'等待任务'));
  if(top){
    if(tv.fv.supplement==='steps') body.append(el('div','focus-sub',`步骤与结果 · ${top.prog||'进行中'} · ${top.project||'未命名项目'}`));
    else if(tv.fv.supplement==='lastmsg') body.append(el('div','focus-sub',`最近消息 · ${top.project||'未命名项目'}`));
    if(tv.fv.value!=='none'){
      const v = tv.fv.value==='model' ? ((live.telemetry.aiu||'').split(',')[1]||'模型未知')
              : tv.fv.value==='auto' ? `已运行 ${fmtElapsed(top.elapsed)}`
              : `本轮用时 ${fmtElapsed(top.elapsed)}`;
      body.append(el('div','focus-value',v));
    }
  }else{
    body.append(el('div','focus-sub',tv.fv.idle==='focus'?'保持状态聚焦，等待新的会话':`空闲时将显示「${schemeNames[tv.fv.idle]||'状态聚焦'}」`));
  }
  if(tv.fv.others==='show'){
    const others=el('div','mini-tasks');
    tasks.slice(1,4).forEach(x=>{
      const row=el('div','mini-task');
      row.append(el('span','',`${x.tool} · ${x.project||'未命名'}`),el('em','',stateNames[x.state]||x.state));
      others.append(row);
    });
    if(!others.childElementCount)others.append(el('div','mini-task','暂无其他进行中任务'));
    body.append(others);
  }
}
function renderTimeline(body){
  const tasks=visibleTasks();
  body.append(el('div','tl-head','任务时间线'));
  body.append(el('div','focus-sub',tasks.length?`${tasks.length} 个任务 · ${tv.tv.range==='running'?'仅进行中':'进行中与近期完成'}`:'暂无任务'));
  const list=el('div','tl-list');
  tasks.slice(0,4).forEach(x=>{
    const row=el('div','tl-item');
    row.append(el('i','tl-dot'));
    const main=el('div','tl-main');
    main.append(el('strong','',`${x.tool} · ${x.project||'未命名项目'}`));
    main.append(el('small','',stateNames[x.state]||x.state));
    row.append(main);
    if(tv.tv.time!=='none'){
      row.append(el('span','tl-time', tv.tv.time==='event' ? `${x.age} 秒前` : fmtElapsed(x.elapsed)));
    }
    list.append(row);
  });
  if(!tasks.length)list.append(el('div','mini-task','连接 AI 会话后显示时间线'));
  body.append(list);
}
function renderCompanion(body){
  const t=live.telemetry||{}, top=(live.tasks||[])[0];
  const scene=el('div',`cp-scene scene-${tv.cp.scene}${tv.cp.fx?' with-fx':''}${tv.cp.action?' with-action':''}`);
  scene.append(el('span','cp-pet','🐱'));
  scene.append(el('span','cp-ground'));
  body.append(scene);
  const info=el('div','cp-info');
  const lines=[];
  if(tv.cp.disp.state) lines.push(top?(stateNames[top.state]||top.state):'休息中');
  if(tv.cp.disp.name&&top) lines.push(top.project||'未命名项目');
  if(tv.cp.disp.elapsed&&top) lines.push(`本轮 ${fmtElapsed(top.elapsed)}`);
  if(tv.cp.disp.model) lines.push((t.aiu||'').split(',')[1]||'模型待定');
  if(tv.cp.disp.tok) lines.push(t.ait?`今日 Token ${String(t.ait).split(',')[0]}k`:'今日暂无用量');
  info.append(el('div','cp-state',lines[0]||'状态待确认'));
  if(lines.length>1)info.append(el('div','cp-meta',lines.slice(1).join(' · ')));
  if(tv.cp.feedback && top?.state==='DONE') info.append(el('div','cp-cheer','🎉 任务完成，陪你庆祝一下'));
  if(tv.cp.rest) info.append(el('div','cp-rest','记得起来喝口水'));
  body.append(info);
}
function renderDashboard(body){
  const t=live.telemetry||{}, top=(live.tasks||[])[0];
  const m=tv.dv.metrics;
  const sel=Object.entries(m).filter(([,v])=>v).length;
  body.append(el('div','focus-sub', tv.dv.combo==='auto'?'自动组合 · 今日概览':'固定指标 · 今日概览'));
  if(tv.dv.middle==='usage' && (m.tok||m.model)){
    const mid=el('div','dash-mid');
    if(m.tok) mid.append(card('今日 Token', t.ait===undefined?'--':`${String(t.ait).split(',')[0]}k`,'全部来源合计'));
    if(m.model&&mid.childElementCount<2) mid.append(card('当前模型',(t.aiu||'').split(',')[1]||'待定', top?top.tool.toUpperCase():'—'));
    if(mid.childElementCount)body.append(mid);
  }
  if(tv.dv.middle==='stats' && (m.task||m.elapsed)){
    const mid=el('div','dash-mid');
    if(m.task) mid.append(card('任务名称', top?(top.project||'未命名'):'无进行中任务', top?top.tool.toUpperCase():'—'));
    if(m.elapsed&&mid.childElementCount<2) mid.append(card('本轮用时', top?fmtElapsed(top.elapsed):'--','当前会话'));
    if(mid.childElementCount)body.append(mid);
  }
  const grid=el('div','screen-grid');
  if(m.cpu) grid.append(card('处理器',t.cpu===undefined?'—':`${t.cpu}%`,'即时负载'));
  if(m.mem) grid.append(card('内存',t.mem===undefined?'—':`${t.mem}%`,t.memtotal?`${t.memused} / ${t.memtotal} GB`:'数据不可用'));
  if(m.disk) grid.append(card('存储',t.disk===undefined?'—':`${t.disk}%`,t.diskfree?`可用 ${t.diskfree} GB`:'数据不可用'));
  if(m.net) grid.append(card('网络',t.netrx===undefined?'—':`${t.netrx}/${t.nettx} Mb`,'↓ 上行 / ↓ 下行'));
  if(grid.childElementCount)body.append(grid);
  else body.append(el('div','mini-task','请在右侧勾选至少一项显示指标'));
  $('dv-metric-count').textContent=`已选 ${sel} 项`;
}
function renderTaskPreview(){
  const scheme = $('scheme-select').value;
  const body=$('screen-body');
  applyDeviceTheme(draft.theme);
  $('screen-clock').textContent=clock();
  $('screen-title').textContent=crownName();
  $('screen-conn').textContent=isConnected()?'设备在线':'设备离线';
  $('screen-source').textContent=live.tasks?.length?'实时会话预览':'配置预览';
  body.replaceChildren();
  if(scheme==='live'){
    const page=current();
    $('screen-title').textContent = tv.crown.mode==='custom'&&tv.crown.text.trim() ? tv.crown.text.trim() : page.title;
    if(page.widgets.length===1&&['ai_list','hw_cards'].includes(page.widgets[0]))previewWidget(page.widgets[0],body);
    else {const grid=el('div','screen-grid');page.widgets.forEach(key=>previewWidget(key,grid));body.append(grid)}
  }
  else if(scheme==='focus')renderFocus(body);
  else if(scheme==='timeline')renderTimeline(body);
  else if(scheme==='companion')renderCompanion(body);
  else renderDashboard(body);
  $('preview-caption').textContent=`${schemeNames[scheme]} · ${crownName()} · ${isConnected()?'小屏已连接，实时生效':'小屏未连接，仅电脑预览'}`;
}
function renderCfgPanels(){
  const scheme=$('scheme-select').value;
  $('cfg-focus').hidden = scheme!=='focus';
  $('cfg-timeline').hidden = scheme!=='timeline';
  $('cfg-companion').hidden = scheme!=='companion';
  $('cfg-dashboard').hidden = scheme!=='dashboard';
  document.querySelectorAll('.layout-btn').forEach(b=>{
    b.classList.toggle('active', b.dataset.layout===tv.layout);
    b.setAttribute('aria-pressed', b.dataset.layout===tv.layout ? 'true':'false');
  });
}

/* ---------- 主题库 ---------- */
function myThemes(){
  try{ return JSON.parse(localStorage.getItem('workbuddy-mythemes')||'[]'); }catch(e){ return [] }
}
function renderThemeGrid(){
  const root=$('theme-grid'); root.replaceChildren();
  const items = themeTab==='preset' ? themeLibrary
    : myThemes().map((t,i)=>({key:'my-'+i, name:t.name||'我的主题 '+(i+1), colors:t.colors, opa:t.opa||70}));
  if(!items.length){
    root.append(el('div','empty','还没有自定义主题。在「设置 → 主题配色」调好后点「存为自定义主题」。'));
    return;
  }
  items.forEach(item=>{
    const cell=el('button','theme-card'+(selectedThemeKey===item.key?' active':''));
    cell.type='button'; cell.setAttribute('role','option');
    const mini=el('div','theme-mini');
    mini.style.background=`linear-gradient(165deg,#${item.colors.bg},#${item.colors.grad} 70%)`;
    const bar=el('div','theme-mini-bar'); bar.style.background='#'+item.colors.card; bar.style.borderColor='#'+(item.colors.card_edge||item.colors.card);
    bar.append(el('span')); bar.firstChild.style.background='#'+item.colors.accent;
    mini.append(bar);
    mini.style.color='#'+item.colors.text;
    mini.append(el('span','theme-mini-time',clock()));
    cell.append(mini, el('span','theme-card-name',item.name));
    cell.onclick=()=>{selectedThemeKey=item.key;renderThemeGrid();
      const desc = themeTab==='preset' ? `精选主题「${item.name}」，应用到任务后保存并同步。`
        : `自定义主题「${item.name}」。`;
      $('theme-current-name').textContent=item.name;
      $('theme-current-desc').textContent=desc;
    };
    root.append(cell);
  });
}
async function applySelectedTheme(){
  const items = themeTab==='preset' ? themeLibrary
    : myThemes().map((t,i)=>({key:'my-'+i, name:t.name||'我的主题 '+(i+1), colors:t.colors, opa:t.opa||70}));
  const item = items.find(x=>x.key===selectedThemeKey);
  if(!item){ notice('请先在左侧选择一个主题', true); return }
  try{
    if(isConnected() && item.gallery){
      notice(`正在生成「${item.name}」背景并推送…`);
      const c=document.createElement('canvas');c.width=320;c.height=480;
      drawGallery(c.getContext('2d'),320,480,item.gallery);
      const blob=await new Promise(ok=>c.toBlob(ok,'image/jpeg',0.82));
      const r=await fetch('/api/bgimg',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
      if(!r.ok){const d=await r.json();throw new Error(d.error||'背景推送失败')}
      logHist('bg', `主题背景「${item.name}」已推送`);
    }
    Object.assign(draft.theme, item.colors, {card_opa:item.opa});
    markDirty();
    await save(true);
    await request('/api/sync',{});
    logHist('theme', `主题「${item.name}」已应用并同步`);
    notice(`「${item.name}」主题已应用：配色+卡片风格`);
    poll();
  }catch(err){ notice(err.message, true); logHist('theme', `主题「${item.name}」应用失败：${err.message}`, false) }
}

/* ---------- 息屏显示 ---------- */
const dialDefs = [
  {v:0, name:'时序', desc:'极简时钟'},
  {v:1, name:'方寸仪表', desc:'紧凑数据'},
  {v:2, name:'像素夜色', desc:'低亮度夜钟'},
  {v:3, name:'诗词短句', desc:'名句伴眠'}
];
const POEMS=[
  ['空山新雨后','王维《山居秋暝》'],
  ['月落乌啼霜满天','张继《枫桥夜泊》'],
  ['晚来天欲雪','白居易《问刘十九》'],
  ['星垂平野阔','杜甫《旅夜书怀》']
];
let sv = {mode:'theme', dial:0, hideLock:false};
function loadSV(){
  try{ sv = {...sv, ...JSON.parse(localStorage.getItem('workbuddy-saver')||'{}')}; }catch(e){}
}
function saveSVLocal(){ localStorage.setItem('workbuddy-saver', JSON.stringify(sv)); }
function renderDials(){
  const root=$('saver-cards'); root.replaceChildren();
  dialDefs.forEach(d=>{
    const cell=el('button','dial-card'+(sv.dial===d.v?' active':''));
    cell.type='button';
    const prev=el('div','dial-prev');
    prev.append(el('span','dial-time',clock()));
    if(d.v===1)prev.append(el('span','dial-sub','CPU 12%'));
    if(d.v===2)prev.append(el('i','dial-pixel'));
    if(d.v===3)prev.append(el('span','dial-poem','空山新雨后'));
    cell.append(prev, el('span','dial-name',d.name), el('small','dial-desc',d.desc));
    cell.onclick=()=>{sv.dial=d.v;sv.mode='theme';saveSVLocal();renderSaverView()};
    root.append(cell);
  });
}
function renderSaverPreview(){
  const now=new Date();
  $('sv-date').textContent=`${now.getMonth()+1}月${now.getDate()}日 星期${'日一二三四五六'[now.getDay()]}`;
  const poem=POEMS[sv.dial%POEMS.length];
  $('sv-poem').textContent=poem[0]; $('sv-author').textContent=poem[1];
  $('sv-corner').textContent=crownName();
  $('sv-screen').className='saver-screen'+(sv.mode==='always'?' always':'');
  if(sv.mode==='always'){
    $('sv-title').textContent='屏幕常亮'; $('sv-lunar').textContent='小屏将保持当前画面';
    $('sv-count').textContent='不会进入息屏';
    $('sv-mode-note').textContent='当前使用：常亮（不息屏）';
    return;
  }
  $('sv-title').textContent=dialDefs[sv.dial].name;
  const target=new Date(now); target.setHours(21,40,0,0);
  const days=Math.max(1,Math.ceil((target-now)/86400000));
  $('sv-lunar').textContent='农历 '+now.toLocaleDateString('zh-CN',{month:'long',day:'numeric'});
  $('sv-count').textContent=`距宵禁提醒还有 ${days} 天`;
  $('sv-mode-note').textContent=`当前使用：${dialDefs[sv.dial].name}`;
}
function renderSaverView(){
  $('saver-mode').value=sv.mode;
  $('saver-hide-lock').checked=!!sv.hideLock;
  renderDials(); renderSaverPreview();
  const detail=$('saver-detail');
  detail.hidden = !$('btn-saver-detail').dataset.open;
  $('saver-idle').value=draft.theme.idledim??60;
  $('saver-idle-val').textContent=(draft.theme.idledim??60)==0?'关闭':(draft.theme.idledim+' 秒');
  $('saver-msg').value=draft.theme.savermsg||'';
}
async function applySaver(){
  try{
    if(sv.mode==='always') draft.theme.idledim=0;
    else{
      draft.theme.saver=sv.dial;
      if(!(draft.theme.idledim??60)) draft.theme.idledim=60;
    }
    draft.theme.savermsg=$('saver-msg').value.trim()||draft.theme.savermsg;
    markDirty();
    await save(true);
    await request('/api/sync',{});
    saveSVLocal();
    logHist('saver', `息屏设置已保存（${sv.mode==='always'?'常亮':dialDefs[sv.dial].name}）`);
    notice('息屏设置已保存并同步到设备');
    $('saver-state').textContent='已保存。设备应用后息屏画面即生效。';
    poll();
  }catch(err){ notice(err.message, true) }
}

/* ================= 页面编排（设置） ================= */
function renderPages() {
  const root=$('pages-list'); if(!root)return; root.replaceChildren();
  draft.pages.forEach((page,i) => {
    const b=el('button',`page-item${i===selected?' active':''}`); b.draggable=true;
    b.append(el('span','page-grip','⠿'),el('span','page-number',String(i+1).padStart(2,'0')),el('strong','',page.title+(i===0?' · 首页':'')),el('small','',`${page.widgets.length} 组件`));
    b.onclick=()=>{selected=i;renderSettings()};
    b.ondragstart=()=>{dragPage=i;b.classList.add('dragging')};
    b.ondragend=()=>{dragPage=-1;b.classList.remove('dragging')};
    b.ondragover=e=>e.preventDefault();
    b.ondrop=e=>{e.preventDefault();reorder(draft.pages,dragPage,i);selected=i};
    root.append(b);
  });
}
function renderPalette() {
  const root=$('widget-palette');root.replaceChildren();
  const page=current();
  const used=page.widgets.reduce((n,w)=>n+widgets[w][2],0);
  Object.entries(widgets).forEach(([key,[name,icon,units]])=>{
    const b=el('button','palette-item');b.append(el('span','palette-symbol',icon),document.createTextNode(name));
    b.disabled=page.widgets.includes(key)||used+units>4||(units===4&&used>0)||(used===4);
    b.onclick=()=>{current().widgets.push(key);markDirty()}; root.append(b);
  });
}
function renderInspector() {
  const page=current();
  $('page-title').value=page.title;$('page-layout').value=page.layout;
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
  $('card-opa').value=draft.theme.card_opa??100;$('opa-value').textContent=(draft.theme.card_opa??100)+'%';
  $('bright-auto').checked=!!Number(draft.theme.bright_auto??0);
  $('topclock-toggle').checked=!!Number(draft.theme.topclock??1);
  $('topwx-toggle').checked=!!Number(draft.theme.topwx??1);
  for(const key of ['bg','grad','card','accent','text']) $('theme-'+key).value='#'+draft.theme[key];
  $('theme-bright').value=draft.theme.bright;$('bright-value').textContent=draft.theme.bright+'%';
}
function renderSettings(){renderPages();renderPalette();renderInspector()}
function renderEntries(key){
  const root=$(key+'-list');root.replaceChildren();
  const items=draft[key];if(!items.length){root.append(el('div','empty',key==='calendar'?'暂无日程':key==='apps'?'尚未添加应用':key==='countdowns'?'尚未添加倒计时':'尚未添加'));return}
  items.forEach((item,i)=>{
    const row=el('div',`entry${key==='calendar'?' calendar':''}${key==='apps'?' app':key==='countdowns'?' countdowns':''}`);
    function input(type,value,field,placeholder){const n=el('input');n.type=type;n.value=value;n.placeholder=placeholder||'';n.onchange=()=>{item[field]=n.value;markDirty()};return n}
    if(key==='apps'){
      row.append(input('text',item.name,'name','应用名'),input('text',item.command,'command','启动命令，如 open -a Safari'));
      const launch=el('button','entry-launch','▶');launch.type='button';launch.title='一键启动';
      launch.setAttribute('aria-label',`启动 ${item.name||'应用'}`);
      launch.onclick=async()=>{
        try{
          const r=await request('/api/launch',{id:item.id});
          logHist('launch',`已启动应用「${item.name}」`);
          notice(r.status||'已启动');
        }catch(err){notice(err.message,true)}
      };
      row.append(launch);
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

/* ================= 概览 ================= */
/* 优化#8 筛选 + #9 Pin：都只作用于 Web 概览，不影响设备屏（设备仍按优先级取前 4） */
let ovFilter='all';
const PIN_KEY='workbuddy-pins';
function pinnedIds(){try{return JSON.parse(localStorage.getItem(PIN_KEY)||'[]')}catch(e){return []}}
function togglePin(id){
  const list=pinnedIds();
  const i=list.indexOf(id);
  if(i>=0)list.splice(i,1);else list.push(id);
  localStorage.setItem(PIN_KEY,JSON.stringify(list.slice(0,8)));
}
function sortTasks(tasks){
  const pins=pinnedIds();
  return [...tasks].sort((a,b)=>(pins.includes(b.id)?1:0)-(pins.includes(a.id)?1:0));
}
function renderFilters(sourceCount){
  const root=$('ov-filters');
  const chips=[['all','全部'],['alert','异常/等待'],...Object.entries(sourceCount).map(([k,n])=>[k,`${k} ${n}`])];
  root.replaceChildren();
  chips.forEach(([key,label])=>{
    const b=el('button','chip'+(ovFilter===key?' active':''));
    b.type='button';b.textContent=label;
    b.onclick=()=>{ovFilter=key;renderOverview()};
    root.append(b);
  });
}
/* 优化#2 抽屉：点击会话卡看全量字段；双击聚焦；右键 Pin */
function traceSegments(task, nowSec){
  /* 阶段事件 → 时间线段：[{s, dur}]，状态持续到下一事件或当前。
     覆盖最近 30 分钟；开头被窗口截断的段以 30 分钟为上限。 */
  const trace=task.trace||[];
  if(trace.length<1)return [];
  const WINDOW=1800;
  const start=Math.max(nowSec-WINDOW, trace[0].t);
  const segs=[];
  for(let i=0;i<trace.length;i++){
    const from=Math.max(start,trace[i].t);
    const to=i+1<trace.length?trace[i+1].t:nowSec;
    if(to<=start)continue;
    segs.push({s:trace[i].s,dur:Math.min(to,WINDOW+start)-from});
  }
  return segs;
}
function openTaskDrawer(task){
  const drawer=$('task-drawer'),scrim=$('drawer-scrim');
  const fields=[['智能体',task.tool],['项目',task.project||'未命名'],['会话',`#${task.id}`],
    ['状态',stateNames[task.state]||task.state],['本轮用时',fmtElapsed(task.elapsed)],
    ['最近活动',`${task.age} 秒前`],['进度',task.prog>=0?`${task.prog}%`:'估算中'],
    ['模型',(live.telemetry?.aiu||'').split(',')[1]||'—']];
  if(task.err)fields.push(['错误',String(task.err).slice(0,80)]);
  const root=$('drawer-fields');root.replaceChildren();
  fields.forEach(([k,v])=>{
    const pair=el('div','drawer-field');
    pair.append(el('dt','',k),el('dd','',v));
    root.append(pair);
  });
  // 优化#13：活动时间线——「瓶颈在想还是等」一眼可辨（最近 30 分钟）
  const segs=traceSegments(task,Math.floor(Date.now()/1000));
  const tl=el('div','trace-block');
  tl.append(el('div','trace-title','活动时间线（30 分钟）'));
  if(segs.length){
    const total=segs.reduce((n,x)=>n+x.dur,0)||1;
    const bar=el('div','trace-bar');
    segs.forEach(x=>{
      const seg=el('i',`trace-seg st-${x.s}`);
      seg.style.width=(x.dur/total*100).toFixed(2)+'%';
      seg.title=`${stateNames[x.s]||x.s} · ${fmtElapsed(x.dur)}`;
      bar.append(seg);
    });
    tl.append(bar);
    const legend=el('div','trace-legend');
    const byState={};
    segs.forEach(x=>byState[x.s]=(byState[x.s]||0)+x.dur);
    Object.entries(byState).sort((a,b)=>b[1]-a[1]).forEach(([s,d])=>{
      legend.append(el('span','trace-key',`${stateNames[s]||s} ${fmtElapsed(d)}`));
    });
    tl.append(legend);
  }else{
    tl.append(el('p','trace-empty','阶段事件积累中——状态每次切换会记一段（本客户端启动后的会话才有）。'));
  }
  root.append(tl);
  drawer.dataset.taskId=task.id||'';
  drawer.hidden=false;scrim.hidden=false;
}
function closeTaskDrawer(){$('task-drawer').hidden=true;$('drawer-scrim').hidden=true}
let drawerTimer=null;
/* 优化#7 聚焦：双击放大为全屏卡，Esc 退出 */
function openFocus(task){
  const hero=$('focus-hero');
  const cls={THINKING:'run',GENERATING:'run',TOOL:'run',BUILD:'run',DONE:'done',ERROR:'alert',WAIT_INPUT:'wait',ASK:'wait'}[task.state]||'run';
  hero.replaceChildren(
    el('span','focus-hero-symbol',(task.tool||'A').slice(0,1).toUpperCase()),
    el('h3','focus-hero-title',task.project||'未命名项目'),
    el('p','focus-hero-sub',`${task.tool} · 会话 #${task.id} · ${fmtElapsed(task.elapsed)}`),
    el('span',`task-state ${cls}`,stateNames[task.state]||task.state),
    el('p','focus-hero-prog',task.prog>=0?`进度 ${task.prog}%`:'进度估算中'));
  $('focus-stage').hidden=false;
}
function closeFocus(){$('focus-stage').hidden=true}
function renderOverview(){
  const t=live.telemetry||{}, tasks=sortTasks(live.tasks||[]);
  const connected=isConnected();
  $('ov-device-state').textContent=connected?'已连接':'未连接';
  $('ov-device-state').className='ov-device-state'+(connected?' ok':'');
  $('ov-device-sub').textContent=connected?live.device.replace('connected:','串口 '):'等待 ESP32-S3 小屏通过 USB 接入。';
  $('ov-version').textContent=`v${saved?.version||'—'}`;
  $('ov-applied').textContent=live.applied_version?`设备已应用 v${live.applied_version}`:'设备尚未应用配置';
  $('ov-task-count').textContent=`${tasks.length} 个会话`;
  // 优化#8：按来源统计生成筛选条；alert=只看 出错/等你
  const sourceCount={};
  tasks.forEach(task=>{const k=task.tool||'其他';sourceCount[k]=(sourceCount[k]||0)+1});
  renderFilters(sourceCount);
  const ALERT_STATES=['ERROR','WAIT_INPUT','ASK'];
  let shown=tasks;
  if(ovFilter==='alert')shown=tasks.filter(x=>ALERT_STATES.includes(x.state));
  else if(ovFilter!=='all')shown=tasks.filter(x=>(x.tool||'其他')===ovFilter);
  const root=$('ov-task-list');root.replaceChildren();
  if(!tasks.length){root.append(el('div','empty','暂无活跃会话。连接 Claude Code、Codex、ZCode 或 WorkBuddy 后将在这里显示。'))}
  else if(!shown.length){root.append(el('div','empty','当前筛选下没有会话。'))}
  else{
    // 优化#1：按来源（claude/codex/zcode/workbuddy）分窗格的多窗格监控，
    // 一眼分清每个 agent 在干什么，不用在混合列表里找。
    const STATE_CLS={THINKING:'run',GENERATING:'run',TOOL:'run',BUILD:'run',DONE:'done',ERROR:'alert',WAIT_INPUT:'wait',ASK:'wait'};
    const grid=el('div','agent-grid');
    const groups=new Map();
    shown.forEach(task=>{const k=task.tool||'其他';if(!groups.has(k))groups.set(k,[]);groups.get(k).push(task)});
    const pins=pinnedIds();
    groups.forEach((list,tool)=>{
      const pane=el('div','agent-pane');
      const head=el('div','agent-pane-head');
      head.append(el('strong','',tool),el('span','agent-pane-count',`${list.length} 项`));
      pane.append(head);
      list.forEach(task=>{
        const item=el('div','task-item');
        const symbol=el('span','task-symbol',task.tool?.slice(0,1).toUpperCase());
        const main=el('div','task-item-main');
        main.append(el('strong','',(pins.includes(task.id)?'📌 ':'')+(task.project||'未命名项目')),
          el('small','',`会话 #${task.id} · 已运行 ${fmtElapsed(task.elapsed)} · ${task.age} 秒前更新`));
        item.append(symbol,main,
          el('span',`task-state ${STATE_CLS[task.state]||'run'}`,stateNames[task.state]||task.state));
        // #2 抽屉（单击）/ #7 聚焦（双击）/ #9 Pin（右键）。
        // 单击延迟 260ms 开抽屉：若紧接着的第二击到来（双击），先取消抽屉，
        // 否则 scrim 盖在卡片上，dblclick 永远派发不到目标。
        item.onclick=e=>{if(e.detail!==1)return;
          clearTimeout(drawerTimer);drawerTimer=setTimeout(()=>openTaskDrawer(task),260)};
        item.ondblclick=e=>{e.preventDefault();clearTimeout(drawerTimer);closeTaskDrawer();openFocus(task)};
        item.oncontextmenu=e=>{e.preventDefault();togglePin(task.id);
          notice(pins.includes(task.id)?'已取消置顶':'已置顶，永远排在最前');renderOverview()};
        pane.append(item);
      });
      grid.append(pane);
    });
    root.append(grid);
  }
  $('ov-cpu').textContent=t.cpu===undefined?'—':`${t.cpu}%`;
  $('ov-mem').textContent=t.mem===undefined?'—':`${t.mem}%`;
  $('ov-mem-sub').textContent=t.memtotal?`${t.memused} / ${t.memtotal} GB`:'数据不可用';
  $('ov-disk').textContent=t.disk===undefined?'—':`${t.disk}%`;
  $('ov-disk-sub').textContent=t.diskfree?`可用 ${t.diskfree} GB`:'—';
  $('ov-net').textContent=t.netrx===undefined?'—':`${t.netrx} / ${t.nettx}`;
  $('ov-net-sub').textContent='Mbps ↓ / ↑';
  $('ov-usage').textContent=t.ait===undefined?'—':`${String(t.ait).split(',')[0]}k token`;
  $('ov-weather').textContent=t.wx_temp===undefined?'—':`${t.wx_temp} ℃`;
  $('ov-remind').textContent=t.remind||'—';
  $('ov-fresh-pill').textContent=typeof live.telemetry_age==='number'?`数据延迟 ${Math.round(live.telemetry_age)} 秒`:'—';
  if(live.paused)$('ov-fresh-pill').textContent='已暂停采集';
  drawUsageChart();
  renderSyncState();
  renderConnState();
}
/* 优化#14：今日 token 每小时用量折线（canvas 本地绘制，无外部库、CSP 安全）。
   数据点来自 /api/status 的 usage.today_k，按小时分桶存 localStorage（保留 24 桶）。 */
const USAGE_KEY='workbuddy-usage-hist';
function usageHistory(){
  try{return JSON.parse(localStorage.getItem(USAGE_KEY)||'[]')}catch(e){return []}
}
function sampleUsage(){
  const u=live.usage;
  const k=u?.today?.total!=null?Math.round(u.today.total/1000):null;
  if(k==null)return;
  const hourBucket=Math.floor(Date.now()/3600000);
  const hist=usageHistory().filter(p=>hourBucket-p.h<24);
  const last=hist[hist.length-1];
  if(last&&last.h===hourBucket)last.k=Math.max(last.k,k);
  else hist.push({h:hourBucket,k});
  try{localStorage.setItem(USAGE_KEY,JSON.stringify(hist.slice(-24)))}catch(e){}
}
function drawUsageChart(){
  const canvas=$('usage-chart');if(!canvas||!canvas.getContext)return;
  const dpr=window.devicePixelRatio||1;
  const w=canvas.clientWidth||canvas.width,h=72;
  if(canvas.width!==Math.round(w*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr)}
  const ctx=canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  const hist=usageHistory();
  const cs=getComputedStyle(document.documentElement);
  const accent=cs.getPropertyValue('--accent').trim()||'#2c8568';
  const line=cs.getPropertyValue('--line-strong').trim()||'#cbd8d6';
  const sub=cs.getPropertyValue('--text-tertiary').trim()||'#5e6e76';
  if(hist.length<2){
    ctx.fillStyle=sub;ctx.font='11px sans-serif';ctx.textAlign='center';
    ctx.fillText('用量数据积累中（每小时一个点）',w/2,h/2);
    return;
  }
  const maxK=Math.max(...hist.map(p=>p.k),10);
  const pad=4,ih=h-pad*2;
  // 网格线（三条）
  ctx.strokeStyle=line;ctx.lineWidth=1;
  for(let i=0;i<=2;i++){const y=pad+ih*i/2;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke()}
  // 折线 + 渐变填充
  const pts=hist.map((p,i)=>[w*i/(hist.length-1),pad+ih*(1-p.k/maxK)]);
  ctx.beginPath();pts.forEach(([x,y],i)=>i?ctx.lineTo(x,y):ctx.moveTo(x,y));
  ctx.strokeStyle=accent;ctx.lineWidth=2;ctx.lineJoin='round';ctx.stroke();
  const grad=ctx.createLinearGradient(0,0,0,h);
  grad.addColorStop(0,accent);grad.addColorStop(1,'transparent');
  ctx.globalAlpha=.14;ctx.lineTo(w,h);ctx.lineTo(0,h);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
  ctx.globalAlpha=1;
  // 末点 + 峰值标注
  const [ex,ey]=pts[pts.length-1];
  ctx.beginPath();ctx.arc(ex,ey,3,0,7);ctx.fillStyle=accent;ctx.fill();
  ctx.fillStyle=sub;ctx.font='10px sans-serif';ctx.textAlign='right';
  ctx.fillText(`峰值 ${maxK}k`,w-2,10);
}
function renderSyncState(){
  if(!$('ov-sync-state'))return;
  $('ov-sync-state').textContent=dirty?'未保存草稿':(live.profile_status||'—');
}
function renderConnState(){
  const connected=isConnected();
  $('conn-dot').classList.toggle('on',connected);
  $('conn-text').textContent=connected?'小屏已连接':'小屏未连接';
  $('ver-text').textContent=`版本 v${saved?.version||'—'} · LVGL 8.3`;
}

/* ================= 视图路由 ================= */
function renderExtras(){['reminders','calendar','alarms','countdowns','apps'].forEach(key=>{if(draft&&draft[key])renderEntries(key)})}
function switchView(name){
  currentView=name;
  document.querySelectorAll('.nav-item').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id==='view-'+name));
  localStorage.setItem('workbuddy-view',name);
  // 草稿在首次 /api/status 返回后才加载；恢复上次视图时 draft 可能还是 null，
  // 依赖 draft 的渲染推迟到 poll() 里的 render()。
  if(draft){
    if(name==='settings')renderSettings();
    if(name==='saver')renderSaverView();
    if(name==='tasks')renderTaskView();
    if(name==='extras')renderExtras();
  }
  if(name==='overview')renderOverview();
  if(name==='history')renderHistory();
  window.scrollTo({top:0,behavior:'smooth'});
}
function renderTaskView(){renderCfgPanels();renderTaskPreview();renderThemeGrid();if(!$('pane-multi').hidden)renderMultiPane()}

/* ================= 轮询与保存 ================= */
function render(){renderOverview();if(currentView==='tasks')renderTaskView();if(currentView==='saver')renderSaverView();if(currentView==='extras')renderExtras()}
async function poll(){
  if(polling)return;
  polling=true;
  try{
    const next=await request('/api/status');
    lastOkAt=Date.now();failStreak=0;
    absorb(next);
  }catch(e){
    failStreak++;
    markStale(`与客户端的连接已中断（${failStreak} 次）`);
    if(failStreak===1)notice('无法读取客户端状态：'+e.message,true);
  }finally{
    polling=false;
  }
}
/* 状态吸收：/api/status 轮询与 /api/stream SSE（优化#3）共用的落地逻辑 */
function absorb(next){
  live=next;
  if(!draft){saved=next.profile;const local=JSON.parse(localStorage.getItem('workbuddy-draft')||'null');draft=local?.base===saved.version?local.draft:structuredClone(saved);dirty=!!local&&local.base===saved.version}
  notifyTaskTransitions(live.tasks||[]);
  sampleUsage();
  if(currentView==='saver'&&!$('btn-saver-detail').dataset.init){renderSaverView();$('btn-saver-detail').dataset.init='1'}
  render();
  updateFreshness();
}
/* 优化#10：任务跨过关键节点（完成/出错/等你）时主动 toast。
   只在「状态变化 → 终态」边沿触发；轮询/SSE 重复投递同一状态不会重复提醒。 */
const NOTIFY_STATES={DONE:'已完成 ✓',ERROR:'出错 ✗',WAIT_INPUT:'等你回复',ASK:'等你确认'};
let taskStates={};
function notifyTaskTransitions(tasks){
  const next={};
  tasks.forEach(t=>{ if(t&&t.id) next[t.id]=t.state; });
  for(const id in taskStates){
    const before=taskStates[id], now=next[id];
    if(now&&now!==before&&NOTIFY_STATES[now]){
      const task=tasks.find(x=>x.id===id);
      const name=task?`${task.tool||'AI'} · ${task.project||'会话 '+id}`:id;
      notice(`${name} ${NOTIFY_STATES[now]}`, now==='ERROR');
    }
  }
  taskStates=next;
}
const STALE_AFTER=8000;
function isStale(){
  const silent=Date.now()-lastOkAt;
  const age=live?.telemetry_age;
  return silent>STALE_AFTER || (typeof age==='number' && age>STALE_AFTER);
}
function markStale(reason){
  document.body.classList.add('is-stale');
  const el=$('freshness');if(!el)return;
  el.textContent=reason;el.title=reason;
  el.hidden=false;
}
function markFresh(){
  document.body.classList.remove('is-stale');
  const el=$('freshness');if(el)el.hidden=true;
}
function updateFreshness(){
  if(isStale()){
    const age=live?.telemetry_age;
    markStale(age!=null?`数据已 ${Math.round(age)} 秒未更新`:`已 ${Math.round((Date.now()-lastOkAt)/1000)} 秒未收到数据`);
    return;
  }
  const t=live?.telemetry;
  const el=$('freshness');if(!el)return;
  if(t&&t.time){
    const hh=String(t.time).padStart(6,'0');
    el.textContent=`数据 ${hh.slice(0,2)}:${hh.slice(2,4)}:${hh.slice(4,6)}`;
    el.title='客户端本机时间，用于核对数据是否为最新';
    el.hidden=false;
  }else{el.hidden=true}
  markFresh();
}
async function save(silent=false){
  const check=await request('/api/validate',draft);
  const result=await request('/api/save',check.profile);
  saved=result.profile;draft=structuredClone(saved);dirty=false;localStorage.removeItem('workbuddy-draft');
  renderSyncState();renderConnState();
  if(currentView==='settings')renderSettings();
  if(!silent)notice('配置已保存，等待同步到设备');
  logHist('save', `配置 v${result.profile.version} 已保存`);
  return result;
}
function bind(){
  // 视图路由
  document.querySelectorAll('.nav-item').forEach(b=>b.onclick=()=>switchView(b.dataset.view));
  const lastView=localStorage.getItem('workbuddy-view');
  if(lastView&&document.getElementById('view-'+lastView))switchView(lastView);

  // 命令面板 & 快捷键帮助（优化#5/#6）
  $('btn-cmd-hint').onclick=()=>openCmd(true);
  $('kbd-close').onclick=()=>toggleKbd(false);
  $('cmd-overlay').onclick=e=>{if(e.target===$('cmd-overlay'))openCmd(false)};
  $('kbd-overlay').onclick=e=>{if(e.target===$('kbd-overlay'))toggleKbd(false)};
  $('cmd-input').oninput=e=>renderCmdList(e.target.value);
  // 会话抽屉关闭（#2）：点 scrim 或 ×
  $('drawer-close').onclick=closeTaskDrawer;
  $('drawer-scrim').onclick=closeTaskDrawer;
  // 聚焦模式整屏点击退出（Esc 之外的多一路径）
  $('focus-stage').onclick=closeFocus;

  // 外观四态（优化#19：新增「护眼」暖纸白+黛蓝档）
  const MODE_KEY='workbuddy-color-mode';
  const modes=['auto','light','dark','eye'], modeNames={auto:'跟随系统',light:'浅色',dark:'深色',eye:'护眼'};
  const applyMode=mode=>{
    document.documentElement.dataset.theme=mode;
    localStorage.setItem(MODE_KEY,mode);
    $('color-mode-btn').textContent=`外观：${modeNames[mode]}（点击切换）`;
  };
  applyMode(localStorage.getItem(MODE_KEY)||'auto');
  $('color-mode-btn').onclick=()=>{
    const next=modes[(modes.indexOf(document.documentElement.dataset.theme||'auto')+1)%modes.length];
    applyMode(next); notice(`外观已切换为「${modeNames[next]}」`);
  };

  const pending=(btn,fn)=>async(...args)=>{
    if(btn.dataset.loading==='true')return;
    btn.dataset.loading='true';
    try{return await fn(...args)}finally{delete btn.dataset.loading}
  };

  // ---- 概览 ----
  $('ov-save-btn').onclick=pending($('ov-save-btn'),async()=>{try{await save()}catch(e){notice(e.message,true)}});
  $('ov-sync-btn').onclick=pending($('ov-sync-btn'),async()=>{
    try{if(dirty)await save();await request('/api/sync',{});logHist('sync','已请求同步到设备');notice('已请求同步，等待设备应用回执');poll()}
    catch(e){notice(e.message,true)}});
  $('ov-act-tasks').onclick=()=>switchView('tasks');
  $('ov-act-saver').onclick=()=>switchView('saver');
  $('ov-act-bg').onclick=()=>switchView('extras');
  $('ov-act-diag').onclick=pending($('ov-act-diag'),async()=>{
    try{await downloadFile('/api/diagnose','workbuddy-diagnose.zip');logHist('diag','诊断包已导出');notice('诊断包已开始下载')}
    catch(e){notice(e.message,true)}});

  // ---- 任务视图 ----
  // 默认落「状态聚焦」而非实时状态：预览直接呈现效果图布局，
  // 右侧配置面板也有内容（实时状态无配置项，右半屏会大面积留白）。
  $('scheme-select').value=['live','focus','timeline','companion','dashboard'].includes(tv.scheme)?tv.scheme:'focus';
  $('scheme-select').onchange=e=>{
    tv.scheme=e.target.value;
    if(tv.scheme!=='live')tv.layout=tv.scheme;
    saveTV();renderCfgPanels();renderTaskPreview();
  };
  document.querySelectorAll('.layout-btn').forEach(b=>b.onclick=()=>{
    tv.layout=b.dataset.layout;tv.scheme=b.dataset.layout;
    $('scheme-select').value=tv.scheme;saveTV();
    renderCfgPanels();renderTaskPreview();
  });
  $('btn-reset-layout').onclick=()=>{
    tv=structuredClone(tvDefaults);saveTV();
    $('scheme-select').value='live';
    hydrateTaskControls();renderCfgPanels();renderTaskPreview();
    notice('已恢复默认布局');
  };
  $('tab-layouts').onclick=()=>switchTaskTab('layouts');
  $('tab-themes').onclick=()=>switchTaskTab('themes');
  $('tab-multi').onclick=()=>switchTaskTab('multi');
  $('btn-multi-edit').onclick=()=>switchView('settings');
  $('tab-preset-themes').onclick=()=>{themeTab='preset';selectedThemeKey=null;
    $('tab-preset-themes').classList.add('active');$('tab-my-themes').classList.remove('active');
    $('tab-preset-themes').setAttribute('aria-selected','true');$('tab-my-themes').setAttribute('aria-selected','false');
    renderThemeGrid()};
  $('tab-my-themes').onclick=()=>{themeTab='my';selectedThemeKey=null;
    $('tab-my-themes').classList.add('active');$('tab-preset-themes').classList.remove('active');
    $('tab-my-themes').setAttribute('aria-selected','true');$('tab-preset-themes').setAttribute('aria-selected','false');
    renderThemeGrid()};
  $('btn-theme-options').onclick=()=>switchView('settings');
  $('btn-apply-theme').onclick=pending($('btn-apply-theme'),applySelectedTheme);
  $('btn-crown').onclick=()=>{
    tv.scheme='timeline';tv.layout='timeline';$('scheme-select').value='timeline';saveTV();
    renderCfgPanels();renderTaskPreview();
    $('crown-mode').focus();
  };
  $('btn-source-jump').onclick=()=>switchView('overview');
  $('btn-deep-edit').onclick=()=>switchView('settings');
  $('btn-apply-task').onclick=pending($('btn-apply-task'),async()=>{
    try{
      const presets={
        focus:[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']}],
        timeline:[{id:'ai',title:'AI 时间线',layout:'list',widgets:['ai_list']}],
        companion:[{id:'ai',title:crownName(),layout:'grid_2x2',widgets:['clock','weather']}],
        dashboard:[{id:'ai',title:'数据看板',layout:'list',widgets:['ai_list']},{id:'pc',title:'电脑',layout:'grid_2x2',widgets:['hw_cards']}]
      };
      if(tv.scheme!=='live'){
        draft.pages=structuredClone(presets[tv.layout]||presets.focus);
        selected=0;markDirty();
      }
      await save(true);
      await request('/api/sync',{});
      logHist('layout', `布局「${schemeNames[tv.scheme]}」已应用到任务`);
      notice(`「${schemeNames[tv.scheme]}」已应用到任务并同步`);
      poll();
    }catch(e){notice(e.message,true)}
  });
  // 布局配置控件 ↔ 状态
  bindRadios('fv-supplement',v=>tv.fv.supplement=v);
  bindRadios('fv-value',v=>tv.fv.value=v);
  bindRadios('fv-others',v=>tv.fv.others=v);
  bindRadios('fv-idle',v=>tv.fv.idle=v);
  bindRadios('tv-range',v=>tv.tv.range=v);
  bindRadios('tv-time',v=>tv.tv.time=v);
  bindRadios('dv-combo',v=>tv.dv.combo=v);
  bindRadios('dv-middle',v=>tv.dv.middle=v);
  const dvIds={tok:'dm-tok',model:'dm-model',task:'dm-task',elapsed:'dm-elapsed',cpu:'dm-cpu',mem:'dm-mem',disk:'dm-disk',net:'dm-net'};
  Object.entries(dvIds).forEach(([k,id])=>{$(id).onchange=e=>{tv.dv.metrics[k]=e.target.checked;saveTV();renderTaskPreview()}});
  const cpToggles={daynight:'cp-daynight',weather:'cp-weather',fx:'cp-fx',action:'cp-action',feedback:'cp-feedback',rest:'cp-rest'};
  Object.entries(cpToggles).forEach(([k,id])=>{$(id).onchange=e=>{tv.cp[k]=e.target.checked;saveTV();renderTaskPreview()}});
  const cpDisp={name:'cpd-name',elapsed:'cpd-elapsed',model:'cpd-model',tok:'cpd-tok',state:'cpd-state'};
  Object.entries(cpDisp).forEach(([k,id])=>{$(id).onchange=e=>{tv.cp.disp[k]=e.target.checked;saveTV();renderTaskPreview()}});
  $('scene-select').onchange=e=>{tv.cp.scene=e.target.value;saveTV();renderTaskPreview()};
  $('crown-mode').onchange=e=>{tv.crown.mode=e.target.value;saveTV();renderTaskPreview()};
  $('crown-text').oninput=e=>{tv.crown.text=e.target.value;saveTV();renderTaskPreview()};
  $('btn-layout-export').onclick=()=>{
    const blob=new Blob([JSON.stringify(tv,null,2)],{type:'application/json'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);
    a.download='maozi-layout.json';a.click();URL.revokeObjectURL(a.href);
    logHist('export','布局配置已导出');notice('布局已导出为 JSON 文件');
  };
  $('btn-layout-import').onclick=()=>$('layout-file').click();
  $('layout-file').onchange=async e=>{
    const file=e.target.files[0];e.target.value='';
    if(!file)return;
    try{
      const data=JSON.parse(await file.text());
      tv=structuredClone({...tvDefaults,...data,
        fv:{...tvDefaults.fv,...(data.fv||{})},tv:{...tvDefaults.tv,...(data.tv||{})},
        crown:{...tvDefaults.crown,...(data.crown||{})},
        cp:{...tvDefaults.cp,...(data.cp||{}),disp:{...tvDefaults.cp.disp,...(data.cp?.disp||{})}},
        dv:{...tvDefaults.dv,...(data.dv||{}),metrics:{...tvDefaults.dv.metrics,...(data.dv?.metrics||{})}}});
      saveTV();hydrateTaskControls();$('scheme-select').value=tv.scheme||'live';
      renderCfgPanels();renderTaskPreview();
      logHist('import','布局配置已导入');notice('布局已导入');
    }catch(err){notice('布局文件无效：'+err.message,true)}
  };
  hydrateTaskControls();

  // ---- 息屏 ----
  loadSV();
  $('saver-mode').onchange=e=>{sv.mode=e.target.value;saveSVLocal();renderSaverView()};
  $('saver-hide-lock').onchange=e=>{sv.hideLock=e.target.checked;saveSVLocal()};
  $('btn-saver-detail').onclick=()=>{
    const d=$('btn-saver-detail');
    d.dataset.open=d.dataset.open==='1'?'':'1';
    d.textContent=d.dataset.open==='1'?'收起表盘细节':'编辑表盘细节';
    renderSaverView();
  };
  $('saver-idle').oninput=e=>{draft.theme.idledim=Number(e.target.value);
    $('saver-idle-val').textContent=Number(e.target.value)==0?'关闭':(e.target.value+' 秒');markDirty()};
  $('saver-msg').onchange=e=>{draft.theme.savermsg=e.target.value;markDirty()};
  $('topclock-toggle').onchange=e=>{draft.theme.topclock=e.target.checked?1:0;markDirty()};
  $('topwx-toggle').onchange=e=>{draft.theme.topwx=e.target.checked?1:0;markDirty()};
  $('btn-saver-save').onclick=pending($('btn-saver-save'),applySaver);
  $('btn-saver-cancel').onclick=()=>{sv.dial=draft.theme.saver??0;sv.mode='theme';saveSVLocal();renderSaverView();notice('已还原为当前配置的息屏设置')};

  // ---- 页面编排 ----
  $('add-page').onclick=()=>{if(draft.pages.length>=6)return notice('最多 6 页',true);draft.pages.push({id:uniqueId('p'),title:'新页面',layout:'grid_2x2',widgets:['clock']});selected=draft.pages.length-1;markDirty()};
  $('page-title').onchange=e=>{current().title=e.target.value;markDirty()};
  $('page-layout').onchange=e=>{current().layout=e.target.value;markDirty()};
  $('duplicate-page').onclick=()=>{if(draft.pages.length>=6)return notice('最多 6 页',true);const copy=structuredClone(current());copy.id=uniqueId('p');copy.title+=' 副本';draft.pages.splice(selected+1,0,copy);selected++;markDirty()};
  $('delete-page').onclick=()=>{if(draft.pages.length===1)return notice('至少保留 1 页',true);draft.pages.splice(selected,1);selected=Math.max(0,selected-1);markDirty()};
  const startTemplates={
    '专注模式':[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']}],
    '数据控':[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']},{id:'pc',title:'电脑',layout:'grid_2x2',widgets:['hw_cards']}],
    '陪伴模式':[{id:'ai',title:'AI',layout:'list',widgets:['ai_list']},{id:'env',title:'环境',layout:'grid_2x2',widgets:['environment','weather']},{id:'today',title:'今天',layout:'grid_2x2',widgets:['today_calendar','reminder_list']}],
    '极简时钟':[{id:'clock',title:'时钟',layout:'grid_2x2',widgets:['clock']}]
  };
  const presetRoot=$('start-templates');presetRoot.replaceChildren();
  Object.entries(startTemplates).forEach(([name,pages])=>{
    const b=el('button','button ghost',name);
    b.onclick=()=>{draft.pages=structuredClone(pages);selected=0;markDirty();notice(`已套用「${name}」模板，保存并同步生效`)};
    presetRoot.append(b);
  });

  // ---- 主题 ----
  const presets=$('theme-presets');presets.replaceChildren();
  Object.entries(themePresets).forEach(([name,colors])=>{
    const b=el('button','preset-swatch');b.title=name;
    b.style.background=`linear-gradient(160deg,#${colors.bg},#${colors.grad} 60%,#${colors.accent})`;
    b.onclick=()=>{Object.assign(draft.theme,colors,{bright:draft.theme.bright});markDirty();notice(`已应用「${name}」主题，保存并同步后生效`)};
    presets.append(b);
  });
  const gal=$('theme-gallery');gal.replaceChildren();
  Object.entries(galleryThemes).forEach(([key,th])=>{
    const cell=el('button','gallery-item');
    cell.type='button';
    const thumb=document.createElement('canvas');thumb.width=64;thumb.height=96;
    thumb.className='gallery-thumb';
    thumb.setAttribute('aria-hidden','true');
    const ctx=thumb.getContext('2d');ctx.save();ctx.scale(64/320,96/480);
    try{drawGallery(ctx,320,480,key)}catch(e){}
    ctx.restore();
    cell.append(thumb,el('span','',th.name));
    cell.title='点击应用：推送背景+自动配色';
    cell.onclick=async()=>{
      try{
        if(isConnected()){
          const c=document.createElement('canvas');c.width=320;c.height=480;
          drawGallery(c.getContext('2d'),320,480,key);
          const blob=await new Promise(ok=>c.toBlob(ok,'image/jpeg',0.82));
          const r=await fetch('/api/bgimg',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
          if(!r.ok){const d=await r.json();throw new Error(d.error||'背景推送失败')}
          logHist('bg',`画廊背景「${th.name}」已推送`);
        }
        Object.assign(draft.theme,th.colors,{card_opa:th.card_opa});
        markDirty();await save(true);await request('/api/sync',{});
        logHist('theme',`画廊主题「${th.name}」已应用`);
        notice(`「${th.name}」主题已应用：背景+配色+卡片透明度`);
        poll();
      }catch(err){notice(err.message,true)}
    };
    gal.append(cell);
  });
  for(const key of ['bg','grad','card','accent','text'])$('theme-'+key).oninput=e=>{draft.theme[key]=e.target.value.slice(1).toUpperCase();markDirty()};
  $('theme-bright').oninput=e=>{draft.theme.bright=Number(e.target.value);markDirty()};
  $('card-opa').oninput=e=>{draft.theme.card_opa=Number(e.target.value);$('opa-value').textContent=e.target.value+'%';markDirty()};
  $('bright-auto').onchange=e=>{draft.theme.bright_auto=e.target.checked?1:0;markDirty();notice(e.target.checked?'已开启自动亮度（白天亮/夜间暗）':'已切回手动亮度')};
  $('btn-save-mytheme').onclick=()=>{
    const list=myThemes();
    const name='我的主题 '+(list.length+1);
    list.push({name, colors:{bg:draft.theme.bg,grad:draft.theme.grad,card:draft.theme.card,card_edge:draft.theme.card_edge,accent:draft.theme.accent,text:draft.theme.text,sub:draft.theme.sub}, opa:draft.theme.card_opa});
    localStorage.setItem('workbuddy-mythemes',JSON.stringify(list));
    logHist('theme',`自定义主题「${name}」已保存`);
    notice(`已保存「${name}」，可在任务视图 → 主题库 → 自定义主题中应用`);
  };

  // ---- 其他视图 ----
  document.querySelectorAll('[data-add]').forEach(button=>button.onclick=()=>{
    const key=button.dataset.add, today=new Date().toLocaleDateString('en-CA');
    draft[key].push(key==='calendar'?{id:uniqueId('e'),date:today,time:'09:00',title:'新日程',enabled:true}:
      key==='alarms'?{id:uniqueId('a'),time:'09:00',label:'新闹钟',weekdays:[1,2,3,4,5,6,7],enabled:true}:
      key==='apps'?{id:uniqueId('app'),name:'新应用',command:'open -a Finder'}:
      key==='countdowns'?{id:uniqueId('c'),date:today,time:'20:00',title:'新目标',enabled:true}:
        {id:uniqueId('r'),time:'09:00',text:'新提醒',weekdays:[1,2,3,4,5,6,7],enabled:true});markDirty()
  });
  $('weather-city').onchange=e=>{if(!draft.weather)draft.weather={};draft.weather.city=e.target.value.trim();markDirty()};
  $('bgimg-file').onchange=async e=>{
    const file=e.target.files[0];e.target.value='';
    if(!file)return;
    if(!isConnected()){notice('设备未连接，先等设备上线',true);return}
    notice('正在处理图片…');
    try{
      // onerror 收到的是 Event 而不是 Error，必须包一层，否则 err.message 是 undefined。
      const img=await new Promise((ok,bad)=>{
        const i=new Image();
        i.onload=()=>{URL.revokeObjectURL(i.src);ok(i)};
        i.onerror=()=>bad(new Error('图片解码失败，请换一张 JPG/PNG'));
        i.src=URL.createObjectURL(file);
      });
      const canvas=document.createElement('canvas');canvas.width=320;canvas.height=480;
      const ctx=canvas.getContext('2d');
      const scale=Math.max(320/img.width,480/img.height);
      const w=img.width*scale,h=img.height*scale;
      ctx.drawImage(img,(320-w)/2,(480-h)/2,w,h);
      const blob=await new Promise(ok=>canvas.toBlob(ok,'image/jpeg',0.72));
      const r=await fetch('/api/bgimg',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
      const d=await r.json();
      if(!r.ok)throw new Error(d.error||'发送失败');
      logHist('bg','自定义背景图已发送');
      notice('背景图已发送，等待设备应用');
    }catch(err){notice('背景图失败：'+err.message,true)}
  };
  $('bgimg-clear').onclick=async()=>{
    try{const r=await fetch('/api/bgimg-clear',{method:'POST'});const d=await r.json();
    if(!r.ok)throw new Error(d.error||'失败');logHist('bg','背景图已清除');notice(d.status)}
    catch(err){notice(err.message,true)}
  };

  // ---- Hook 快通道接入（采集第 2 层） ----
  async function refreshHooks(){
    const elState=$('hooks-state');
    try{
      const s=await request('/api/hooks-status');
      if(!s.settings_found){elState.textContent='未找到 Claude Code（~/.claude）';return}
      elState.textContent=s.wired?'已接入':'未接入';
      if(s.error)elState.textContent=s.error;
      $('btn-hooks-install').disabled=s.wired;
      $('btn-hooks-uninstall').disabled=!s.events.length;
    }catch(e){elState.textContent='状态未知'}
  }
  $('btn-hooks-install').onclick=pending($('btn-hooks-install'),async()=>{
    try{
      const r=await request('/api/hooks-install',{});
      const done=r.installed||[], skip=r.skipped||[];
      notice(done.length?`已接入 ${done.join('、')}（重启 Claude Code 会话后生效）`:'早已接入，无需重复');
      if(skip.length)notice(`本次跳过：${skip.join('、')}（已存在）`);
      logHist('hook',`Hook 快通道已接入（${done.length||0} 项）`);
      refreshHooks();
    }catch(e){notice(e.message,true)}
  });
  $('btn-hooks-uninstall').onclick=pending($('btn-hooks-uninstall'),async()=>{
    try{
      const r=await request('/api/hooks-uninstall',{});
      notice((r.removed&&r.removed.length)?`已移除 ${r.removed.join('、')}`:'未发现接入标记');
      logHist('hook','Hook 快通道已移除');
      refreshHooks();
    }catch(e){notice(e.message,true)}
  });
  refreshHooks();

  // ---- 历史记录 ----
  $('btn-history-clear').onclick=()=>{localStorage.removeItem(HIST_KEY);renderHistory();notice('历史已清空')};

  // ---- 设置：保存/同步/维护 ----
  $('validate-btn').onclick=pending($('validate-btn'),async()=>{try{await request('/api/validate',draft);notice('配置检查通过，可以保存')}catch(e){notice(e.message,true)}});
  $('save-btn').onclick=pending($('save-btn'),async()=>{try{await save()}catch(e){notice(e.message,true)}});
  $('sync-btn').onclick=pending($('sync-btn'),async()=>{try{if(dirty)await save();await request('/api/sync',{});logHist('sync','已请求同步到设备');notice('已请求同步，等待设备应用回执');poll()}catch(e){notice(e.message,true)}});
  $('reset-btn').onclick=()=>{if(!confirm('恢复默认档案？当前未保存修改会丢失。'))return;draft={schema:1,version:saved.version,pages:[{id:'ai',title:'AI 工作台',layout:'list',widgets:['ai_list']},{id:'computer',title:'电脑',layout:'grid_2x2',widgets:['hw_cards']},{id:'environment',title:'环境',layout:'grid_2x2',widgets:['environment','clock']}],reminders:[],calendar:[],alarms:[],apps:[],theme:{bg:'0B1220',grad:'0E1B33',card:'274064',card_edge:'3E506E',accent:'22D3EE',text:'F3F7FF',sub:'A8BDD9',bright:90}};selected=0;markDirty()};
  $('btn-export').onclick=async()=>{
    try{
      const data=await request('/api/export');
      const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);
      a.download='workbuddy-profile.json';a.click();URL.revokeObjectURL(a.href);
      logHist('export',`配置 v${data.version} 已导出`);notice('配置已导出');
    }catch(e){notice(e.message,true)}
  };
  $('import-file').onchange=async e=>{
    const file=e.target.files[0];e.target.value='';
    if(!file)return;
    try{
      const data=JSON.parse(await file.text());
      const r=await request('/api/import',data);
      logHist('import',`配置 v${r.profile?.version||'?'} 已导入`);
      notice('配置已导入并保存');saved=r.profile;draft=structuredClone(r.profile);dirty=false;poll();
    }catch(err){notice('导入失败：'+err.message,true)}
  };
  $('ota-file').onchange=async e=>{
    const file=e.target.files[0];e.target.value='';
    if(!file)return;
    if(!isConnected()){notice('设备未连接，无法升级固件',true);return}
    if(!confirm(`确认向设备发送固件 ${file.name}（${Math.round(file.size/1024)} KB）？`))return;
    notice('正在发送固件，请勿断开设备…');
    try{
      const r=await fetch('/api/ota',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await file.arrayBuffer()});
      const d=await r.json();
      if(!r.ok)throw new Error(d.error||'升级失败');
      logHist('ota','固件包已发送');
      notice('固件已发送，设备将自行完成升级');
    }catch(err){notice('固件升级失败：'+err.message,true);logHist('ota','固件升级失败',false)}
  };
  $('diag-btn').onclick=pending($('diag-btn'),async()=>{
    try{await downloadFile('/api/diagnose','workbuddy-diagnose.zip');logHist('diag','诊断包已导出');notice('诊断包已开始下载')}
    catch(e){notice(e.message,true)}});
  $('autostart-toggle').onchange=async e=>{
    try{const r=await request('/api/autostart',{enable:e.target.checked});
      e.target.checked=r.enabled;notice(r.enabled?'已开启开机自启':'已关闭开机自启')}
    catch(err){e.target.checked=!e.target.checked;notice(err.message,true)}
  };
}
function bindRadios(name,setter){
  document.querySelectorAll(`input[name="${name}"]`).forEach(r=>{
    r.onchange=e=>{if(e.target.checked){setter(e.target.value);saveTV();renderTaskPreview()}};
  });
}
function hydrateTaskControls(){
  const setRadio=(name,v)=>{const r=document.querySelector(`input[name="${name}"][value="${v}"]`);if(r)r.checked=true};
  setRadio('fv-supplement',tv.fv.supplement);setRadio('fv-value',tv.fv.value);
  setRadio('fv-others',tv.fv.others);setRadio('fv-idle',tv.fv.idle);
  setRadio('tv-range',tv.tv.range);setRadio('tv-time',tv.tv.time);
  setRadio('dv-combo',tv.dv.combo);setRadio('dv-middle',tv.dv.middle);
  const dvIds={tok:'dm-tok',model:'dm-model',task:'dm-task',elapsed:'dm-elapsed',cpu:'dm-cpu',mem:'dm-mem',disk:'dm-disk',net:'dm-net'};
  Object.entries(dvIds).forEach(([k,id])=>{$(id).checked=!!tv.dv.metrics[k]});
  $('cp-daynight').checked=!!tv.cp.daynight;$('cp-weather').checked=!!tv.cp.weather;
  $('cp-fx').checked=!!tv.cp.fx;$('cp-action').checked=!!tv.cp.action;
  $('cp-feedback').checked=!!tv.cp.feedback;$('cp-rest').checked=!!tv.cp.rest;
  $('cpd-name').checked=!!tv.cp.disp.name;$('cpd-elapsed').checked=!!tv.cp.disp.elapsed;
  $('cpd-model').checked=!!tv.cp.disp.model;$('cpd-tok').checked=!!tv.cp.disp.tok;
  $('cpd-state').checked=!!tv.cp.disp.state;
  $('scene-select').value=tv.cp.scene;
  $('crown-mode').value=tv.crown.mode;$('crown-text').value=tv.crown.text;
  const sel=Object.entries(tv.dv.metrics).filter(([,v])=>v).length;
  $('dv-metric-count').textContent=`已选 ${sel} 项`;
}
function switchTaskTab(which){
  const layouts=which==='layouts';
  $('tab-layouts').classList.toggle('active',layouts);
  $('tab-themes').classList.toggle('active',which==='themes');
  $('tab-multi').classList.toggle('active',which==='multi');
  $('tab-layouts').setAttribute('aria-selected',layouts?'true':'false');
  $('tab-themes').setAttribute('aria-selected',which==='themes'?'true':'false');
  $('tab-multi').setAttribute('aria-selected',which==='multi'?'true':'false');
  $('pane-layouts').hidden=!layouts;
  $('pane-themes').hidden=which!=='themes';
  $('pane-multi').hidden=which!=='multi';
  if(!layouts&&which==='themes')renderThemeGrid();
  if(which==='multi')renderMultiPane();
}
/* 优化#11：多窗格预览——同一份数据、四种布局并排（虚拟多副屏）。
   屏内样式全部走容器查询单位，小尺寸自动缩放，无需为迷你窗单独适配。 */
function renderMultiPane(){
  const root=$('multi-grid');if(!root)return;
  const schemes=[['focus','状态聚焦',renderFocus],['timeline','任务时间线',renderTimeline],
                 ['companion','角色陪伴',renderCompanion],['dashboard','数据看板',renderDashboard]];
  root.replaceChildren();
  schemes.forEach(([key,name,renderer])=>{
    const cell=el('div','multi-cell');
    const pane=el('div','screen mini');
    const body=el('div','screen-body');
    pane.append(body);
    applyDeviceTheme(draft.theme,pane);
    try{renderer(body)}catch(e){body.append(el('div','mini-task','渲染失败'))}
    cell.append(pane,el('span','multi-cap',name));
    cell.onclick=()=>{tv.scheme=key;tv.layout=key;$('scheme-select').value=key;saveTV();
      switchTaskTab('layouts');renderCfgPanels();renderTaskPreview()};
    root.append(cell);
  });
}
async function downloadFile(path, filename){
  const r=await fetch(path);
  if(!r.ok)throw new Error('下载失败');
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=filename;a.click();URL.revokeObjectURL(a.href);
}
bind();
poll();
let sse=null, sseLive=false;
startSSE();
startTicker();
/* 优化#3：SSE 实时推送。/api/stream 到达即渲染（亚秒级）；
   端点不可用（旧版客户端）时自动留在轮询模式，轮询始终作为兜底。 */
function startSSE(){
  if(typeof EventSource==='undefined')return;
  try{ sse=new EventSource('/api/stream'); }catch(e){ return }
  sse.onmessage=e=>{
    try{
      const data=JSON.parse(e.data);
      sseLive=true;lastOkAt=Date.now();failStreak=0;
      absorb(data);
    }catch(err){}
  };
  sse.onerror=()=>{
    // 首连即失败（404 等）→ 彻底退回轮询；连上后的偶发断线交给 EventSource 自动重连
    if(!sseLive&&sse){ try{sse.close()}catch(e){} sse=null; }
  };
}
function startTicker(){
  scheduleNext();
  function scheduleNext(){
    clearTimeout(tickTimer);
    const delay=failStreak>0 ? Math.min(15000, 2500*Math.pow(1.6,failStreak-1))
      : sseLive ? 10000 : 2500;
    tickTimer=setTimeout(async()=>{
      // 后台标签页会被浏览器降频，回到前台必须立刻补一次。
      if(!document.hidden)await poll();
      scheduleNext();
    },delay);
  }
}
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){clearTimeout(tickTimer);return}
  failStreak=0;clearTimeout(tickTimer);
  poll().finally(startTicker);
});
window.addEventListener('focus',()=>{if(!document.hidden){failStreak=0;poll()}});
// 预览时钟每秒走字
setInterval(()=>{
  if($('screen-clock'))$('screen-clock').textContent=clock();
},1000);
