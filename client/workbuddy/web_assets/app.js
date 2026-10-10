const $ = id => document.getElementById(id);
const widgets = {
  ai_list: ['AI 会话', '✦', 4], hw_cards: ['硬件四卡', '▦', 4],
  clock: ['时钟', '◷', 1], environment: ['温湿度', '◉', 1],
  reminder_list: ['提醒列表', '☷', 2], today_calendar: ['今日日历', '▣', 2],
  alarm: ['闹钟', '◴', 1], weather: ['天气', '☁', 1], countdown: ['倒计时', '⏳', 1],
  ai_cost: ['AI 用量', '∑', 1]
};
const stateNames = {THINKING:'思考中',GENERATING:'生成中',TOOL:'执行工具',BUILD:'构建中',DONE:'已完成',ERROR:'出错',WAIT_INPUT:'等你回复',ASK:'进行中待确认'};
// 主题预设：每套自带 card_opa，card 与 bg 必须拉开亮度差——注意校验用的是
// **合成后的填充色**（固件 box() 用 bg_opa=card_opa 把 card 叠在渐变上），
// 所以不透明度越低、card 越要亮。下面这些值是照 1.6:1 解出来的，
// 改任何一项都要让 profile.validate_profile 通过（tests/test_theme_presets.py 会拦）。
const themePresets = {
  '实机校准·深蓝灰':{card_opa:100,bg:'060B16',grad:'0B1730',card:'304661',card_edge:'4B6685',accent:'57C8F5',text:'F4F8FF',sub:'C7D5E5'},
  '极光石板':{card_opa:100,bg:'060A12',grad:'0E1726',card:'28384E',card_edge:'3C5070',accent:'4CC9F0',text:'F1F5F9',sub:'A6B6C9'},
  '墨蓝夜':{card_opa:100,bg:'05080F',grad:'0A1220',card:'223650',card_edge:'36588C',accent:'6AA0FF',text:'EEF3FB',sub:'9FB4D2'},
  '深蓝科技':{card_opa:70,bg:'0B1220',grad:'0E1B33',card:'2E4C76',card_edge:'3E506E',accent:'22D3EE',text:'F3F7FF',sub:'A8BDD9'},
  '紫夜':{card_opa:72,bg:'120B22',grad:'1D1136',card:'4D3C7A',card_edge:'54427F',accent:'A78BFA',text:'F5F3FF',sub:'C4B5E8'},
  '苍翠':{card_opa:70,bg:'071A12',grad:'0C2A1B',card:'21543A',card_edge:'2A6B4A',accent:'34D399',text:'ECFDF5',sub:'A3D5BC'},
  '暖阳':{card_opa:66,bg:'1A1207',grad:'2A1C0C',card:'684922',card_edge:'6B4C24',accent:'F59E0B',text:'FFF8EB',sub:'DEC9A3'},
  '玫瑰':{card_opa:68,bg:'1A0B12',grad:'2A1120',card:'6E3E5E',card_edge:'8F4566',accent:'FB7185',text:'FFF1F3',sub:'E8B4C0'},
  '晨雾浅蓝':{card_opa:92,bg:'B7C3D4',grad:'ACB9CD',card:'FFFFFF',card_edge:'D8E1EC',accent:'0E7490',text:'1B2536',sub:'44566C'},
  '米纸暖白':{card_opa:94,bg:'C6BDA9',grad:'BDB3A0',card:'FFFDF8',card_edge:'E4DCCC',accent:'9A6B2F',text:'2A241A',sub:'5C5342'},
  '薄荷奶白':{card_opa:94,bg:'B9CCC2',grad:'AEC3B8',card:'FCFFFD',card_edge:'D9E7E0',accent:'0F766E',text:'15291F',sub:'3F5A4C'},
  '樱花粉白':{card_opa:94,bg:'D2C0C8',grad:'C9B4BE',card:'FFFAFB',card_edge:'EBD9DF',accent:'B0486E',text:'2E1E26',sub:'63505B'},
  '碳灰霓虹':{card_opa:88,bg:'0D0F14',grad:'12151D',card:'3A4358',card_edge:'46506A',accent:'A3E635',text:'E5E9F0',sub:'97A3B8'},
  '海军蓝金':{card_opa:82,bg:'0A1424',grad:'0E1B31',card:'35476B',card_edge:'45597D',accent:'E8C268',text:'F2F6FF',sub:'9FB0CC'},
  '咖啡奶昔':{card_opa:88,bg:'171009',grad:'1E150C',card:'52402A',card_edge:'6B563A',accent:'E8A761',text:'FAEEDF',sub:'B39B7E'},
  '紫罗兰夜':{card_opa:82,bg:'120E1E',grad:'181232',card:'483D71',card_edge:'5D4F8A',accent:'C084FC',text:'F3EFFF',sub:'A79BC8'}
};
// 画廊主题的 card 填充必须对 bg 保持 ≥1.5:1（profile.validate_profile 的
// 硬门槛，按 RGB565 上屏值核算）。这些值就是照该门槛解出来的：低于它们
// 「精选主题」会保存失败——曾经 7 套全都不达标，等于整个主题画廊是坏的。
// 主题库（任务视图 → 主题库）的卡片清单在 galleryThemes 定义之后初始化
// （见 themeLibrary），避免 const 初始化前读取的 TDZ 崩溃。
let draft = null, saved = null, live = {tasks:[],telemetry:{},device:'等待设备'};
let selected = 0, dirty = false, dragPage = -1, dragWidget = -1, toastTimer = null;
let polling = false, lastOkAt = 0, failStreak = 0, tickTimer = null;
let currentView = 'overview';
let selectedThemeKey = null, themeTab = 'preset';
let colorMap = null;

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
/* 面板标定矩阵（24 格实测）：换屏 / 换控制器时用它核对通道序、换字节、反相。
   数据来自 color-map.json 的 diagnosticVariants（第 7 格是实机确认的方案）。
   这些格子代表的是「驱动层参数」，不是可选配色——改它要同步改固件的
   PANEL_COLOR_ORDER_BGR / PANEL_COLOR_BYTESWAP / cfg.invert。 */
function renderVariants(){
  const root=$('variant-grid'); if(!root || !colorMap) return;
  const list=colorMap.diagnosticVariants||[];
  root.replaceChildren();
  list.forEach(v=>{
    const cell=el('div','variant-cell'+(v.selected?' on':''));
    cell.append(el('strong','',`格 ${v.cell}${v.selected?' ✓':''}`));
    cell.append(el('small','',`${v.channelOrder}${v.byteSwap?' · 换字节':''}`));
    cell.append(el('small','',v.softwareInvert?'软件反相':'无软件反相'));
    root.append(cell);
  });
  const note=$('variant-note');
  if(note){
    const cal=(colorMap.panel||{}).calibration||{};
    const sel=list.find(v=>v.selected)||{};
    note.textContent = cal.cell
      ? `当前标定：第 ${cal.cell} 格（${cal.rgbOrder||sel.channelOrder||'RGB'}`
        + `${cal.lvglFlushByteSwap?' + flush 换字节':''}`
        + `${cal.panelInvert?' + 硬件反相':''}）`
      : '未标注选定的格子';
  }
}

/* ---------- 屏幕色彩管线：请求色 → RGB565 量化 → flush 字 ----------
   小屏是 RGB565（ST7796，竖屏 320×480）。Web 里拿到一个 #RRGGBB **不能直接渲染**：
   屏幕每个像素只有 5/6/5 位，颜色先被量化，观感可能和请求色不同（深色主题里
   蓝紫偏移尤其明显）。所以预览一律先过这里，画出来的是屏幕真正显示的颜色。

   位宽、通道序、flush 换字节全部读 color-map.json 的 panel 段——换屏或换控制器
   时改 JSON（再跑 tools/gen_color_map.py），不改本文件。 */
const PANEL_FALLBACK = {
  pixelFormat:'RGB565',
  calibration:{ rgbOrder:'RGB', lvglFlushByteSwap:true, panelInvert:true }
};
function panelSpec(){ return (colorMap && colorMap.panel) || PANEL_FALLBACK }
function panelBits(){
  const m = /^RGB(\d)(\d)(\d)$/.exec(String(panelSpec().pixelFormat || 'RGB565'));
  return m ? {r:+m[1], g:+m[2], b:+m[3]} : {r:5, g:6, b:5};
}
function quantizeWord(hex){
  const {r:br, g:bg, b:bb} = panelBits();
  const [r,g,b] = hexToRgb(hex);
  return ((r >> (8-br)) << (bg+bb)) | ((g >> (8-bg)) << bb) | (b >> (8-bb));
}
/* 565 → 888：位复制展开，与 LVGL 内部把量化值还原成像素的做法一致。
   5 位 → (v<<3)|(v>>2)，6 位 → (v<<2)|(v>>4)，通式 (v<<s)|(v>>(2n-8))。 */
function expandBits(value, n){
  return n >= 8 ? value : ((value << (8-n)) | (value >> (2*n-8))) & 0xFF;
}
function wordToRgb(word){
  const {r:br, g:bg, b:bb} = panelBits();
  return [
    expandBits((word >> (bg+bb)) & ((1<<br)-1), br),
    expandBits((word >> bb) & ((1<<bg)-1), bg),
    expandBits(word & ((1<<bb)-1), bb),
  ];
}
function flushWordOf(word){ return ((word & 0xFF) << 8) | ((word >> 8) & 0xFF) }
/** 一条主题色的完整链条：请求色 → 屏幕色。drift 是量化造成的最大通道偏移。
 *
 * ⚠️ WYSIWYG 不变量（换屏/改标定时由 test_color_map.py 锁死）：
 *    返回的 hex 必须是「量化后的设计色」本身——它就是真机最终显示色。
 *    原因：固件在 flush_cb 对 rgb565 做字节交换（lvglFlushByteSwap），
 *    面板字节序相反、读回原式；panelInvert=true 是面板正常黑底模式（非额外取反）。
 *    净效果：面板显示的就是设计色。所以这里【绝不】再补字节交换、也【绝不】对
 *    设计色取反——否则预览会变成负片，与真机相反（回归）。
 *    唯一需要处理的驱动层差异是通道序：BGR 面板上 R 与 B 落反，预览须跟着反。 */
function panelColor(hex){
  const req = String(hex || '').replace('#','').toUpperCase();
  const raw = quantizeWord(req);
  let rgb = wordToRgb(raw);
  const cal = panelSpec().calibration || {};
  const order = String(cal.rgbOrder || 'RGB').toUpperCase();
  // 通道序是驱动层标定的结果：BGR 面板上 R 与 B 落反，预览必须跟着反，
  // 否则「预览好看、实机偏色」又会重现。
  if (order === 'BGR') rgb = [rgb[2], rgb[1], rgb[0]];
  const wanted = hexToRgb(req);
  return {
    req,
    hex: rgb.map(v=>v.toString(16).padStart(2,'0')).join('').toUpperCase(),
    word: raw,
    flush: flushWordOf(raw),
    drift: Math.max(...wanted.map((v,i)=>Math.abs(v-rgb[i]))),
    order,
    swap: !!cal.lvglFlushByteSwap,
    invert: !!cal.panelInvert,
  };
}
/** 屏幕真正显示的颜色，'#RRGGBB'。预览的所有颜色都必须走它。 */
function panelHex(hex){ return '#'+panelColor(hex).hex }
function word4(value){ return value.toString(16).toUpperCase().padStart(4,'0') }
/** 一行文字说明某色的映射结果，用于色卡与取色器提示。 */
function panelNote(hex){
  const c = panelColor(hex);
  return `屏幕 #${c.hex} · 565 ${word4(c.word)} · flush ${word4(c.flush)}`;
}
/* 固件里写死、不随主题变的语义色（config.h / ui.cpp 字面值）。
   值仍从映射文件读——这样「屏幕显示什么颜色」只有一份来源。 */
const DEV_SEMANTIC_FALLBACK = {
  'status.ok':'34D399', 'status.warn':'FBBF24', 'status.err':'F87171',
  'robot.body':'46689C', 'robot.error':'2A1A1A', 'robot.done':'1A2A1A',
  'saver.black':'000000', 'saver.frame':'0A0F14', 'saver.edge':'2E4370',
  'saver.quote':'8CA3C7', 'saver.time':'22D3EE', 'saver.dim':'6B7A8F',
  'saver.label':'3A4A5F',
};
function devSemantic(id){
  const hit = colorMap && colorMap.semantic
    ? colorMap.semantic.find(c => c.id === id) : null;
  return panelHex(hit ? hit.hex : (DEV_SEMANTIC_FALLBACK[id] || '000000'));
}
function applyDeviceTheme(theme, root){
  const screen = root || $('screen');
  if (!screen) return;
  // 写进 CSS 变量的必须是量化后的颜色，否则卡片色/描边比实机亮一档。
  const q = k => panelHex(theme[k]);
  const opa = Math.max(0.3, Math.min(1, (theme.card_opa ?? 100) / 100));
  const backdrop = composite(hexToRgb(q('bg')), hexToRgb(q('grad')), 0.5);
  const cardBg = composite(hexToRgb(q('card')), backdrop, opa);
  screen.style.setProperty('--dev-bg', q('bg'));
  screen.style.setProperty('--dev-grad', q('grad'));
  screen.style.setProperty('--dev-card', q('card'));
  screen.style.setProperty('--dev-edge', panelHex(theme.card_edge || theme.card));
  screen.style.setProperty('--dev-accent', q('accent'));
  screen.style.setProperty('--dev-text', q('text'));
  screen.style.setProperty('--dev-sub', q('sub'));
  screen.style.setProperty('--dev-card-opa', String(opa));
  screen.style.setProperty('--dev-card-ink', ensureContrast([q('text'), '#ffffff'], cardBg, 4.5));
  screen.style.setProperty('--dev-sub-ink', ensureContrast([q('sub'), q('text')], cardBg, 3));
}
async function loadColorMap(){
  try{
    const response=await fetch('/color-map.json');
    if(!response.ok)throw new Error('色彩映射文件不可用');
    colorMap=await response.json();
    const target=$('color-map-target'), group=$('color-map-group');
    target.replaceChildren();
    Object.entries(colorMap.themeBindings).forEach(([key,meta])=>{
      const option=el('option','',meta.label);option.value=key;target.append(option);
    });
    target.value='accent';
    const groups=[...new Set([...colorMap.semantic,...colorMap.checks,...colorMap.palette].map(c=>c.group))];
    groups.forEach(name=>{const option=el('option','',name);option.value=name;group.append(option)});
    group.value='实机确认主题样本';
    group.onchange=renderColorMap;
    $('color-map-search').oninput=renderColorMap;
    renderColorMap();
    renderVariants();
    // 映射文件到位后重画一次：预览与色卡都依赖 panel 标定参数，
    // 首次渲染时 colorMap 可能还没加载完（本地 fetch 也不能假设同步）。
    if(currentView==='tasks')renderTaskPreview();
    if(currentView==='saver')renderSaverPreview();
    if(currentView==='settings')renderInspector();
  }catch(err){$('color-map-grid').textContent=err.message}
}
function renderColorMap(){
  if(!colorMap)return;
  const root=$('color-map-grid'), group=$('color-map-group').value;
  const query=$('color-map-search').value.trim().toLowerCase();
  root.replaceChildren();
  const colors=[...colorMap.semantic,...colorMap.checks,...colorMap.palette];
  colors.filter(c=>(!group||c.group===group)&&
    (!query||[c.name,c.hex,c.rgb565,c.id].some(v=>String(v).toLowerCase().includes(query))))
    .forEach(c=>{
      // 色块显示的是**屏幕会显示的颜色**（hex 经 RGB565 量化后还原），
      // 请求色只在有偏移时以小角标出现——否则用户会以为选了什么就得到什么。
      const shown=panelColor(c.hex);
      const item=el('button','color-map-item');item.type='button';
      const chip=el('b');chip.style.background='#'+shown.hex;
      chip.style.position='relative';
      if(shown.drift>0){
        const req=el('i');
        req.style.cssText='position:absolute;right:2px;bottom:2px;width:9px;height:9px;'
          +'border-radius:2px;border:1px solid rgba(0,0,0,.35);background:#'+c.hex;
        req.title=`请求色 #${c.hex}`;
        chip.append(req);
      }
      item.append(chip,el('span','',c.name),
                  el('code','',`#${c.hex}→#${shown.hex} · ${word4(shown.word)}`));
      item.title=`${c.name}\n请求色 #${c.hex}　屏幕色 #${shown.hex}`
        +`\nRGB565 ${word4(shown.word)}　flush ${word4(shown.flush)}`
        +`\n量化最大通道偏移 Δ${shown.drift}`;
      item.onclick=()=>{
        const key=$('color-map-target').value;
        if(!draft||!colorMap.themeBindings[key])return;
        draft.theme[key]=c.hex;
        $('theme-'+key).value='#'+c.hex;
        markDirty();updateQuantNote();
        notice(shown.drift>=8
          ? `${c.name} 已选为${colorMap.themeBindings[key].label}；该色过 RGB565 后偏移 Δ${shown.drift}（屏幕显示 #${shown.hex}）`
          : `${c.name} 已选为${colorMap.themeBindings[key].label}，屏幕显示 #${shown.hex}`);
      };
      root.append(item);
    });
  if(!root.childElementCount)root.append(el('div','empty','没有匹配的颜色'));
}

// ---------- 主题画廊：程序绘制背景 ----------
function srng(seed){let s=seed;return()=>{s=(s*9301+49297)%233280;return s/233280}};
const galleryThemes = {
  aurora:{name:'极光',card_opa:70,colors:{bg:'060A14',grad:'0B1428',card:'284880',card_edge:'1E3A5F',accent:'5EEAD4',text:'E9FFFA',sub:'8FB6D9'}},
  synthwave:{name:'赛博霓虹',card_opa:70,colors:{bg:'0D0221',grad:'1A0533',card:'5D16A8',card_edge:'7B2CBF',accent:'F72585',text:'FFF8FE',sub:'B39DDB'}},
  dusk:{name:'山峦黄昏',card_opa:65,colors:{bg:'221028',grad:'40203A',card:'704689',card_edge:'7A4A63',accent:'FF9E64',text:'FFF3E9',sub:'D4A88F'}},
  starry:{name:'星空',card_opa:70,colors:{bg:'050914',grad:'0A1230',card:'244088',card_edge:'27408B',accent:'8AB8FF',text:'EEF4FF',sub:'8FA3C9'}},
  garden:{name:'像素花园',card_opa:70,colors:{bg:'0C1810',grad:'122418',card:'295035',card_edge:'2E5C40',accent:'A3E635',text:'F0FDF0',sub:'94C9A3'}},
  waves:{name:'深海波浪',card_opa:70,colors:{bg:'03121E',grad:'062033',card:'124C76',card_edge:'1D5C7A',accent:'22D3EE',text:'E8FBFF',sub:'89B8C9'}},
  ink:{name:'墨韵',card_opa:93,colors:{bg:'D2CFCA',grad:'C9C6BD',card:'FFFFFF',card_edge:'AEA89C',accent:'B03A2E',text:'2B2B2B',sub:'6B6B60'}},
  ember:{name:'熔岩余烬',card_opa:70,colors:{bg:'0F0505',grad:'1F0808',card:'2A0E0E',card_edge:'6B2020',accent:'FF6B35',text:'FFF0E8',sub:'C9947E'}}
};
function galleryColors(key){
  return galleryThemes[key] ? galleryThemes[key].colors : themePresets['深蓝科技'];
}
// 主题库（任务视图 → 主题库）：预设 + 精选画廊的合一清单。
// 必须在 galleryThemes 之后初始化（galleryColors 会读它）。
const themeLibrary = [
  {key:'preset-实机校准', name:'实机校准·深蓝灰', colors:themePresets['实机校准·深蓝灰'], opa:100, bright:62},
  {key:'preset-深蓝科技', name:'深蓝科技', colors:themePresets['深蓝科技'], opa:70},
  {key:'preset-紫夜', name:'紫夜', colors:themePresets['紫夜'], opa:72},
  {key:'preset-苍翠', name:'苍翠', colors:themePresets['苍翠'], opa:70},
  {key:'preset-暖阳', name:'暖阳', colors:themePresets['暖阳'], opa:66},
  {key:'preset-玫瑰', name:'玫瑰', colors:themePresets['玫瑰'], opa:68},
  {key:'preset-晨雾浅蓝', name:'晨雾浅蓝', colors:themePresets['晨雾浅蓝'], opa:92, bright:88},
  {key:'preset-米纸暖白', name:'米纸暖白', colors:themePresets['米纸暖白'], opa:94, bright:88},
  {key:'preset-薄荷奶白', name:'薄荷奶白', colors:themePresets['薄荷奶白'], opa:94, bright:88},
  {key:'preset-樱花粉白', name:'樱花粉白', colors:themePresets['樱花粉白'], opa:94, bright:88},
  {key:'preset-碳灰霓虹', name:'碳灰霓虹', colors:themePresets['碳灰霓虹'], opa:88},
  {key:'preset-海军蓝金', name:'海军蓝金', colors:themePresets['海军蓝金'], opa:82},
  {key:'preset-咖啡奶昔', name:'咖啡奶昔', colors:themePresets['咖啡奶昔'], opa:88},
  {key:'preset-紫罗兰夜', name:'紫罗兰夜', colors:themePresets['紫罗兰夜'], opa:82},
  {key:'gallery-aurora', name:'极光', colors:galleryColors('aurora'), opa:70, gallery:'aurora'},
  {key:'gallery-starry', name:'星空', colors:galleryColors('starry'), opa:70, gallery:'starry'},
  {key:'gallery-garden', name:'像素花园', colors:galleryColors('garden'), opa:70, gallery:'garden'},
  {key:'gallery-waves', name:'深海波浪', colors:galleryColors('waves'), opa:70, gallery:'waves'},
  {key:'gallery-ink', name:'墨韵', colors:galleryColors('ink'), opa:93, gallery:'ink'},
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
    {label:'撤销上一步配置（Cmd/Ctrl+Z）',run:()=>undoChange()},
    {label:'任务视图：多窗格预览',run:()=>{switchView('tasks');switchTaskTab('multi')}},
    {label:'查看键盘快捷键',key:'?',run:()=>toggleKbd(true)}
  );
  // 优化#17：索引当前配置——页面/提醒/应用都能搜到并直达（或直接执行）
  if(draft){
    (draft.pages||[]).forEach((page,i)=>{
      cmds.push({label:`编辑页面：${i+1} ${page.title}`,run:()=>{
        openSettings('pages');selected=i;renderSettings()}});
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
  const kindNames = {save:'保存', sync:'同步', theme:'主题', bg:'背景图', ota:'固件', diag:'诊断', import:'导入', export:'导出', layout:'布局', saver:'息屏', hook:'Hook', launch:'启动'};
  list.forEach(item=>{
    const row = el('div',`history-item${item.ok?'':' failed'}`);
    const time = new Date(item.t).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});
    row.append(el('span','history-kind',kindNames[item.kind]||item.kind),
               el('span','history-text',item.text),
               el('time','history-time',time));
    root.append(row);
  });
}

/* ================= 系统趋势（P1-4 行为遥测） ================= */
const TREND_SERIES = [
  {key:'cpu',  label:'处理器', colorProp:'--accent'},
  {key:'mem',  label:'内存',   colorProp:'--info'},
  {key:'disk', label:'磁盘',   colorProp:'--warn'},
];
function drawTrendChart(){
  const canvas=$('trend-chart');if(!canvas||!canvas.getContext)return;
  const w=canvas.clientWidth||canvas.width,h=180;
  const dpr=window.devicePixelRatio||1;
  if(canvas.width!==Math.round(w*dpr)){canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr)}
  const ctx=canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  const cs=getComputedStyle(document.documentElement);
  const cLine=cs.getPropertyValue('--line').trim()||'#e2eae8';
  const cSub=cs.getPropertyValue('--text-tertiary').trim()||'#5e6e76';
  // canvas 不认 CSS var()，颜色必须先解析成实际值
  const seriesColors=Object.fromEntries(TREND_SERIES.map(s=>
    [s.key, cs.getPropertyValue(s.colorProp).trim()||'#888']));
  fetch(`/api/trend?hours=${$('trend-range').value}`).then(r=>r.json()).then(d=>{
    const series=d.series||[];
    // 图例（含最新值）
    const legend=$('trend-legend');legend.replaceChildren();
    TREND_SERIES.forEach(s=>{
      const last=series.length?series[series.length-1][s.key]:null;
      const item=el('span','trend-key');
      const dot=el('i','trend-dot');dot.style.background=seriesColors[s.key];
      item.append(dot,document.createTextNode(`${s.label} ${last==null?'—':last+'%'}`));
      legend.append(item);
    });
    if(series.length<2){
      ctx.fillStyle=cSub;ctx.font='12px sans-serif';ctx.textAlign='center';
      ctx.fillText('趋势数据积累中（客户端每分钟一拍，约 10 分钟后出线）',w/2,h/2);
      return;
    }
    const padL=34,padR=10,padT=10,padH=h-padT-18;
    ctx.strokeStyle=cLine;ctx.fillStyle=cSub;ctx.font='10px sans-serif';
    for(let i=0;i<=4;i++){
      const y=padT+padH*i/4;
      ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();
      ctx.textAlign='right';ctx.fillText(`${100-i*25}%`,padL-4,y+3);
    }
    const t0=series[0].t,t1=series[series.length-1].t,span=Math.max(1,t1-t0);
    const X=t=>padL+(w-padL-padR)*(t-t0)/span;
    const Y=v=>padT+padH*(1-Math.max(0,Math.min(100,v))/100);
    TREND_SERIES.forEach(s=>{
      ctx.beginPath();
      let started=false;
      series.forEach(p=>{
        const v=p[s.key];
        if(typeof v!=='number')return;
        const x=X(p.t),y=Y(v);
        started?ctx.lineTo(x,y):(ctx.moveTo(x,y),started=true);
      });
      if(started){
        ctx.strokeStyle=seriesColors[s.key];
        ctx.lineWidth=1.6;ctx.lineJoin='round';ctx.stroke();
      }
    });
    ctx.fillStyle=cSub;ctx.textAlign='left';
    ctx.fillText(new Date(t0*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}),padL,h-4);
    ctx.textAlign='right';
    ctx.fillText(new Date(t1*1000).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}),w-padR,h-4);
  }).catch(()=>{});
}

/* 与 profile.py DEFAULT_PROFILE.theme 保持一致，避免两处默认主题漂移。 */
const DEFAULT_THEME = {bg:'060A12', grad:'0E1726', card:'28384E', card_edge:'3C5070',
  accent:'4CC9F0', text:'F1F5F9', sub:'A6B6C9', bright:90};

/* 与 workbuddy/music.py 的 MUSIC_COMMAND 一致：快捷应用里用它表示
   「用默认音乐软件播放」。 */
const MUSIC_COMMAND = '@music';

/* 自动按工具分页：全部 + 每工具一页（页面带 filter，固件据此过滤会话）。
   只重排 AI 页，其它页面（电脑/环境…）原样保留；总页数硬上限 6，
   放不下时先砍靠后的工具页（那些工具的会话仍在「全部」页可见）。 */
function autoPages(){
  const TOOL_TITLES = {claude:'Claude', codex:'Codex', zcode:'ZCode',
                       opencode:'OpenCode', workbuddy:'智能体'};
  const tools=[...new Set((live.tasks||[]).map(t=>t.tool).filter(Boolean))].sort();
  const wCities=(draft.weather&&draft.weather.cities)||[];
  // 非 AI 页 = 天气城市页 + 其余页面；城市页由本函数生成，其余原样保留
  const others=(draft.pages||[]).filter(p=>!((p.widgets||[]).includes('ai_list'))
                                          && !p.city);
  const pages=[{id:'ai', title:'全部', layout:'list', widgets:['ai_list'], filter:''}];
  tools.forEach(t=>{
    if(pages.length>=6) return;
    pages.push({id:('t'+t).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,16),
                title:TOOL_TITLES[t]||t, layout:'list', widgets:['ai_list'], filter:t});
  });
  wCities.forEach(c=>{
    if(pages.length>=6) return;
    pages.push({id:('w'+c.name).replace(/[^a-zA-Z0-9_-]/g,'').slice(0,16),
                title:c.name, layout:'list', widgets:['weather'], city:c.name});
  });
  return [...pages, ...others].slice(0,6);
}
function regenAutoPages(){
  const pages=autoPages();
  const changed=JSON.stringify(pages)!==JSON.stringify(draft.pages);
  draft.pages=pages;
  if(changed){ selected=0; previewPage=0; markDirty(); }
  return changed;
}
function regenAllPages(){ regenAutoPages(); }
/* 手动排序意图：一旦用户拖动页面，自动分页立即关闭（否则保存时会被
   autoPages 按固定顺序打回，手动排序形同虚设）。 */
function manualOrder(){
  if(draft.autopage){
    draft.autopage=0;
    const t=$('autopage-toggle'); if(t) t.checked=false;
    notice('已切换为手动排序（自动按工具分页已关闭），保存后生效');
  }
}
/* 任务视图「当前页面组合」的拖拽排序 */
let dragTaskPage = -1;

/* ================= 任务视图：布局预设 =================
   这里的「四种布局」= 四套真实的页面组合（LAYOUT_PRESETS），点选即写入
   draft.pages，和设备上看到的一页一页完全对应。

   原来这里存着一整套 tv.* 参数（任务补充 / 任务数值 / 其他任务 / 空闲时显示 /
   场景 / 氛围开关 / 显示内容 / 内容组合 / 显示指标…）。它们只喂浏览器里的
   效果图，从不进 profile、也从不经 CFG 下发——用户在「任务视图」调半天，
   小屏一点变化都没有。整块已移除：能改小屏的控件才留在界面上。 */
const TV_KEY = 'workbuddy-taskview';
const tvDefaults = { layout:'focus' };
function loadTV(){
  try{
    const raw = JSON.parse(localStorage.getItem(TV_KEY) || 'null');
    return {...tvDefaults, ...(raw && typeof raw === 'object' ? {layout:raw.layout} : {})};
  }catch(e){ return {...tvDefaults} }
}
let tv = loadTV();
function saveTV(){ localStorage.setItem(TV_KEY, JSON.stringify({layout:tv.layout})); }
/* 默认页面组合＝与 client/workbuddy/profile.py 的 DEFAULT_PROFILE 一致。
   「恢复默认」必须恢复成设备认得的页面，而不是另一份前端私有清单。 */
function defaultPages(){
  return [
    {id:'ai', title:'帽子工作台', layout:'list', widgets:['ai_list']},
    {id:'computer', title:'电脑', layout:'grid_2x2', widgets:['hw_cards']},
    {id:'environment', title:'环境', layout:'grid_2x2', widgets:['environment','clock']},
  ];
}

/* ---------- 小屏预览：与固件同源的坐标复刻 ---------- */
function clock(){return new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit',hour12:false})}
/* 顶栏时钟要显示到秒（用户要求「秒的变化能体现出来」）。
   固件每秒重刷同一串；预览由下方 1s ticker 只改这一个文本节点。
   息屏页仍用 HH:MM——固件 build_saver() 的时间串就是短格式。 */
function clockHMS(){
  const d=new Date(), pad=n=>String(n).padStart(2,'0');
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}
function fmtElapsed(sec){
  sec = Math.max(0, Math.round(sec||0));
  const m = Math.floor(sec/60), s = sec%60;
  return `${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

/* ================= 设备屏忠实预览 =================
   预览画的就是屏幕会画的东西：
     · 几何取自 firmware/src/ui.cpp（320×480、BODY_Y=60、AI 卡 304×380、
       硬件四卡 148×186、页码 32×22 等），不是另画一版效果图；
     · 字号走 label() 的字库映射（20 请求实际按 24px 渲染）；
     · 颜色一律过 panelColor()，即先量化成 RGB565 再上屏。

   历史问题：这里原是一套 flex「效果图」，与实机不共享几何。最典型的是
   「角色陪伴」画了一只猫和四个场景，而固件根本没有场景渲染——固件里的角色
   是 AI 页右侧那只随状态改表情的像素机器人。现在全部换成同源复刻。 */
const DEV = {
  W:320, H:480, HEAD_Y:8, BODY_Y:60, BODY_H:380, FOOT_Y:446,
  DOT_W:32, DOT_H:22, DOT_GAP:10, GRID_GAP:8,
  AI:{x:8, y:60, w:304, h:380},
  HW_CARD:{x0:8, y0:60, dx:156, dy:148, w:148, h:140},
};
const DEV_WIDGET_TITLES = {
  clock:'本地时间', environment:'桌面环境', reminder_list:'提醒列表',
  today_calendar:'今日日历', alarm:'闹钟', weather:'天气',
  countdown:'倒计时', ai_cost:'AI 用量',
};
/* label() 的字库映射：>=40→48px、>=28→32px、>=20→24px、其余→20px。
   所以固件里写 "20" 的文字实际以 24px 渲染，预览必须跟着放大。 */
function devFont(size){ return size>=40 ? 48 : size>=28 ? 32 : size>=20 ? 24 : 20 }
function devLineH(size){ return Math.round(devFont(size) * 1.3) }

function devAbs(node,x,y,w,h){
  node.style.left=x+'px'; node.style.top=y+'px';
  node.style.width=w+'px'; node.style.height=h+'px';
  return node;
}
function devLabel(parent,x,y,w,text,size,cls,color,align){
  const n = devAbs(el('div','dev-label'+(cls?' '+cls:'')),x,y,w,devLineH(size));
  n.style.fontSize = devFont(size)+'px';
  if(color) n.style.color = color;
  if(align) n.style.textAlign = align;
  n.textContent = text===null||text===undefined ? '' : String(text);
  parent.append(n); return n;
}
function devCard(parent,x,y,w,h){
  const n = devAbs(el('div','dev-el dev-card'),x,y,w,h);
  parent.append(n); return n;
}
/* 迷你进度条：轨道用 bg 半透明，填充条宽度＝百分比。
   prog<0（未知）时固件不画填充——预览也不画，不拿假进度凑数。 */
function devTrack(parent,x,y,w,pct,color){
  const t = devAbs(el('div','dev-el dev-track'),x,y,w,4);
  if(pct > 0){
    const f = el('div','dev-el');
    f.style.cssText = 'left:0;top:0;height:100%;border-radius:2px;width:'
      + Math.min(100,pct) + '%;background:' + color;
    t.append(f);
  }
  parent.append(t); return t;
}
function devDot(parent,x,y,d,color){
  const n = devAbs(el('div','dev-el'),x,y,d,d);
  n.style.borderRadius = (d/2)+'px';
  n.style.background = color;
  parent.append(n); return n;
}

/* ---------- 任务状态：镜像固件的 state_short / state_color / state_running ---------- */
const DEV_RUNNING = ['THINKING','GENERATING','TOOL','BUILD'];
function devRunning(state){ return DEV_RUNNING.includes(String(state||'')) }
function devStateShort(state){
  const map = {ASK:'待确认', ERROR:'出错', WAIT_INPUT:'等回复', DONE:'已完成',
               TOOL:'执行中', THINKING:'思考中'};
  return map[String(state||'')] || '处理中';
}
/* 状态色是固件写死的语义色（COL_OK/WARN/ERR），不随 profile 主题走。 */
function devStateColor(state, accent, sub){
  const s = String(state||'');
  if(s === 'GENERATING' || s === 'TOOL') return accent;
  if(s === 'WAIT_INPUT') return devSemantic('status.ok');
  if(s === 'ASK' || s === 'THINKING') return devSemantic('status.warn');
  if(s === 'ERROR') return devSemantic('status.err');
  return sub;
}

/* ---------- 遥测取值：与固件的 render_generic 一一对应 ---------- */
function weatherText(code){
  const c = Number(code);
  if(c === 0) return '晴';
  if(c <= 2) return '多云';
  if(c === 3) return '阴';
  if(c === 45 || c === 48) return '雾';
  if(c >= 51 && c <= 57) return '毛毛雨';
  if(c >= 61 && c <= 67) return '雨';
  if(c >= 71 && c <= 77) return '雪';
  if(c >= 80 && c <= 82) return '阵雨';
  if(c >= 85 && c <= 86) return '阵雪';
  if(c >= 95) return '雷暴';
  return '未知';
}
/* 协议里天气是单个 wx=temp,code,hi,lo（见 protocol.py 的 _ORDER）。
   以前这里读 t.wx_temp，字段名对不上，所以「天气」永远是「—」。 */
function devWx(){
  const raw = (live.telemetry||{}).wx;
  if(!raw) return null;
  const [temp,code,hi,lo] = String(raw).split(',');
  const t = parseFloat(temp);
  if(!isFinite(t)) return null;
  return {temp:t, code:parseInt(code,10), hi:parseFloat(hi), lo:parseFloat(lo)};
}
function devDateText(){ return String((live.telemetry||{}).date || '') }
function devClockText(){
  const raw = String((live.telemetry||{}).time || '');
  if(!raw) return '--:--:--';
  const p = raw.padStart(6,'0');
  return `${p.slice(0,2)}:${p.slice(2,4)}:${p.slice(4,6)}`;
}
function devWeekday(dateStr){
  const d = new Date(String(dateStr)+'T00:00:00');
  return isNaN(d.getTime()) ? '' : '日一二三四五六'[d.getDay()];
}
function devCountdownText(item){
  const at = new Date(`${item.date}T${item.time}:00`);
  if(isNaN(at.getTime())) return null;
  let remain = Math.floor((at - Date.now())/1000);
  if(remain < 0) return {main:item.title, sub:'已结束'};
  const days = Math.floor(remain/86400);
  remain %= 86400;
  const pad = n => String(n).padStart(2,'0');
  return {main:`${days}天 ${pad(Math.floor(remain/3600))}:${pad(Math.floor(remain/60)%60)}:${pad(remain%60)}`,
          sub:`${item.title} ${item.time}`};
}
/* 通用组件的文案，逐条对应 render_generic 的分支。 */
function devGenericContent(kind){
  const t = live.telemetry || {};
  const today = devDateText(), wd = devWeekday(today);
  if(kind === 'clock'){
    const time = today ? devClockText() : '--:--:--';
    const sub = today && wd ? `${today} 星期${wd}` : '时间未同步';
    const parts = today.split('-');
    const date = parts.length === 3 ? `${Number(parts[0])}/${Number(parts[1])}/${Number(parts[2])}` : today;
    return {main:time, sub, date};
  }
  if(kind === 'environment'){
    return {main: t.temp === undefined ? '温度不可用' : `${Number(t.temp).toFixed(1)} ℃`,
            sub: t.hum === undefined ? '传感器读取失败' : `湿度 ${Math.round(t.hum)}%`};
  }
  if(kind === 'weather'){
    const w = devWx();
    if(!w) return {main:'天气未配置', sub:'等待客户端推送'};
    return {main:`${w.temp.toFixed(1)}℃ ${weatherText(w.code)}`,
            sub: isFinite(w.hi) ? `高 ${Math.round(w.hi)}°  低 ${Math.round(w.lo)}°` : '等待客户端推送'};
  }
  if(kind === 'ai_cost'){
    if(t.ait === undefined) return {main:'今日暂无用量', sub:'AI 会话活动后显示'};
    const k = Number(t.ait) || 0;
    const model = String(t.aiu||'').split(',')[1] || '';
    return {main: k >= 1000 ? `今日 ${(k/1000).toFixed(1)}M` : `今日 ${k}k`, sub: model};
  }
  if(kind === 'countdown'){
    const item = (draft.countdowns||[]).find(x => x.enabled);
    if(!item) return {main:'未设置', sub:'在工作台添加倒计时'};
    return devCountdownText(item) || {main:'未设置', sub:'日期无效'};
  }
  if(kind === 'reminder_list'){
    const day = new Date(today+'T00:00:00').getDay() || 7;   // 0=周日 → 7
    const hit = (draft.reminders||[]).filter(x => x.enabled && (x.weekdays||[]).includes(day))
      .sort((a,b) => String(a.time).localeCompare(String(b.time)))[0];
    return hit ? {main:`${hit.time} ${hit.text}`, sub:'今天的下一条提醒'}
               : {main:'今天无提醒', sub:''};
  }
  if(kind === 'today_calendar'){
    const hit = (draft.calendar||[]).filter(x => x.enabled && x.date === today)
      .sort((a,b) => String(a.time).localeCompare(String(b.time)))[0];
    return hit ? {main:`${hit.time} ${hit.title}`, sub:'今日日程'} : {main:'今天无日程', sub:''};
  }
  if(kind === 'alarm'){
    const day = new Date(today+'T00:00:00').getDay() || 7;
    const hit = (draft.alarms||[]).filter(x => x.enabled && (x.weekdays||[]).includes(day))
      .sort((a,b) => String(a.time).localeCompare(String(b.time)))[0];
    return hit ? {main:`${hit.time} ${hit.label}`, sub:'到点触摸屏幕停止'}
               : {main:'未设置闹钟', sub:''};
  }
  return {main:'—', sub:''};
}

/* ---------- 像素宠物：镜像固件 draw_pet / pet_apply_expression ----------
   三种形象（robot/cat/chick）×两种尺寸（AI 卡小只 sz1 / 宠物页大只 sz2），
   表情联动 AI 状态（出错红眼/抖动、完成眯眼跳、等你弹「!」），
   饿/渴/累 徽标来自 PS 遥测（priority 低于 AI 徽标）。 */
const PET_GEO = { // 与 firmware draw_pet 的坐标表一致（sz 已含）
  robot: { ant:[29,0,6,6], head:[10,5,44,28,8] },
  cat:   { ears:[[12,0,10,9],[42,0,10,9]], tail:[58,24,6,22] },
  chick: { beak:[26,22,12,6] },
};
function petColors(type, ctx, st){
  const c = type==='cat' ? {body:'#C88A4B',edge:'#8A5A2B',limb:'#C88A4B'}
          : type==='chick' ? {body:'#F7D794',edge:'#D9A441',limb:'#D9A441'}
          : {body:devSemantic('robot.body'),edge:ctx.accent,limb:devSemantic('robot.body')};
  if(st==='ERROR'){ c.edge=devSemantic('status.err'); c.eye=devSemantic('status.err'); }
  else c.eye=ctx.text;
  return c;
}
function devPet(card, type, st, bx, by, sz){
  // 三层仲裁（镜像固件 pet_arbitrate）：场景 > 时间层 > 活动层
  const hh = parseInt((((live.telemetry||{}).time)||'').slice(0,2),10);
  let L;
  if(st==='ERROR') L='ALERT';
  else if(st==='DONE') L='CELEBRATE';
  else if(hh>=1 && hh<6 && !(draft.pet?.sleep===0)) L='SLEEPING';
  else{
    const working=['GENERATING','TOOL','BUILD'].includes(st);
    if(((hh>=22 || hh<1) || (hh>=1 && hh<6)) && !working) L='SLEEPY';
    else if(['WAIT_INPUT','ASK'].includes(st)) L='THINKING';
    else if(working) L='WORKING';
    else L='IDLE';
  }
  // 状态视觉表（与固件 pet_apply_expression 同参数：眼高/视线偏移/嘴宽/徽标）
  const V = {WORKING:{eh:8,dy:0,mw:12}, THINKING:{eh:6,dy:-2,mw:12,badge:'?'},
             IDLE:{eh:5,dy:0,mw:12},
             SLEEPY:{eh:3,dy:0,mw:12,badge:'Z'}, SLEEPING:{eh:2,dy:0,mw:12,badge:'z'},
             GREETING:{eh:8,dy:0,mw:16,badge:'嗨'}, CELEBRATE:{eh:3,dy:0,mw:18},
             PLAYING:{eh:4,dy:0,mw:14}, ALERT:{eh:8,dy:0,mw:20,badge:'×'}}[L];
  const ctx = { accent: panelHex(draft.theme.accent),
                text: panelHex(draft.theme.text),
                sub: panelHex(draft.theme.sub),
                bg: panelHex(draft.theme.bg) };
  const col = petColors(type, ctx, st);
  const isErr = st==='ERROR', isDone = st==='DONE', isWait = st==='WAIT_INPUT'||st==='ASK';
  const U = (v)=>Math.round(v*sz);
  const rect=(x,y,w,h,r,color,opa,border)=>{ const n=devAbs(el('div','dev-el'),U(x),U(y),U(w),U(h));
    n.style.borderRadius=U(r)+'px'; if(color){n.style.background=color;}
    n.style.opacity=opa==null?1:opa;
    if(border){n.style.border=Math.max(1,Math.round(sz))+'px solid '+border;}
    card.append(n); return n; };
  const root=devAbs(el('div','dev-el'),U(bx),U(by),U(64),U(80)); root.style.opacity=1; card.append(root);
  const grp=devAbs(el('div','dev-el'),0,0,U(64),U(80)); root.append(grp);
  const R=(x,y,w,h,r,color,opa,border)=>{const n=devAbs(el('div','dev-el'),U(x),U(y),U(w),U(h));
    n.style.borderRadius=U(r)+'px'; if(color){n.style.background=color;}
    n.style.opacity=opa==null?1:opa;
    if(border){n.style.border=Math.max(1,Math.round(sz))+'px solid '+border;}
    grp.append(n); return n;};
  if(type==='robot') R(29,0,6,6,3,devSemantic('status.ok'));
  if(type==='cat'){ R(12,0,10,9,3,col.body,1,col.edge); R(42,0,10,9,3,col.body,1,col.edge); }
  const hy = type==='cat'?7:5;
  R(10,hy,44,28,8,col.body,1,col.edge);
  R(16,hy+5,14,14,7,ctx.accent,0.5); R(34,hy+5,14,14,7,ctx.accent,0.5);
  R(19,hy+8+V.dy,8,V.eh,4,col.eye); R(37,hy+8+V.dy,8,V.eh,4,col.eye);
  if(type==='chick') R(26,hy+17,12,6,2,'#FFB84D');
  else R(24,hy+19,V.mw,3,1, L==='ALERT'?devSemantic('status.err'):L==='CELEBRATE'?devSemantic('status.ok'):ctx.accent);
  const by2 = hy+30;
  R(8,by2,48,26,8,col.body,1,col.edge);
  R(28,by2+8,8,5,2, type==='robot'?ctx.accent:col.edge);
  R(0,by2+3,7,16,3,col.limb); R(57,by2+3,7,16,3,col.limb);
  if(type==='cat') R(58,by2-12,6,22,3,col.limb);
  R(16,by2+28,16,7,3,col.limb); R(34,by2+28,16,7,3,col.limb);
  // 徽标：场景层自带（?/Z/z/嗨/×）> 饿渴累（仅活动层显示，睡觉/场景时不多嘴）
  const ps=(live.telemetry||{}).ps||'';
  const [h,t,e]=ps?ps.split(',').map(Number):[100,100,100];
  let badge=V.badge||'', bcol=devSemantic('status.warn');
  if(L==='ALERT')bcol=devSemantic('status.err');
  else if(badge==='?')bcol=ctx.text;
  else if(badge==='Z'||badge==='z')bcol=ctx.sub;
  if(!badge && (L==='IDLE'||L==='THINKING'||L==='WORKING')){
    if(h<20){badge='饿';}
    else if(t<20){badge='渴';}
    else if(e<20){badge='累';}
  }
  if(badge){ const n=devAbs(el('div','dev-label'),U(bx+44),U(by),U(20),devLineH(24));
    n.style.fontSize=devFont(24)+'px'; n.style.color=bcol; n.textContent=badge; card.append(n); }
}
function devPetCompact(card, ctx, st){
  devPet(card, (draft.pet&&draft.pet.type)||'robot', st, 229, 42, 1);
}
/* 宠物页（W_PET）：大宠物 ×2 + 三条状态条 + 三个交互按钮，几何照抄固件 */
function devPetPage(screen, ctx){
  const type=(draft.pet&&draft.pet.type)||'robot';
  const st=((live.tasks||[])[0]||{}).state||'IDLE';
  devPet(screen, type, st, 96, 66, 2);
  const ps=(live.telemetry||{}).ps||'80,75,90';
  const vals=ps.split(',').map(Number);
  const names=['饱食','水分','活力'];
  names.forEach((label,i)=>{
    devLabel(screen, 24, 240+30*i, 60, label, 20, 'dev-dim');
    const track=devAbs(el('div','dev-el'), 84, 244+30*i, 170, 10);
    track.style.borderRadius='5px';
    track.style.background=panelHex(draft.theme.bg);
    track.style.border='1px solid '+panelHex(draft.theme.card_edge||draft.theme.card);
    screen.append(track);
    const fill=devAbs(el('div','dev-el'), 0, 0, Math.max(0,Math.min(100,vals[i]||0))*1.7, 10);
    fill.style.background=ctx.accent; fill.style.borderRadius='5px';
    track.append(fill);
    devLabel(screen, 262, 240+30*i, 44, String(vals[i]||0), 20, 'dev-ink');
  });
  ['喂食','喝水','运动'].forEach((t,i)=>{
    const btn=devAbs(el('div','dev-el'), 12+102*i, 344, 92, 44);
    btn.style.background=panelHex(draft.theme.card);
    btn.style.border='1px solid '+panelHex(draft.theme.card_edge||draft.theme.card);
    btn.style.borderRadius='10px';
    screen.append(btn);
    devLabel(btn, 0, 0, 92, t, 20, null, ctx.text, 'center');
  });
  devLabel(screen, 10, 396, 300, '状态保存在电脑，互动立即生效', 20, 'dev-dim');
}
/* 工具页（页面带 filter）：顶部完成带 30px + 5 行会话（66px、间隔 4、y=94 起）。/* 工具页（页面带 filter）：顶部完成带 30px + 5 行会话（66px、间隔 4、y=94 起）。
   与固件 make_ai_tool_page / render_ai_filtered 同源；完成带高亮 = 有 10 秒内
   的新完成（已读状态与概览共用本地存储）。 */
function agoText(sec){
  sec = Math.max(0, Number(sec)||0);
  if(sec < 60) return '刚刚';
  if(sec < 3600) return `${Math.floor(sec/60)}分前`;
  return `${Math.floor(sec/3600)}时前`;
}
function devAiToolPage(screen, ctx, filter){
  const band = devCard(screen, 8, DEV.BODY_Y, 304, 30);
  const fresh = (live.done||[]).some(d => (d.at||0) > doneSeenAt());
  if(fresh) band.style.borderColor = ctx.accent;
  const d0 = (live.done||[])[0];
  devLabel(band, 10, 0, 284,
           d0 ? `完成 ${d0.project||'未命名项目'} · ${agoText(d0.age)}` : '暂无完成的会话',
           20, null, fresh ? ctx.accent : undefined);
  const tasks = (live.tasks||[]).filter(t => !filter || t.tool === filter).slice(0,5);
  for(let i=0;i<5;i++){
    const row = devAbs(el('div','dev-el'), 8, 94 + 70*i, 304, 66);
    row.style.background = ctx.bg;
    row.style.border = '1px solid ' + panelHex(draft.theme.card_edge||draft.theme.card);
    screen.append(row);
    const task = tasks[i];
    if(!task) continue;
    devDot(row, 8, 10, 8, devStateColor(task.state, ctx.accent, ctx.sub));
    devLabel(row, 22, 4, 104, task.tool, 20, null,
             task.state === 'ERROR' ? devSemantic('status.err') : undefined);
    devLabel(row, 126, 4, 170, `${task.project || '未命名'} · ${fmtElapsed(task.elapsed)}`,
             20, 'dev-dim', null, 'right');
    devTrack(row, 8, 46, 268, Number(task.prog)||0, ctx.accent);
  }
}
/* 天气城市页预览：当前实况卡 + 5 天预报行，几何照抄固件 make_weather_city。
   数据下标 = 页面 city 在 draft.weather.cities 里的序号（与固件的
   g_profile.city_names 顺序一致——都来自同一份 profile）。 */
function devWeatherCity(screen, ctx, page){
  const cities=(draft.weather&&draft.weather.cities)||[];
  let idx=cities.findIndex(c=>c.name===page.city); if(idx<0) idx=0;
  const t=live.telemetry||{};
  const d=t['w'+idx], f=t['wf'+idx];
  const card=devCard(screen, 8, DEV.BODY_Y, 304, 116);
  const parts=d?String(d).split(','):[];
  // parts 经 split 后永远是数组（无数据时是 []），拿它判真恒为真——数据未到
  // 时会把 "undefined℃" 画上屏。判据必须是原始的 d。
  devLabel(card, 14, 4, 150, d?`${parts[0]}℃`:'--℃', 48, 'dev-ink');
  devLabel(card, 14, 70, 140, d?weatherText(+parts[1]):'天气未配置', 24, null, ctx.accent);
  devLabel(card, 150, 78, 140, d?`高 ${+parts[2]}° 低 ${+parts[3]}°`:'等客户端推送', 20, 'dev-dim');
  const days=f?String(f).split(';'):[];
  const today=new Date().getDay();     // 0=周日
  for(let i=0;i<5;i++){
    const row=devAbs(el('div','dev-el'), 8, 184 + 50*i, 304, 46);
    row.style.background=ctx.bg;
    row.style.border='1px solid '+panelHex(draft.theme.card_edge||draft.theme.card);
    screen.append(row);
    const dp=days[i]?days[i].split(',').map(Number):null;
    const wd='日一二三四五六'[(today+i)%7];
    const name=i===0?'今天':i===1?'明天':i===2?'后天':`周${wd}`;
    devLabel(row, 12, 4, 64, name, 20, 'dev-dim');
    devLabel(row, 80, 4, 110, dp?weatherText(dp[0]):'--', 20);
    devLabel(row, 194, 4, 98, dp?`${dp[1]}° ${dp[2]}°`:'', 20, null, null, 'right');
  }
}
function devAiPage(screen, ctx, page){
  const filter = (page && page.filter) || '';
  if(filter){ devAiToolPage(screen, ctx, filter); return; }
  const tasks = (live.tasks||[]).slice(0,4);
  const card = devCard(screen, DEV.AI.x, DEV.AI.y, DEV.AI.w, DEV.AI.h);
  const running = tasks.filter(t => devRunning(t.state)).length;
  const focus = tasks[0];
  devLabel(card, 14, 10, 178, focus ? `${focus.tool} · ${String(focus.id||'').slice(0,4)}` : 'AI 会话',
           20, 'dev-dim');
  devLabel(card, 192, 10, 98, `活跃 ${running}/${tasks.length}`, 20, 'dev-dim', null, 'right');
  if(!focus){
    devLabel(card, 14, 42, 208, '暂无活跃会话', 32, 'dev-ink');
    devDot(card, 14, 102, 10, ctx.sub);
    devLabel(card, 30, 96, 196, '等待编辑器活动', 20, null, ctx.accent);
    devLabel(card, 14, 134, 276, '多编辑器启动后自动加入', 20, 'dev-dim');
    devTrack(card, 14, 166, 276, 0, ctx.accent);
  }else{
    devLabel(card, 14, 42, 208, focus.project || '未命名项目', 32, 'dev-ink');
    const dotCol = devStateColor(focus.state, ctx.accent, ctx.sub);
    devDot(card, 14, 102, 10, dotCol);
    devLabel(card, 30, 96, 196, devStateShort(focus.state), 20, null, dotCol);
    devLabel(card, 14, 134, 276,
             `运行 ${fmtElapsed(focus.elapsed)} · ${Math.max(0,focus.age||0)} 秒前更新`, 20, 'dev-dim');
    devTrack(card, 14, 166, 276, Number(focus.prog)||0, ctx.accent);
  }
  // 其他会话 3 行
  const others = tasks.slice(1,4);
  for(let i=0;i<3;i++){
    const row = devAbs(el('div','dev-el'), 10, 174 + 68*i, 284, 62);
    row.style.background = ctx.bg;
    card.append(row);
    const task = others[i];
    if(!task) continue;
    devDot(row, 8, 10, 8, devStateColor(task.state, ctx.accent, ctx.sub));
    devLabel(row, 22, 4, 104, task.tool, 20, null,
             task.state === 'ERROR' ? devSemantic('status.err') : undefined);
    devLabel(row, 126, 4, 150, `${task.project || '未命名'} · ${fmtElapsed(task.elapsed)}`,
             20, 'dev-dim', null, 'right');
    devTrack(row, 8, 40, 268, Number(task.prog)||0, ctx.accent);
  }
  // 单会话时用空位显示电脑摘要（固件逻辑：total<=1 才显示）
  if(tasks.length <= 1){
    const t = live.telemetry || {};
    const box = devAbs(el('div','dev-el'), 10, 212, 284, 154);
    box.style.background = ctx.bg;
    card.append(box);
    devLabel(box, 12, 10, 258, '电脑概览', 20, 'dev-dim');
    if(isConnected()){
      devLabel(box, 12, 52, 258, `CPU ${t.cpu===undefined?'--':t.cpu}%   内存 ${t.mem===undefined?'--':t.mem}%`,
               20, null, ctx.text);
      const net = (isFinite(t.netrx) && isFinite(t.nettx)) ? (Number(t.netrx)+Number(t.nettx)).toFixed(1) : null;
      devLabel(box, 12, 98, 258, `磁盘 ${t.disk===undefined?'--':t.disk}%   网络 ${net===null?'--':net+' Mb'}`,
               20, null, ctx.text);
    }else{
      devLabel(box, 12, 52, 258, '等待电脑数据', 20, null, ctx.text);
    }
  }
  devPetCompact(card, ctx, focus ? focus.state : 'IDLE');
}

/* ---------- 硬件四卡页：镜像 make_hardware + render_hardware ---------- */
function devHwPage(screen, ctx){
  const t = live.telemetry || {};
  const C = DEV.HW_CARD;
  const titles = ['处理器','内存','磁盘','网络'];
  const ok = isConnected();
  titles.forEach((title,i) => {
    const x = C.x0 + (i%2)*C.dx, y = C.y0 + Math.floor(i/2)*C.dy;
    const card = devCard(screen, x, y, C.w, C.h);
    devLabel(card, 12, 18, C.w-24, title, 20, 'dev-dim');
    let value = '--', note = '数据不可用', pct = 0, color = ctx.accent;
    if(ok){
      if(i===0){ value = `${t.cpu===undefined?'--':t.cpu}%`; note = '即时负载'; pct = Number(t.cpu)||0; }
      if(i===1){ value = `${t.mem===undefined?'--':t.mem}%`;
                 note = t.memtotal ? `${t.memused}/${t.memtotal} GB` : '即时负载'; pct = Number(t.mem)||0; }
      if(i===2){ value = `${t.disk===undefined?'--':t.disk}%`;
                 note = t.diskfree===undefined ? '使用率' : `余 ${Math.round(t.diskfree)} G`;
                 pct = Number(t.disk)||0;
                 if(Number(t.disk) >= 90) color = devSemantic('status.warn'); }
      if(i===3){
        if(isFinite(t.netrx) && isFinite(t.nettx)){
          const sum = Number(t.netrx)+Number(t.nettx);
          value = `${Math.round(sum)} Mb`;
          note = `下${Math.round(t.netrx)} 上${Math.round(t.nettx)}`;
          pct = Math.round(sum*100/50);
        }else{ value = '--'; note = '网速未提供'; }
      }
    }
    devLabel(card, 12, 46, C.w-24, value, 30, 'dev-ink');
    devTrack(card, 12, 98, C.w-24, pct, color);
    devLabel(card, 12, 114, C.w-24, note, 20, 'dev-dim');
  });
  // 底部整宽「温度与风扇」三列卡（与固件 make_hardware 同几何）
  const tc = devCard(screen, 8, C.y0+296, 304, 84);
  const clean = (v)=>v!==undefined && v!=='' && v!==null && isFinite(Number(v));
  const hwCols = [
    ['CPU', clean(t.cputemp) ? Math.round(Number(t.cputemp))+'℃' : '--'],
    ['GPU', clean(t.gputemp) ? Math.round(Number(t.gputemp))+'℃' : '--'],
    ['风扇', clean(t.fan) ? String(Math.round(Number(t.fan))) : '--'],
  ];
  hwCols.forEach((c,i)=>{
    devLabel(tc, 16+i*100, 12, 88, ok?c[0]:'', 20, 'dev-dim');
    devLabel(tc, 16+i*100, 38, 88, ok?c[1]:'--', 28, 'dev-ink');
  });
}

/* ---------- 通用组件卡：镜像 make_generic + render_generic ---------- */
function devGenericCard(screen, ctx, kind, x, y, w, h){
  const card = devCard(screen, x, y, w, h);
  devLabel(card, 12, 12, w-24, DEV_WIDGET_TITLES[kind] || '', 20, 'dev-dim');
  const compact = h < 110;
  const content = devGenericContent(kind);
  // 大时钟卡（非紧凑 + 宽度够）：32px 时间 + 右栏「周X / YYYY/M/D」
  if(kind === 'clock' && !compact && w >= 280){
    const wd = devWeekday(devDateText());
    devLabel(card, 12, 50, w-112, content.main, 32, 'dev-ink');
    devLabel(card, w-96, 50, 84, wd ? `周${wd}` : '', 24, 'dev-dim', null, 'right');
    devLabel(card, w-96, 86, 84, content.date, 20, null, ctx.accent, 'right');
    return card;
  }
  devLabel(card, 12, compact ? 44 : 68, w-24, content.main, compact ? 20 : 28, 'dev-ink');
  if(!compact) devLabel(card, 12, h-40, w-24, content.sub, 20, 'dev-dim');
  return card;
}

/* ---------- 页面组合 → 组件矩形：镜像 firmware build_page 的槽位算法 ---------- */
function devWidgetUnits(kind){ return (kind === 'reminder_list' || kind === 'today_calendar') ? 2 : 1 }
function devBodyLayout(page){
  if(page.widgets.includes('ai_list')) return [{kind:'ai_list'}];
  if(page.widgets.includes('hw_cards')) return [{kind:'hw_cards'}];
  const slots = page.widgets.reduce((n,k) => n + devWidgetUnits(k), 0);
  const rows = Math.max(1, Math.min(2, Math.ceil(slots/2)));
  const cardH = Math.floor((DEV.BODY_H - (rows-1)*DEV.GRID_GAP) / rows);
  const out = [];
  if(page.layout === 'list'){
    const h = Math.floor(DEV.BODY_H / page.widgets.length) - 6;
    page.widgets.forEach((kind,i) => out.push({kind, x:8, y:DEV.BODY_Y + i*(h+6), w:304, h}));
  }else{
    let cursor = 0;
    page.widgets.forEach(kind => {
      const wide = devWidgetUnits(kind) === 2;
      if(wide && (cursor % 2)) cursor++;
      out.push({kind, x:8 + (cursor%2)*156,
                y:DEV.BODY_Y + Math.floor(cursor/2)*(cardH+DEV.GRID_GAP),
                w: wide ? 304 : 148, h:cardH});
      cursor += wide ? 2 : 1;
    });
  }
  return out;
}

/* 一整屏：顶栏 + 内容 + 页脚，全部按固件坐标摆放。 */
function devScreen(pageIndex, theme, host){
  const page = (draft.pages||[])[pageIndex] || (draft.pages||[])[0];
  // host 是页面上已有的 .screen 容器；给了就填进去，不再套一层 bezel
  // （HTML 里已经是 .screen-scale > .screen-bezel > .screen 的结构）。
  const screen = host || el('div','screen');
  if(host) screen.replaceChildren();
  applyDeviceTheme(theme, screen);
  if(!page) return screen;
  const ctx = {
    bg: panelHex(theme.bg), accent: panelHex(theme.accent),
    sub: panelHex(theme.sub), text: panelHex(theme.text),
  };
  if(!isConnected()){
    // 离线：顶部 4px 红条（固件的 offbar）
    const bar = devAbs(el('div','dev-el'), 0, 0, DEV.W, 4);
    bar.style.background = devSemantic('status.err');
    screen.append(bar);
  }
  // 顶栏：左页面标题 · 中短时钟 · 右天气 + 状态点（对应 build_page 的 HEAD_Y 行）
  // 坐标与 firmware build_page 的顶栏一致：时钟显示到秒后需要 ~96px，
  // 标题收到 100px、天气右对齐收窄到 76px 让位（详见 ui.cpp 的注释）。
  const topclock = Number(draft.theme.topclock ?? 1), topwx = Number(draft.theme.topwx ?? 1);
  devLabel(screen, 8, DEV.HEAD_Y, topclock ? 100 : (topwx ? 174 : 190),
           page.title, 20, 'dev-ink');
  if(topclock){
    const clk = devLabel(screen, 112, DEV.HEAD_Y, 100, clockHMS(), 20, null, ctx.accent, 'center');
    clk.dataset.role = 'topclock';
    // 浏览器字体的数字比 Noto Sans SC 宽约 6%（同样 8 个字符：102.4px vs 96.4px），
    // 24px 会在 100px 盒子里被截成 "01:08:…"。盒子尺寸照抄固件不变，只把字号
    // 收到 22px 让整串放得下——布局与实机一致，只有数字宽度这一点是近似的。
    clk.style.fontSize = '22px';
  }
  if(topwx){
    const w = devWx();
    devLabel(screen, 216, DEV.HEAD_Y, 76, w ? weatherText(w.code) : '', 20, 'dev-dim', null, 'right');
  }
  if(topclock || topwx) devDot(screen, 300, DEV.HEAD_Y+6, 12, isConnected() ? devSemantic('status.ok') : devSemantic('status.err'));
  else devLabel(screen, 210, DEV.HEAD_Y+2, 100, isConnected() ? '在线' : '电脑离线', 20, null,
                isConnected() ? devSemantic('status.ok') : devSemantic('status.err'));
  // 内容区
  for(const slot of devBodyLayout(page)){
    if(slot.kind === 'ai_list'){ devAiPage(screen, ctx, page); continue }
    if(slot.kind === 'weather' && page.city){ devWeatherCity(screen, ctx, page); continue }
    if(slot.kind === 'pet'){ devPetPage(screen, ctx); continue }
    if(slot.kind === 'hw_cards'){ devHwPage(screen, ctx); continue }
    devGenericCard(screen, ctx, slot.kind, slot.x, slot.y, slot.w, slot.h);
  }
  // 页脚：左下角「设」按钮 + 居中页码（视觉 32×22，间距 10）
  devLabel(screen, 4, DEV.FOOT_Y-2, 40, '设', 20, null, ctx.text, 'center');
  const counts = (draft.pages||[]).length;
  const total = counts*DEV.DOT_W + (counts-1)*DEV.DOT_GAP;
  const x0 = Math.floor((DEV.W-total)/2);
  (draft.pages||[]).forEach((_,k) => {
    const on = k === pageIndex;
    const dot = devAbs(el('div','dev-el'), x0 + k*(DEV.DOT_W+DEV.DOT_GAP), DEV.FOOT_Y, DEV.DOT_W, DEV.DOT_H);
    dot.style.background = on ? ctx.accent : panelHex(theme.card);
    dot.style.borderRadius = '6px';
    dot.style.border = '1px solid ' + panelHex(theme.card_edge||theme.card);
    screen.append(dot);
    devLabel(dot, 0, 0, DEV.DOT_W, String(k+1), 20, null,
             on ? ctx.bg : ctx.text, 'center');
  });
  return screen;
}
/* 把 320×480 的舞台缩放进容器，避免窄栏溢出。scale<=1，只缩不放。 */
function fitStage(container){
  if(!container || !container.firstElementChild) return;
  const avail = container.clientWidth || 0;
  if(!avail) return;
  const stage = container.firstElementChild;
  const k = Math.min(1, avail / (DEV.W + 16));
  stage.style.transform = `scale(${k})`;
  container.style.height = Math.ceil((DEV.H + 16) * k) + 'px';
}
/* 四种布局预设＝真实的页面组合。每个页面都由固件真能渲染的组件组成；
   以前这里写的是一套只有浏览器认识的布局参数（tv.*），从不下发到设备。 */
const LAYOUT_PRESETS = {
  focus:     {desc:'AI 页独占：焦点会话 + 像素机器人 + 进度条',
              pages:[{id:'ai', title:'状态聚焦', layout:'list', widgets:['ai_list']}]},
  timeline:  {desc:'AI 页（其余会话带用时，可当轻量时间线）+ 一页今日用量',
              pages:[{id:'ai', title:'任务时间线', layout:'list', widgets:['ai_list']},
                     {id:'cost', title:'用量', layout:'grid_2x2', widgets:['ai_cost','clock']}]},
  companion: {desc:'AI 页的像素机器人随状态改表情（思考/出错/完成/等你），另加一页温湿度；小屏没有独立场景页',
              pages:[{id:'ai', title:'角色陪伴', layout:'list', widgets:['ai_list']},
                     {id:'env', title:'环境', layout:'grid_2x2', widgets:['environment','weather']}]},
  dashboard: {desc:'电脑四卡（处理器/内存/磁盘/网络）+ 一页今日用量',
              pages:[{id:'pc', title:'数据看板', layout:'grid_2x2', widgets:['hw_cards']},
                     {id:'cost', title:'用量', layout:'grid_2x2', widgets:['ai_cost','clock']}]},
};
const schemeNames = {focus:'状态聚焦', timeline:'任务时间线', companion:'角色陪伴', dashboard:'数据看板'};
function isConnected(){ return String(live.device||'').startsWith('connected:') }

let previewPage = 0;
function renderTaskPreview(){
  const host = $('screen');
  if(!host) return;
  devScreen(previewPage, draft.theme, host);
  const pages = draft.pages||[];
  $('preview-caption').textContent =
    `第 ${previewPage+1}/${pages.length} 页 · ${pages[previewPage]?.title||'—'} · ` +
    (isConnected() ? '小屏已连接，实时生效' : '小屏未连接，画面按本机遥测渲染');
  const cal = panelSpec().calibration || {};
  if($('panel-pipeline')){
    $('panel-pipeline').textContent =
      `预览已过屏幕色彩管线：${panelSpec().pixelFormat} 量化` +
      `（标定第 ${cal.cell||'—'} 格：${String(cal.rgbOrder||'RGB')}` +
      `${cal.lvglFlushByteSwap?' + flush 换字节':''}${cal.panelInvert?' + 硬件反相':''}）`;
  }
  fitStage($('screen-scale'));
}
function renderPageSelect(){
  const sel = $('page-select');
  if(!sel) return;
  const pages = draft.pages||[];
  sel.replaceChildren();
  pages.forEach((page,i) => {
    const option = el('option','',`第 ${i+1} 页 · ${page.title}`);
    option.value = String(i);
    sel.append(option);
  });
  if(previewPage >= pages.length) previewPage = 0;
  sel.value = String(previewPage);
}
/* 任务视图右栏：列出真实页面组合，让用户看见具体下发什么。 */
function renderTaskPages(){
  const root = $('task-pages');
  if(!root) return;
  root.replaceChildren();
  (draft.pages||[]).forEach((page,i) => {
    const b = el('button','page-item'+(i===previewPage?' active':''));
    b.type = 'button';
    b.append(el('span','page-number',String(i+1).padStart(2,'0')),
             el('strong','',page.title + (page.filter?`（只看 ${page.filter}）`:'')),
             el('small','',page.widgets.map(k => (widgets[k]||[k])[0]).join(' · ')));
    b.draggable = true;
    b.onclick = () => { previewPage = i; renderPageSelect(); renderTaskPreview(); renderTaskPages() };
    b.ondragstart = () => { dragTaskPage = i; b.classList.add('dragging') };
    b.ondragend = () => { b.classList.remove('dragging') };
    b.ondragover = e => e.preventDefault();
    b.ondrop = e => {
      e.preventDefault();
      if(dragTaskPage < 0 || dragTaskPage === i) return;
      manualOrder();
      reorder(draft.pages, dragTaskPage, i);
      previewPage = i;
      renderPageSelect(); renderTaskPages(); renderTaskPreview();
    };
    root.append(b);
  });
  const page = (draft.pages||[])[previewPage];
  if($('task-page-title') && page) $('task-page-title').value = page.title;
}

/* ---------- 主题库 ---------- */
function myThemes(){
  try{ return JSON.parse(localStorage.getItem('workbuddy-mythemes')||'[]'); }catch(e){ return [] }
}
/* 自定义配色预校验：镜像 profile.validate_profile 的合成后对比度规则
   （text≥4.5、sub≥3.0、卡片边界≥1.5，全部按 RGB565 上屏色 + 卡片不透明度合成）。
   后端保存时仍会复检——这里提前拦是为了不让「存了却永远应用不了」的主题进库。 */
function themeIssues(colors, opa){
  const P={};
  ['bg','grad','card','accent','text','sub'].forEach(k=>P[k]=hexToRgb(panelHex(colors[k])));
  const alpha=(opa||100)/100, issues=[];
  const fills=[composite(P.card,P.bg,alpha), composite(P.card,P.grad,alpha)];
  const names=['背景端','渐变端'];
  if(contrastRatio(P.text,P.bg)<4.5)issues.push('文字与背景对比不足 4.5:1');
  fills.forEach((f,i)=>{ if(contrastRatio(P.text,f)<4.5)issues.push(`文字与卡片对比不足（${names[i]}）`); });
  if(contrastRatio(P.sub,P.bg)<3)issues.push('次要文字与背景对比不足 3:1');
  fills.forEach((f,i)=>{ if(contrastRatio(P.sub,f)<3)issues.push(`次要文字与卡片对比不足（${names[i]}）`); });
  fills.forEach((f,i)=>{ if(contrastRatio(f,P[i?'grad':'bg'])<1.5)issues.push(`卡片边界看不清（${names[i]}，需 ≥1.5:1，可提高卡片不透明度）`); });
  return issues;
}
function renderThemeGrid(){
  const root=$('theme-grid'); root.replaceChildren();
  const items = themeTab==='preset' ? themeLibrary
    : myThemes().map((t,i)=>({key:'my-'+i, _idx:i, name:t.name||'我的主题 '+(i+1), colors:t.colors, opa:t.opa||70}));
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
    if(themeTab==='my'){
      cell.style.position='relative';
      const del=el('button','theme-del','×'); del.type='button'; del.title='删除该自定义主题';
      del.style.cssText='position:absolute;top:4px;right:4px;width:22px;height:22px;line-height:20px;'
        +'border-radius:50%;border:1px solid rgba(255,255,255,.25);background:rgba(0,0,0,.35);'
        +'color:#fff;font-size:14px;cursor:pointer;padding:0';
      del.onclick=(e)=>{ e.stopPropagation();
        const list=myThemes(); const removed=list.splice(item._idx,1);
        localStorage.setItem('workbuddy-mythemes',JSON.stringify(list));
        if(selectedThemeKey===item.key)selectedThemeKey=null;
        renderThemeGrid(); notice(`已删除「${removed[0]?.name||item.name}」`);
      };
      cell.append(del);
    }
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
    if(item.bright)draft.theme.bright=item.bright;
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
  {v:3, name:'诗词短句', desc:'名句伴眠·可轮换'},
  {v:4, name:'相册轮播', desc:'图片轮换'}
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
    if(d.v===1){
      const t=live.telemetry||{};
      prev.append(el('span','dial-sub',
        t.temp===undefined&&t.hum===undefined ? '传感离线'
          : `${t.temp===undefined?'--':Number(t.temp).toFixed(1)}℃ ${t.hum===undefined?'--':Math.round(t.hum)+'%'}`));
    }
    if(d.v===2)prev.append(el('i','dial-pixel'));
    // 诗词样式缩略图显示用户真正会看到的短句，不再写死示范诗句
    if(d.v===3)prev.append(el('span','dial-poem',
      (draft.theme.savermsg||'').trim().slice(0,6)||'（未设置）'));
    cell.append(prev, el('span','dial-name',d.name), el('small','dial-desc',d.desc));
    cell.onclick=()=>{sv.dial=d.v;sv.mode='theme';saveSVLocal();renderSaverView()};
    root.append(cell);
  });
}
/* 息屏画面预览：与 firmware build_saver() 同源。
   黑底是固件写死的（ui.cpp 用 0x000000），四款样式的坐标与固定配色照搬；
   只有「像素夜色」的色带取主题强调色。短句用 draft.theme.savermsg——
   以前这里放的是硬编码诗句，改「息屏语句」输入框画面纹丝不动。 */
function svStage(style,msg,always,host){
  const screen = host || el('div','sv-screen');
  if(host) screen.replaceChildren();
  screen.style.background=devSemantic('saver.black');
  const date=devDateText(), wd=devWeekday(date);
  const dateLine=date?`${date} 星期${wd}`:'';
  const time=devClockText().slice(0,5);
  if(always){
    devLabel(screen,0,190,320,time,48,null,devSemantic('saver.dim'),'center');
    devLabel(screen,0,262,320,'屏幕常亮 · 不会进入息屏',24,null,devSemantic('saver.label'),'center');
    return screen;
  }
  if(style===1){
    const frame=devAbs(el('div','dev-el'),20,130,280,220);
    frame.style.background=devSemantic('saver.frame');
    frame.style.border='2px solid '+devSemantic('saver.edge');
    frame.style.borderRadius='14px';
    screen.append(frame);
    devLabel(frame,30,30,220,time,32,null,devSemantic('saver.time'));
    devLabel(frame,30,80,220,dateLine,20,null,devSemantic('saver.quote'));
    const t=live.telemetry||{};
    devLabel(frame,30,120,220,
             (t.temp===undefined&&t.hum===undefined) ? '传感离线'
               : `${t.temp===undefined?'--':Number(t.temp).toFixed(1)}℃  ${t.hum===undefined?'--':Math.round(t.hum)}%`,
             20,null,devSemantic('saver.dim'));
    devLabel(frame,30,156,220,wd?`星期${wd}`:'',20,null,devSemantic('saver.dim'));
    devLabel(screen,10,8,120,'方寸仪表',20,null,devSemantic('saver.label'));
  }else if(style===2){
    const band=devAbs(el('div','dev-el'),0,190,320,96);
    band.style.background=panelHex(draft.theme.accent);
    screen.append(band);
    devLabel(band,0,12,320,time,48,null,devSemantic('saver.black'),'center');
    devLabel(screen,0,300,320,dateLine,24,null,devSemantic('saver.dim'),'center');
    devLabel(screen,10,8,200,'PIXEL NIGHT',20,null,devSemantic('saver.label'));
  }else if(style===3){
    devLabel(screen,10,8,140,time,24,null,devSemantic('saver.dim'));
    // 固件用 LV_LABEL_LONG_WRAP + set_width(272)，高度自适应：短句可以多行。
    // 所以这里必须放开固定行高，否则第二行起会被 overflow:hidden 吃掉。
    const quote=devLabel(screen,24,150,272,msg||'代码写完了吗？\n起来喝口水吧',
                         24,null,devSemantic('saver.quote'));
    quote.style.whiteSpace='pre-wrap';
    quote.style.height='auto';
    quote.style.lineHeight='1.5';
    devLabel(screen,10,430,300,dateLine,20,null,devSemantic('saver.label'));
  }else{
    devLabel(screen,0,190,320,time,48,null,devSemantic('saver.dim'),'center');
    devLabel(screen,0,262,320,dateLine,24,null,devSemantic('saver.label'),'center');
  }
  return screen;
}
function renderSaverPreview(){
  const always = sv.mode==='always';
  const style = always ? 0 : (Number(draft.theme.saver)||0);
  const host=$('sv-screen');
  if(host){
    try{ svStage(style, draft.theme.savermsg, always, host) }
    catch(e){ host.replaceChildren(); host.append(el('p','hint','渲染失败：'+e.message)) }
    fitStage($('sv-scale'));
  }
  if($('sv-mode-note')){
    $('sv-mode-note').textContent = always
      ? '当前使用：常亮（不息屏）'
      : `当前使用：${dialDefs[style] ? dialDefs[style].name : '时序'}`;
  }
  if($('sv-quote-note')){
    const msg=(draft.theme.savermsg||'').trim();
    $('sv-quote-note').textContent = (style===3 && !always)
      ? `预览中的短句就是设备上会显示的那句：${msg||'（未设置，用固件默认）'}`
      : '短句只在「诗词短句」样式上显示。';
  }
}
function renderPoemList(){
  const root=$('poem-list'); if(!root) return;
  root.replaceChildren();
  const poems=draft.poems||[];
  if(!poems.length){ root.append(el('div','empty','诗句库为空——设备用内置四句轮换，可在下方添加')); return }
  poems.forEach((p,i)=>{
    const row=el('div','weather-result');
    row.append(el('b','',`第 ${i+1} 句`));
    row.append(el('small','',p));
    const del=el('button','button danger','删除');
    del.onclick=()=>{ draft.poems.splice(i,1); markDirty(); renderPoemList(); };
    row.append(del);
    root.append(row);
  });
}
function renderSaverView(){
  $('saver-mode').value=sv.mode;
  $('saver-hide-lock').checked=!!sv.hideLock;
  // 屏保增强控件：按样式联动显隐 + 回填当前值
  const dial=Number(draft.theme.saver||0);
  if($('saver-extra')) $('saver-extra').hidden=!(dial===4||dial===3);
  if($('album-mgr')) $('album-mgr').hidden=dial!==4;
  if($('poem-lib')) $('poem-lib').hidden=dial!==3;
  if($('sv-int')) $('sv-int').value=String(draft.theme.svint||60);
  if($('sv-fx')) $('sv-fx').value=String(draft.theme.svfx==null?1:draft.theme.svfx);
  if($('poem-int')) $('poem-int').value=String(draft.theme.pint||5);
  if($('album-state')) $('album-state').textContent=
    `已上传 ${Number(draft.svalbum||0)} 张（设备最多存 8 张，重传同槽位即替换）`;
  renderPoemList();
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
    b.ondrop=e=>{e.preventDefault();manualOrder();reorder(draft.pages,dragPage,i);selected=i;renderTaskPages()};
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
  for(const key of ['bg','grad','card','card_edge','accent','text','sub']) $('theme-'+key).value='#'+draft.theme[key];
  $('theme-bright').value=draft.theme.bright;$('bright-value').textContent=draft.theme.bright+'%';
  updateQuantNote();
}
/* 取色器下方那行：把「请求色 → 屏幕色」的量化结果摆出来。
   深色主题里量化偏移往往肉眼可辨，选完色就该看见，而不是等上屏才发现。 */
function updateQuantNote(){
  const el=$('theme-quant-note');
  if(!el||!draft||!draft.theme)return;
  const keys=['bg','grad','card','card_edge','accent','text','sub'];
  const rows=keys.map(k=>({k, c:panelColor(draft.theme[k])}));
  const worst=rows.reduce((a,b)=>b.c.drift>a.c.drift?b:a, rows[0]);
  const cal=panelSpec().calibration||{};
  el.textContent = `屏幕色彩管线：${panelSpec().pixelFormat} 量化，标定第 ${cal.cell||'—'} 格`
    + `（${String(cal.rgbOrder||'RGB')}${cal.lvglFlushByteSwap?' + flush 换字节':''}`
    + `${cal.panelInvert?' + 硬件反相':''}）。最大量化偏移：`
    + `${COLOR_FIELD_NAMES[worst.k]||worst.k} Δ${worst.c.drift}`
    + `（#${worst.c.req} → #${worst.c.hex}）`;
  el.className = worst.c.drift>=8 ? 'hint quant-warn' : 'hint';
}
const COLOR_FIELD_NAMES={bg:'背景',grad:'渐变',card:'卡片',card_edge:'描边',accent:'强调',text:'文字',sub:'次要文字'};
/* ================= 设置 · 天气（多城市） =================
   draft.weather.cities：[{name, lat, lon, label}]，最多 3 个。
   删除城市时同步删除绑定它的页面，否则 profile 校验不过（页面 city
   必须指向已配置的城市）。 */
function renderWeatherTab(){
  const list=$('weather-city-list'); if(!list) return;
  const cities=(draft.weather&&draft.weather.cities)||[];
  const count=$('weather-city-count');
  if(count) count.textContent=`${cities.length}/3`;
  list.replaceChildren();
  if(!cities.length){
    list.append(el('div','empty','还没有城市。搜索并添加，最多 3 个。'));
  }
  cities.forEach((c,i)=>{
    const row=el('div','weather-result');
    row.append(el('b','',c.name));
    row.append(el('small','',c.label||''));
    const del=el('button','button danger','删除');
    del.onclick=()=>{
      cities.splice(i,1);
      // 同步删除绑定该城市的页面，保证 profile 可保存
      draft.pages=(draft.pages||[]).filter(p=>p.city!==c.name);
      if(previewPage>=draft.pages.length) previewPage=Math.max(0,draft.pages.length-1);
      markDirty(); renderWeatherTab(); renderPages(); renderPageSelect(); renderTaskPages(); renderTaskPreview();
      notice(`已删除「${c.name}」及其小屏页面，保存并同步生效`);
    };
    row.append(del);
    list.append(row);
  });
}
function renderWeatherSearch(results, q){
  const root=$('weather-search-results'); if(!root) return;
  root.replaceChildren();
  if(!results.length){ root.append(el('div','empty',`没有找到「${q}」的匹配城市——试试更短的关键词（如「天津」「杭州」）`)); return }
  results.forEach(r=>{
    const row=el('div','weather-result');
    row.append(el('b','',r.name));
    row.append(el('small','',r.label));
    const add=el('button','button','添加');
    add.onclick=()=>{
      const cities=(draft.weather=draft.weather||{}).cities=draft.weather.cities||[];
      if(cities.length>=3){notice('天气城市最多 3 个，先删一个再试',true);return}
      if(cities.some(c=>c.name===r.name)){notice(`「${r.name}」已经在列表里`);return}
      cities.push(r);
      markDirty(); renderWeatherTab();
      notice(`已添加「${r.name}」，保存并同步后小屏生效`);
    };
    row.append(add);
    root.append(row);
  });
}

/* ================= 设置 · 宠物 =================
   状态在客户端 pet.py（本机持久化，惰性衰减），设备只负责显示+触控。
   类型/名字走 profile（/api/pet-config），设备重建宠物形象。 */
const PET_TYPES = {robot:'机器人', cat:'小猫', chick:'小鸡'};
const PET_FACES = {robot:'🤖', cat:'🐱', chick:'🐤'};
let petCache = null;
function renderPetTab(){
  const types=$('pet-types'); if(!types) return;
  types.replaceChildren();
  Object.entries(PET_TYPES).forEach(([key,label])=>{
    const card=el('button','pet-type'+((petCache?.type||draft.pet?.type||'robot')===key?' active':''));
    card.type='button';
    card.append(el('span','pet-face',PET_FACES[key]), el('span','',label));
    card.onclick=()=>{ if(petCache) petCache.type=key; renderPetTab(); };
    types.append(card);
  });
  const name=$('pet-name');
  if(name && document.activeElement!==name) name.value=draft.pet?.name||'帽子';
  const sleepT=$('pet-sleep'), greetT=$('pet-greet');
  if(sleepT) sleepT.checked=!(draft.pet?.sleep===0);
  if(greetT) greetT.checked=!!draft.pet?.greet;
  renderPetStats();
}
function renderPetStats(){
  const root=$('pet-stats'); if(!root) return;
  const m=petCache||{};
  root.replaceChildren();
  [['饱食',m.hunger,'喂食'],['水分',m.thirst,'喝水'],['活力',m.energy,'运动']].forEach(([label,val])=>{
    const row=el('div','pet-stat');
    row.append(el('span','',label));
    const track=el('span','track'); const fill=el('span','fill');
    fill.style.width=(val==null?0:Math.max(0,Math.min(100,val)))+'%';
    track.append(fill); row.append(track);
    row.append(el('span','val',val==null?'—':val+''));
    root.append(row);
  });
  const mood=$('pet-mood');
  if(mood) mood.textContent=({ok:'状态不错',hungry:'饿了',thirsty:'渴了',tired:'累了'})[m.mood]||'—';
}
async function petRefresh(){
  try{ petCache=await request('/api/pet'); }catch(e){}
  renderPetStats();
}

function renderSettings(){
  renderPages();renderPalette();renderInspector();
  renderMusic();renderMusicNow();
  renderWeatherTab(); petRefresh(); renderPetTab();
  switchSettingsTab(settingsTab);
}
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

/* 关键渲染优化：用 keyed DOM reconciliation 替代 replaceChildren 整块重建。
   root.replaceChildren() 会每次 2.5s 轮询时销毁并重建整片 DOM，触发重排/重绘，
   表现为整屏闪烁、滚动跳动。改成按稳定 key（tool / 会话 id）复用节点、仅更新文本
   与状态类、重排用 insertBefore，节点不再被销毁，闪烁与跳动一并消除。 */
function reconcile(parent, items, keyOf, makeEl, updateEl){
  const existing=new Map();
  for(const c of Array.from(parent.children)){
    if(c.__key!==undefined) existing.set(c.__key, c);
  }
  let anchor=parent.firstChild;
  for(const item of items){
    const k=keyOf(item);
    let node=existing.get(k);
    if(node){ existing.delete(k); } else { node=makeEl(item); node.__key=k; }
    updateEl(node, item);
    if(node!==anchor) parent.insertBefore(node, anchor);
    anchor = node.nextSibling;
  }
  for(const n of existing.values()) n.remove();
}

const OV_STATE_CLS={THINKING:'run',GENERATING:'run',TOOL:'run',BUILD:'run',DONE:'done',ERROR:'alert',WAIT_INPUT:'wait',ASK:'wait'};

function buildTaskItem(task){
  const item=el('div','task-item');
  const symbol=el('span','task-symbol',task.tool?.slice(0,1).toUpperCase());
  const main=el('div','task-item-main');
  const title=el('strong','');
  const sub=el('small','');
  main.append(title, sub);
  const state=el('span','task-state');
  item.append(symbol, main, state);
  item.__refs={symbol,title,sub,state};
  bindTaskItem(item, task);
  return item;
}
function bindTaskItem(item, task){
  // #2 抽屉（单击）/ #7 聚焦（双击）/ #9 Pin（右键）。
  // 单击延迟 260ms 开抽屉；若紧接着第二击（双击）到来，先取消抽屉，否则
  // scrim 盖在卡片上，dblclick 永远派发不到目标。
  item.onclick=e=>{if(e.detail!==1)return; clearTimeout(drawerTimer); drawerTimer=setTimeout(()=>openTaskDrawer(task),260)};
  item.ondblclick=e=>{e.preventDefault(); clearTimeout(drawerTimer); closeTaskDrawer(); openFocus(task)};
  item.oncontextmenu=e=>{e.preventDefault();
    const wasPinned=pinnedIds().includes(task.id);
    togglePin(task.id);
    notice(wasPinned?'已取消置顶':'已置顶，永远排在最前');
    renderOverview();};
}
function updateTaskItem(item, task){
  const {symbol,title,sub,state}=item.__refs;
  const pins=pinnedIds();
  symbol.textContent=task.tool?.slice(0,1).toUpperCase();
  title.textContent=(pins.includes(task.id)?'📌 ':'')+(task.project||'未命名项目');
  sub.textContent=`会话 #${task.id} · 已运行 ${fmtElapsed(task.elapsed)} · ${task.age} 秒前更新`;
  state.className=`task-state ${OV_STATE_CLS[task.state]||'run'}`;
  state.textContent=stateNames[task.state]||task.state;
  bindTaskItem(item, task);
}

function renderOverview(){
  const t=live.telemetry||{};
  renderDone();
  // 已完成的不进主列表：它归「刚完成」条。主列表只放还要你做点什么的会话，
  // 列表长度因此不再随「刚跑完一个」而波动。
  const tasks=sortTasks((live.tasks||[]).filter(x=>x.state!=='DONE'));
  const connected=isConnected();
  $('ov-device-state').textContent=connected?'已连接':'未连接';
  $('ov-device-state').className='ov-device-state'+(connected?' ok':'');
  $('ov-device-sub').textContent=connected?live.device.replace('connected:','串口 '):'等待 ESP32-S3 小屏通过 USB 接入。';
  $('ov-version').textContent=`v${saved?.version||'—'}`;
  $('ov-applied').textContent=live.applied_version?`设备已应用 v${live.applied_version}`:'设备尚未应用配置';
  $('ov-task-count').textContent=`${tasks.length} 个进行中`;
  // 优化#8：按来源统计生成筛选条；alert=只看 出错/等你
  const sourceCount={};
  tasks.forEach(task=>{const k=task.tool||'其他';sourceCount[k]=(sourceCount[k]||0)+1});
  renderFilters(sourceCount);
  const ALERT_STATES=['ERROR','WAIT_INPUT','ASK'];
  let shown=tasks;
  if(ovFilter==='alert')shown=tasks.filter(x=>ALERT_STATES.includes(x.state));
  else if(ovFilter!=='all')shown=tasks.filter(x=>(x.tool||'其他')===ovFilter);
  const root=$('ov-task-list');
  if(!tasks.length){
    root.replaceChildren(el('div','empty',(live.done||[]).length
      ? '当前没有进行中的会话；刚完成的在上一行的「刚完成」里。'
      : '暂无活跃会话。连接 Claude Code、Codex、ZCode 或 WorkBuddy 后将在这里显示。'));
    return;
  }
  if(!shown.length){
    root.replaceChildren(el('div','empty','当前筛选下没有会话。'));
    return;
  }
  // 优化#1：按来源（claude/codex/zcode/workbuddy）分窗格的多窗格监控，
  // 一眼分清每个 agent 在干什么，不用在混合列表里找。
  // 复用持久 grid + keyed reconcile，避免每轮 replaceChildren 整屏闪烁。
  let grid=root.querySelector('.agent-grid');
  if(!grid){ root.replaceChildren(); grid=el('div','agent-grid'); root.append(grid); }
  const groups=new Map();
  shown.forEach(task=>{const k=task.tool||'其他';if(!groups.has(k))groups.set(k,[]);groups.get(k).push(task)});
  reconcile(grid, [...groups.keys()],
    tool=>'pane:'+tool,
    tool=>{ const pane=el('div','agent-pane');
            const head=el('div','agent-pane-head');
            head.append(el('strong','',tool),el('span','agent-pane-count',''));
            const items=el('div','agent-pane-items');
            pane.append(head, items);
            pane.__head=head; pane.__items=items;
            return pane; },
    (pane,tool)=>{
      const list=groups.get(tool)||[];
      pane.__head.querySelector('.agent-pane-count').textContent=`${list.length} 项`;
      reconcile(pane.__items, list,
        task=>'t:'+task.id,
        task=>buildTaskItem(task),
        (item,task)=>updateTaskItem(item, task));
    });
  $('ov-cpu').textContent=t.cpu===undefined?'—':`${t.cpu}%`;
  $('ov-mem').textContent=t.mem===undefined?'—':`${t.mem}%`;
  $('ov-mem-sub').textContent=t.memtotal?`${t.memused} / ${t.memtotal} GB`:'数据不可用';
  $('ov-disk').textContent=t.disk===undefined?'—':`${t.disk}%`;
  $('ov-disk-sub').textContent=t.diskfree?`可用 ${t.diskfree} GB`:'—';
  $('ov-net').textContent=t.netrx===undefined?'—':`${t.netrx} / ${t.nettx}`;
  $('ov-net-sub').textContent='Mbps ↓ / ↑';
  $('ov-usage').textContent=t.ait===undefined?'—':`${String(t.ait).split(',')[0]}k token`;
  // 遥测里天气是 wx="temp,code,hi,lo"（见 protocol.py 的 _ORDER），没有
  // wx_temp 字段——旧代码读 wx_temp，概览天气永远是「—」。
  const wxParts=typeof t.wx==='string'?t.wx.split(','):[];
  $('ov-weather').textContent=wxParts.length>=4&&isFinite(parseFloat(wxParts[0]))?`${wxParts[0]} ℃`:'—';
  $('ov-remind').textContent=t.remind||'—';
  $('ov-fresh-pill').textContent=typeof live.telemetry_age==='number'?`数据延迟 ${Math.round(live.telemetry_age)} 秒`:'—';
  if(live.paused)$('ov-fresh-pill').textContent='已暂停采集';
  renderTools();
  drawUsageChart();
  renderSyncState();
  renderConnState();
}
/* 概览「AI 工具」：进程识别的结果。
   日志推断有天然盲区——工具关掉后日志不再变化，最后状态会被新鲜度窗口
   兜住（Codex 的「等你回复」可挂 30 分钟）。进程表能直接回答「它还开着吗」，
   未运行的工具有会话也一律被忽略（见 aiwatch._gate_inactive）。 */
function renderTools(){
  const root=$('ov-tool-chips'); if(!root) return;
  const note=$('ov-tools-note');
  const info=live.tools||{};
  if(info.ok===false){
    note.textContent='进程扫描不可用';
    root.replaceChildren(el('div','empty',
      `无法枚举进程：${info.error||'未知原因'}；当前仍按日志推断显示会话。`));
    return;
  }
  const tools=info.tools||[];
  if(!tools.length){
    note.textContent='未检测到';
    root.replaceChildren(el('div','empty','没有检测到 AI 工具进程。'));
    return;
  }
  note.textContent=`${tools.filter(t=>t.running).length}/${tools.length} 在运行`;
  // keyed reconcile：工具进程每轮只在 running 状态变化时改样式，不重建整片 chips。
  reconcile(root, tools,
    t=>'chip:'+(t.label||t.name||''),
    t=>{ const chip=el('span','tool-chip');
         const dot=el('i','tool-dot');
         const lab=el('span','',t.label);
         const sub=el('small','');
         chip.append(dot, lab, sub);
         chip.__label=lab; chip.__sub=sub;
         return chip; },
    (chip,t)=>{
      chip.className='tool-chip'+(t.running?' on':'');
      chip.__label.textContent=t.label;
      chip.__sub.textContent=t.running?'运行中':(t.gates?'未运行 · 会话已忽略':'未运行');
    });
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

/* ================= 概览 · 刚完成 =================
   完成提示不靠主列表重排：重排只在正好看到的那一刻可见，还会打乱空间记忆。
   改成固定位置的一条带子（顺序＝时间），配一个「未读」计数解决"当时没看"。
   数据来自 aiwatch 的完成记录（服务端保留 30 分钟、最多 8 条）。 */
const DONE_SEEN_KEY='workbuddy-done-seen';
function doneSeenAt(){
  const raw=Number(localStorage.getItem(DONE_SEEN_KEY));
  return isFinite(raw)&&raw>0 ? raw : 0;
}
function renderDone(){
  const rail=$('ov-done-rail'); if(!rail) return;
  const items=live.done||[];
  if(!items.length){ rail.hidden=true; return }
  rail.hidden=false;
  const seen=doneSeenAt();
  const unread=items.filter(d=>(d.at||0)>seen).length;
  const pill=$('ov-done-count');
  if(pill){
    pill.textContent = unread ? `${unread} 条未读` : `共 ${items.length} 条`;
    pill.className = unread ? 'count-pill' : 'count-pill muted';
  }
  const root=$('ov-done-items'); if(!root) return;
  // keyed reconcile：新完成的会话滑入时复用既有节点，只更新文本/未读类，不整片重建。
  reconcile(root, items.slice(0,4),
    d=>'done:'+(d.id||d.at||''),
    d=>{ const row=el('div','done-item');
         const b=el('b',''); const sp=el('span',''); const sm=el('small','');
         row.append(b, sp, sm);
         row.__b=b; row.__sp=sp; row.__sm=sm;
         return row; },
    (row,d)=>{
      const unreadFlag=(d.at||0)>seen;
      row.className='done-item'+(unreadFlag?' unread':'');
      row.__b.textContent=d.project||'未命名项目';
      row.__sp.textContent=d.tool||'';
      row.__sm.textContent=`完成 ${fmtElapsed(d.age)} 前`;
      row.title=`${d.tool||''} · ${d.project||'未命名项目'} · #${d.id||''}`;
    });
  const clear=$('btn-done-clear');
  if(clear) clear.disabled=!unread;
}

/* ================= 音乐（默认软件 + 正在播放） =================
   后端见 workbuddy/music.py：媒体命令走 macOS MediaRemote（系统「正在播放」中枢），
   所以播放/切歌作用于真正在放歌的软件；旧的 AppleScript 逐个试法只有
   Music/Spotify 有字典，必然落到 Apple 音乐。 */
let musicInfo = null;
async function loadMusicApps(force){
  if(musicInfo && !force) return musicInfo;
  try{ musicInfo = await request('/api/music-apps'); }
  catch(e){ musicInfo = null }
  return musicInfo;
}
async function renderMusic(){
  const sel=$('music-app-select'); if(!sel) return;
  const info=await loadMusicApps(true);
  const note=$('music-note');
  if(!info){
    sel.replaceChildren(el('option','','（读取失败）'));
    if(note) note.textContent='无法读取本机音乐软件列表。';
    return;
  }
  sel.replaceChildren();
  const auto=el('option','','自动（优先网易云 → QQ音乐 → Apple 音乐）');
  auto.value='auto'; sel.append(auto);
  (info.apps||[]).forEach(app=>{
    const option=el('option','',app.name); option.value=app.id; sel.append(option);
  });
  // 配置值可能指向一个已卸载/未发现的软件：补一个条目，避免下拉框显示成空
  const configured=info.configured||'auto';
  if(configured!=='auto' && !(info.apps||[]).some(a=>a.id===configured)){
    const option=el('option','',`${configured}（未检测到）`); option.value=configured;
    sel.append(option);
  }
  sel.value=configured;
  if(note){
    if(!info.supported){
      note.textContent='当前系统不支持统一读取播放信息（仅 macOS 支持）。';
    }else if(!(info.apps||[]).length){
      note.textContent='没有在本机发现音乐软件；可在下方「快捷应用」里直接填启动命令。';
    }else{
      const cur=info.default||{};
      note.textContent=`本机找到 ${info.apps.length} 个音乐软件，当前默认「${cur.name||'—'}」` +
        `（${info.running?'正在运行':'未运行'}）。`;
    }
  }
}
function renderMusicNow(){
  const pill=$('music-state'), now=$('music-now');
  if(!pill||!now) return;
  const info=live.music||{};
  const data=info.now||{};
  if(info.supported===false){ pill.textContent='不支持'; now.textContent='当前系统读不到播放信息。'; return }
  if(!data.title){ pill.textContent='无播放'; now.textContent='系统里没有正在播放的软件。'; return }
  pill.textContent=data.playing?'播放中':'已暂停';
  const meta=[data.artist,data.album].filter(Boolean).join(' · ');
  const pos=data.position!==undefined&&data.duration
    ? `　${fmtElapsed(data.position)} / ${fmtElapsed(data.duration)}` : '';
  now.textContent=`${data.title}${meta?` — ${meta}`:''}${pos}`;
}

/* ================= 设置页内部标签 =================
   页面编排 / 主题与显示 / 音乐 / 系统——每个功能域一个页签，
   新增设置项=新增一个页签，不再堆成一根长滚动页。 */
const SETTINGS_TABS = {
  pages:  ['stab-pages', 'spane-pages'],
  look:   ['stab-look',  'spane-look'],
  music:  ['stab-music', 'spane-music'],
  weather:['stab-weather','spane-weather'],
  pet:   ['stab-pet',   'spane-pet'],
  system: ['stab-system','spane-system'],
};
let settingsTab = 'pages';
function switchSettingsTab(which){
  if(!SETTINGS_TABS[which]) which='pages';
  settingsTab=which;
  try{ localStorage.setItem('workbuddy-settings-tab',which); }catch(e){}
  Object.entries(SETTINGS_TABS).forEach(([key,[tabId,paneId]])=>{
    const on = key===which;
    const tab=$(tabId), pane=$(paneId);
    if(tab){ tab.classList.toggle('active',on); tab.setAttribute('aria-selected',on?'true':'false'); }
    if(pane) pane.hidden=!on;
  });
}
/* 带落点的设置入口（任务视图「详细编排」→页面、主题选项→主题与显示…） */
function openSettings(tab){
  switchView('settings');
  if(tab) switchSettingsTab(tab);
}

/* ================= 视图路由 ================= */
function renderExtras(){
  ['reminders','calendar','alarms','countdowns','apps'].forEach(key=>{if(draft&&draft[key])renderEntries(key)});
  // 城市输入框要回填当前值：已配置「天津」时打开本视图若显示空框，
  // 用户会以为之前没配过，随手一改就覆盖了旧值。焦点在框上时不动它。
  const city=$('weather-city');
  if(city&&document.activeElement!==city) city.value=(draft.weather&&draft.weather.city)||'';
}
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
  if(name==='history'){renderHistory();drawTrendChart()}
  window.scrollTo({top:0,behavior:'smooth'});
}
function renderTaskView(){
  if($('autopage-toggle')) $('autopage-toggle').checked=!!Number(draft.autopage||0);
  renderPageSelect(); renderTaskPages(); renderTaskPreview(); renderThemeGrid();
  if(!$('pane-multi').hidden)renderMultiPane();
}

/* ================= 轮询与保存 ================= */
function render(){
  renderOverview();
  if(currentView==='settings')renderMusicNow();
  if(currentView==='tasks')renderTaskView();if(currentView==='saver')renderSaverView();if(currentView==='extras')renderExtras()}
// 趋势视图：每 60s 重画（新桶会推进曲线）；范围切换立即重画
setInterval(()=>{if(currentView==='history')drawTrendChart()},60000);
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
  if(draft.autopage) regenAutoPages();
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
  if(!LAYOUT_PRESETS[tv.layout]) tv.layout='focus';
  const markLayoutButtons=()=>{
    document.querySelectorAll('.layout-btn').forEach(b=>{
      const on=b.dataset.layout===tv.layout;
      b.classList.toggle('active',on);
      b.setAttribute('aria-pressed',on?'true':'false');
    });
    if($('layout-hint')&&LAYOUT_PRESETS[tv.layout])
      $('layout-hint').textContent=LAYOUT_PRESETS[tv.layout].desc;
  };
  $('page-select').onchange=e=>{
    previewPage=Math.max(0,Number(e.target.value)||0);
    renderTaskPreview();renderTaskPages();
  };
  document.querySelectorAll('.layout-btn').forEach(b=>b.onclick=()=>{
    const key=b.dataset.layout, preset=LAYOUT_PRESETS[key];
    if(!preset)return;
    draft.pages=structuredClone(preset.pages);
    selected=0;previewPage=0;tv.layout=key;saveTV();markDirty();
    markLayoutButtons();renderPageSelect();renderTaskPages();renderTaskPreview();
    notice(`已套用「${schemeNames[key]}」页面组合，保存并同步后上屏`);
  });
  $('task-page-title').onchange=e=>{
    const page=(draft.pages||[])[previewPage];if(!page)return;
    page.title=e.target.value.trim().slice(0,24)||page.title;
    e.target.value=page.title;markDirty();
    renderPageSelect();renderTaskPages();renderTaskPreview();
  };
  $('btn-reset-layout').onclick=()=>{
    draft.pages=structuredClone(defaultPages());
    selected=0;previewPage=0;markDirty();
    renderPageSelect();renderTaskPages();renderTaskPreview();
    notice('已恢复默认页面组合，保存并同步后生效');
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
  $('btn-theme-options').onclick=()=>openSettings('look');
  $('btn-apply-theme').onclick=pending($('btn-apply-theme'),applySelectedTheme);
  $('btn-source-jump').onclick=()=>switchView('overview');
  $('btn-deep-edit').onclick=()=>openSettings('pages');
  // 实拍：请求后轮询 meta（snap_at 翻新即完成），最长等 25s
  $('btn-snap').onclick=pending($('btn-snap'),async()=>{
    try{
      await request('/api/snap-request',{});
      notice('已请求实拍，设备回传约需 10 秒…');
      const start=Date.now(), baseline=Date.now()/1000;
      while(Date.now()-start<25000){
        await new Promise(ok=>setTimeout(ok,2000));
        const meta=await request('/api/snapshot-meta');
        if(meta.at && meta.at>baseline){
          $('snapshot-img').src=`/api/snapshot?ts=${meta.at}`;
          $('snapshot-img').hidden=false;
          notice('实拍完成');
          return;
        }
      }
      notice('实拍超时：设备无响应或固件过旧', true);
    }catch(e){notice(e.message, true)}
  });
  $('btn-apply-task').onclick=pending($('btn-apply-task'),async()=>{
    try{
      // 页面组合已经是 draft.pages（预览画的就是它），这里只负责落盘 + 下发。
      await save(true);
      await request('/api/sync',{});
      logHist('layout', `页面组合已应用到设备（${(draft.pages||[]).length} 页）`);
      notice(`已应用到设备并同步（${(draft.pages||[]).length} 页）`);
      poll();
    }catch(e){notice(e.message,true)}
  });
  markLayoutButtons();

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

  // ---- 屏保增强：相册轮播 / 诗句库 ----
  $('album-file').onchange=async e=>{
    const files=[...e.target.files||[]];
    e.target.value='';
    if(!files.length) return;
    if(!isConnected()){notice('设备未连接，先等设备上线',true);return}
    let slot=Number(draft.svalbum||0);
    let okN=0;
    notice(`开始上传 ${files.length} 张…`);
    for(const file of files){
      if(slot>=8){notice('相册最多 8 张',true);break}
      slot+=1;
      try{
        const img=await new Promise((ok2,bad)=>{
          const i=new Image();
          i.onload=()=>ok2(i);
          i.onerror=()=>bad(new Error('图片解码失败'));
          i.src=URL.createObjectURL(file);
        });
        const canvas=document.createElement('canvas');
        canvas.width=320; canvas.height=480;
        const c2=canvas.getContext('2d');
        const scale=Math.max(320/img.width,480/img.height);
        const w=img.width*scale,h=img.height*scale;
        c2.drawImage(img,(320-w)/2,(480-h)/2,w,h);
        const blob=await new Promise(ok2=>canvas.toBlob(ok2,'image/jpeg',0.72));
        if(!blob || blob.size>120*1024){notice('图片压缩后仍超 120KB，换一张试试',true);continue}
        const r=await fetch(`/api/bgimg?target=sv${slot}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
        const d2=await r.json();
        if(!r.ok) throw new Error(d2.error||'上传失败');
        okN+=1;
        draft.svalbum=slot;
      }catch(err){notice(`上传失败：${err.message}`,true)}
    }
    if(okN){
      markDirty(); renderSaverView();
      notice(`已上传 ${okN} 张到相册，样式选「相册轮播」并同步后生效`);
    }
  };
  $('album-clear').onclick=async()=>{
    if(!isConnected()){notice('设备未连接，先等设备上线',true);return}
    for(let i=1;i<=8;i++){
      try{ await fetch(`/api/bgimg-delete?target=sv${i}`,{method:'POST'}); }catch(err){}
    }
    draft.svalbum=0; markDirty(); renderSaverView();
    notice('相册已清空');
  };
  $('poem-add').onclick=()=>{
    const v=$('poem-new').value.trim();
    if(!v) return;
    draft.poems=draft.poems||[];
    if(draft.poems.length>=10){notice('诗句库最多 10 条',true);return}
    draft.poems.push(v);
    $('poem-new').value='';
    markDirty(); renderPoemList();
    notice('已加入诗句库，保存并同步后小屏轮换显示');
  };
  $('poem-int').onchange=e=>{draft.theme.pint=Number(e.target.value);markDirty()};
  $('sv-int').onchange=e=>{draft.theme.svint=Number(e.target.value);markDirty()};
  $('sv-fx').onchange=e=>{draft.theme.svfx=Number(e.target.value);markDirty()};

  // ---- 屏保增强：相册轮播 / 诗句库 ----
  $('album-file').onchange=async e=>{
    const files=[...e.target.files||[]];
    e.target.value='';
    if(!files.length) return;
    if(!isConnected()){notice('设备未连接，先等设备上线',true);return}
    let slot=Number(draft.svalbum||0);
    let okN=0;
    notice(`开始上传 ${files.length} 张…`);
    for(const file of files){
      if(slot>=8){notice('相册最多 8 张',true);break}
      slot+=1;
      try{
        const img=await new Promise((ok2,bad)=>{
          const i=new Image();
          i.onload=()=>ok2(i);
          i.onerror=()=>bad(new Error('图片解码失败'));
          i.src=URL.createObjectURL(file);
        });
        const canvas=document.createElement('canvas');
        canvas.width=320; canvas.height=480;
        const c2=canvas.getContext('2d');
        const scale=Math.max(320/img.width,480/img.height);
        const w=img.width*scale,h=img.height*scale;
        c2.drawImage(img,(320-w)/2,(480-h)/2,w,h);
        const blob=await new Promise(ok2=>canvas.toBlob(ok2,'image/jpeg',0.72));
        if(!blob || blob.size>120*1024){notice('图片压缩后仍超 120KB，换一张试试',true);continue}
        const r=await fetch(`/api/bgimg?target=sv${slot}`,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:await blob.arrayBuffer()});
        const d2=await r.json();
        if(!r.ok) throw new Error(d2.error||'上传失败');
        okN+=1;
        draft.svalbum=slot;
      }catch(err){notice(`上传失败：${err.message}`,true)}
    }
    if(okN){
      markDirty(); renderSaverView();
      notice(`已上传 ${okN} 张到相册，样式选「相册轮播」并同步后生效`);
    }
  };
  $('album-clear').onclick=async()=>{
    if(!isConnected()){notice('设备未连接，先等设备上线',true);return}
    for(let i=1;i<=8;i++){
      try{ await fetch(`/api/bgimg-delete?target=sv${i}`,{method:'POST'}); }catch(err){}
    }
    draft.svalbum=0; markDirty(); renderSaverView();
    notice('相册已清空');
  };
  $('poem-add').onclick=()=>{
    const v=$('poem-new').value.trim();
    if(!v) return;
    draft.poems=draft.poems||[];
    if(draft.poems.length>=10){notice('诗句库最多 10 条',true);return}
    draft.poems.push(v);
    $('poem-new').value='';
    markDirty(); renderPoemList();
    notice('已加入诗句库，保存并同步后小屏轮换显示');
  };
  $('poem-int').onchange=e=>{draft.theme.pint=Number(e.target.value);markDirty()};
  $('sv-int').onchange=e=>{draft.theme.svint=Number(e.target.value);markDirty()};
  $('sv-fx').onchange=e=>{draft.theme.svfx=Number(e.target.value);markDirty()};
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
    // 预设自带 card_opa（colors 里就带），不再沿用用户当前的透明度——
    // 否则同一套预设「在 100% 时能存、在 70% 时报边界看不见」。
    b.onclick=()=>{Object.assign(draft.theme,colors,{bright:name==='实机校准·深蓝灰'?62:draft.theme.bright});
      markDirty();updateQuantNote();renderInspector();
      notice(`已应用「${name}」主题，保存并同步后生效`)};
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
        markDirty();renderInspector();await save(true);await request('/api/sync',{});
        logHist('theme',`画廊主题「${th.name}」已应用`);
        notice(`「${th.name}」主题已应用：背景+配色+卡片透明度`);
        poll();
      }catch(err){notice(err.message,true)}
    };
    gal.append(cell);
  });
  for(const key of ['bg','grad','card','card_edge','accent','text','sub'])$('theme-'+key).oninput=e=>{
    draft.theme[key]=e.target.value.slice(1).toUpperCase();markDirty();updateQuantNote();
  };
  $('theme-bright').oninput=e=>{draft.theme.bright=Number(e.target.value);markDirty()};
  $('card-opa').oninput=e=>{draft.theme.card_opa=Number(e.target.value);$('opa-value').textContent=e.target.value+'%';markDirty()};
  $('bright-auto').onchange=e=>{draft.theme.bright_auto=e.target.checked?1:0;markDirty();notice(e.target.checked?'已开启自动亮度（白天亮/夜间暗）':'已切回手动亮度')};
  $('btn-save-mytheme').onclick=()=>{
    const colors={bg:draft.theme.bg,grad:draft.theme.grad,card:draft.theme.card,
      card_edge:draft.theme.card_edge,accent:draft.theme.accent,text:draft.theme.text,sub:draft.theme.sub};
    const issues=themeIssues(colors, draft.theme.card_opa);
    if(issues.length){
      notice('这套配色上屏后不达标，未保存：'+issues[0]+'（可调亮文字/卡片或提高卡片不透明度）', true);
      return;
    }
    const list=myThemes();
    const name='我的主题 '+(list.length+1);
    list.push({name, colors, opa:draft.theme.card_opa});
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

  // ---- 设置 · 天气 ----
  $('weather-search-btn').onclick=pending($('weather-search-btn'),async()=>{
    const q=$('weather-q').value.trim();
    if(!q){notice('先输入城市名',true);return}
    try{
      const result=await request('/api/weather-search',{q});
      renderWeatherSearch(result.results||[], q);
    }catch(e){notice(e.message,true)}
  });
  $('weather-q').onkeydown=e=>{ if(e.key==='Enter') $('weather-search-btn').click(); };
  $('weather-gen-pages').onclick=()=>{
    regenAllPages(); renderPageSelect(); renderTaskPages(); renderTaskPreview();
    notice('已按城市生成天气页面，保存并同步后上屏');
  };

  // ---- 设置 · 宠物 ----
  $('pet-save').onclick=pending($('pet-save'),async()=>{
    try{
      const r=await request('/api/pet-config',{type:petCache?.type||'robot',
        name:$('pet-name').value.trim()||'帽子',
        sleep:$('pet-sleep')?.checked?1:0, greet:$('pet-greet')?.checked?1:0});
      draft.pet={...(draft.pet||{}), type:petCache?.type||'robot',
        name:$('pet-name').value.trim()||'帽子',
        sleep:$('pet-sleep')?.checked?1:0, greet:$('pet-greet')?.checked?1:0};
      notice(`宠物形象已保存（v${r.version}），同步后设备换新装`);
      poll();
    }catch(e){notice(e.message,true)}
  });
  $('pet-page-gen').onclick=()=>{
    draft.pages=(draft.pages||[]).filter(p=>p.id!=='pet');
    draft.pages.splice(1,0,{id:'pet',title:$('pet-name').value.trim()||'宠物',
      layout:'list',widgets:['pet']});
    markDirty(); renderPages(); renderPageSelect(); renderTaskPages(); renderTaskPreview();
    notice('已生成「宠物」小屏页面，保存并同步后上屏');
  };
  [['pet-feed','feed'],['pet-water','water'],['pet-play','play']].forEach(([id,action])=>{
    $(id).onclick=pending($(id),async()=>{
      try{
        petCache=await request('/api/pet-do',{action});
        renderPetStats();
        notice({feed:'喂饱了！',water:'喝上水了！',play:'运动了一下，活力下降了'}[action]);
      }catch(e){notice(e.message,true)}
    });
  });

  // ---- 设置页内部标签 ----
  let savedTab=null;
  try{ savedTab=localStorage.getItem('workbuddy-settings-tab'); }catch(e){}
  if(savedTab) settingsTab=savedTab;
  Object.keys(SETTINGS_TABS).forEach(key=>{ $(SETTINGS_TABS[key][0]).onclick=()=>switchSettingsTab(key); });

  // ---- 自动按工具分页 ----
  // bind() 在首轮 /api/status 之前执行，draft 此时还是 null；
  // 回填在 renderTaskView 里每次都会做，这里只绑事件，绝不碰 draft。
  $('autopage-toggle').onchange=e=>{
    if(!draft)return;
    draft.autopage=e.target.checked?1:0; markDirty();
    if(draft.autopage){
      regenAutoPages(); renderPageSelect(); renderTaskPages(); renderTaskPreview();
      notice('已按当前会话的工具重排 AI 页，保存并同步后上屏');
    }
  };

  // ---- 音乐 ----
  const addMusicApp=async()=>{
    const apps=draft.apps||[];
    if(apps.length>=12){notice('快捷应用最多 12 个',true);return}
    if(apps.some(a=>a.command===MUSIC_COMMAND)){notice('已经有一个音乐入口了');return}
    apps.push({id:uniqueId('app'),name:'音乐',command:MUSIC_COMMAND});
    markDirty();renderExtras();renderMusic();
    notice('已添加「音乐」快捷入口，保存并同步后触屏可用');
  };
  $('music-add-app').onclick=addMusicApp;
  $('music-add-app2').onclick=addMusicApp;
  $('music-app-select').onchange=async e=>{
    try{
      const result=await request('/api/music-app',{id:e.target.value});
      musicInfo=null;
      await renderMusic();
      notice(`默认音乐软件已设为「${(result.default||{}).name||e.target.value}」`);
    }catch(err){notice(err.message,true)}
  };
  $('music-test-btn').onclick=pending($('music-test-btn'),async()=>{
    try{
      const result=await request('/api/music-play',{});
      notice(result.ok
        ? `${result.app}${result.launched?' 已启动并开始播放':' 已下发播放'}`
        : `没能播放：${result.reason||'系统里没有正在播放的软件'}`);
      poll();
    }catch(err){notice(err.message,true)}
  });

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
  // 「系统趋势」默认收起：收起时 canvas 的 clientWidth 为 0，画不出来，
  // 所以展开时补画一次（switchView 里那次调用在收起状态下不生效）。
  const trendDetails=$('trend-details');
  if(trendDetails) trendDetails.addEventListener('toggle',()=>{
    if(trendDetails.open) drawTrendChart();
  });
  $('btn-history-clear').onclick=()=>{localStorage.removeItem(HIST_KEY);renderHistory();notice('历史已清空')};
  const doneClear=$('btn-done-clear');
  if(doneClear) doneClear.onclick=()=>{
    localStorage.setItem(DONE_SEEN_KEY,String(Date.now()/1000));
    renderDone();
    notice('完成记录已标记为已读（记录仍保留 30 分钟）');
  };
  $('trend-range').onchange=()=>drawTrendChart();

  // ---- 其他视图与设置各页签的保存/同步入口 ----
  // 保存按钮原来只在「概览」和「页面编排」有：用户在其他视图/主题/音乐/天气
  // 里改完，本视图内没有任何可保存的地方（用户反馈「有的地方没有保存按钮」）。
  const bindSavePair=(saveId,syncId)=>{
    if(!$(saveId)||!$(syncId))return;
    $(saveId).onclick=pending($(saveId),async()=>{try{await save()}catch(e){notice(e.message,true)}});
    $(syncId).onclick=pending($(syncId),async()=>{try{if(dirty)await save();await request('/api/sync',{});logHist('sync','已请求同步到设备');notice('已请求同步，等待设备应用回执');poll()}catch(e){notice(e.message,true)}});
  };
  bindSavePair('extras-save-btn','extras-sync-btn');
  bindSavePair('look-save-btn','look-sync-btn');
  bindSavePair('music-save-btn','music-sync-btn');
  bindSavePair('weather-save-btn','weather-sync-btn');

  // ---- 设置：保存/同步/维护 ----
  $('validate-btn').onclick=pending($('validate-btn'),async()=>{try{await request('/api/validate',draft);notice('配置检查通过，可以保存')}catch(e){notice(e.message,true)}});
  $('save-btn').onclick=pending($('save-btn'),async()=>{try{await save()}catch(e){notice(e.message,true)}});
  $('sync-btn').onclick=pending($('sync-btn'),async()=>{try{if(dirty)await save();await request('/api/sync',{});logHist('sync','已请求同步到设备');notice('已请求同步，等待设备应用回执');poll()}catch(e){notice(e.message,true)}});
  $('reset-btn').onclick=()=>{
    if(!confirm('恢复默认档案？当前未保存修改会丢失。'))return;
    draft={schema:1,version:saved.version,pages:defaultPages(),
           reminders:[],calendar:[],alarms:[],apps:[],countdowns:[],
           theme:structuredClone(DEFAULT_THEME)};
    selected=0;previewPage=0;markDirty();
  };
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
  const smcState=$('smc-state');
  const smcRefresh=async()=>{
    try{const s=await(await fetch('/api/smc-status')).json();
      if(s.installed&&s.fresh&&s.data){
        const parts=[];
        if(s.data.cputemp!=null)parts.push('CPU '+s.data.cputemp+'℃');
        if(s.data.gputemp!=null)parts.push('GPU '+s.data.gputemp+'℃');
        if(s.data.fan!=null)parts.push(s.data.fan+' RPM');
        smcState.textContent='运行中 · '+(parts.join(' / ')||'采样中')+'（'+s.age+'s 前）';
      }else if(s.installed){smcState.textContent='已安装但暂无数据';}
      else{smcState.textContent='未启用（小屏隐藏该项）';}
    }catch(e){smcState.textContent='状态未知';}
  };
  smcRefresh(); setInterval(smcRefresh,10000);
  $('btn-smc-install').onclick=pending($('btn-smc-install'),async()=>{
    notice('请在弹出的系统密码框输入管理员密码…');
    try{
      const r=await request('/api/smc-install',{});
      if(r.ok){notice('温度采样已启用：CPU '+(r.data.cputemp??'?')+'℃ / 风扇 '+(r.data.fan??'?')+' RPM');
        logHist('smc','温度采样已启用');}
      else{notice(r.err||'启用失败',true);logHist('smc','温度采样启用失败',false);}
      smcRefresh();
    }catch(err){notice('启用失败：'+err.message,true)}
  });
  $('btn-smc-uninstall').onclick=pending($('btn-smc-uninstall'),async()=>{
    try{
      const r=await request('/api/smc-uninstall',{});
      notice(r.ok?'已停用温度采样':'停用未完成（组件可能已部分移除）',!r.ok);
      if(r.ok)logHist('smc','温度采样已停用');
      smcRefresh();
    }catch(err){notice('停用失败：'+err.message,true)}
  });
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
/* 多窗格：把配置里的页面逐页平铺。用的是同一个 devScreen()，
   所以每块小窗都是该页在小屏上的真实样子，不是另画一版缩略图。 */
function renderMultiPane(){
  const root=$('multi-grid');if(!root)return;
  root.replaceChildren();
  (draft.pages||[]).forEach((page,i)=>{
    const cell=el('div','multi-cell');
    const wrap=el('div','screen-scale');
    const bezel=el('div','screen-bezel');
    try{ bezel.append(devScreen(i, draft.theme)) }catch(e){ bezel.append(el('div','empty','渲染失败：'+e.message)) }
    wrap.append(bezel); cell.append(wrap);
    cell.append(el('span','multi-cap',`第 ${i+1} 页 · ${page.title}`));
    cell.onclick=()=>{
      previewPage=i;tv.layout=tv.layout;renderPageSelect();renderTaskPages();
      switchTaskTab('layouts');renderTaskPreview();
    };
    /* 多窗格点击落点保持在本视图，不进设置 */
    root.append(cell);
  });
  requestAnimationFrame(()=>root.querySelectorAll('.screen-scale').forEach(fitStage));
}
async function downloadFile(path, filename){
  const r=await fetch(path);
  if(!r.ok)throw new Error('下载失败');
  const blob=await r.blob();
  const a=document.createElement('a');a.href=URL.createObjectURL(blob);
  a.download=filename;a.click();URL.revokeObjectURL(a.href);
}
bind();
loadColorMap();
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
/* 舞台随侧栏宽度变化重新缩放（预览是缩放渲染，不是响应式重排）。 */
window.addEventListener('resize',()=>{
  fitStage($('screen-scale'));
  fitStage($('sv-scale'));
  document.querySelectorAll('#multi-grid .screen-scale').forEach(fitStage);
});
document.addEventListener('visibilitychange',()=>{
  if(document.hidden){clearTimeout(tickTimer);return}
  failStreak=0;clearTimeout(tickTimer);
  poll().finally(startTicker);
});
window.addEventListener('focus',()=>{if(!document.hidden){failStreak=0;poll()}});
// 预览顶栏时钟每秒走字：只改文本，不重建整块舞台
setInterval(()=>{
  document.querySelectorAll('.screen [data-role="topclock"]').forEach(node=>{
    node.textContent=clockHMS();
  });
},1000);
