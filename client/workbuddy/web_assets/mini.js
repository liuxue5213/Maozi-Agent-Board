/* 迷你悬浮窗数据逻辑（优化#4）。与主工作台共用 /api/status + /api/stream，
   但独立一份精简渲染，不依赖 app.js。 */
const $ = id => document.getElementById(id);
const STATE_NAMES = {THINKING:'思考中',GENERATING:'生成中',TOOL:'执行工具',BUILD:'构建中',
  DONE:'已完成',ERROR:'出错',WAIT_INPUT:'等你回复',ASK:'等你确认'};
const STATE_CLS = {THINKING:'run',GENERATING:'run',TOOL:'run',BUILD:'run',
  DONE:'done',ERROR:'alert',WAIT_INPUT:'wait',ASK:'wait'};
let lastData = null;

function render(d){
  lastData = d;
  const tasks = d.tasks || [];
  const top = tasks[0];
  const cls = top ? (STATE_CLS[top.state] || 'run') : '';
  const stateEl = $('m-state');
  stateEl.textContent = d.paused ? '已暂停采集' : (top ? (STATE_NAMES[top.state] || top.state) : '空闲');
  stateEl.className = 'mini-state ' + cls;
  $('m-project').textContent = top ? (top.project || '未命名项目') : '暂无活跃会话';
  $('m-meta').textContent = top
    ? `${top.tool} · #${top.id} · ${fmt(top.elapsed)}`
    : '连接 AI 编辑器后显示';
  const fill = $('m-bar-fill');
  fill.style.width = (top && top.prog >= 0 ? top.prog : 0) + '%';
  const others = $('m-others');
  others.replaceChildren();
  tasks.slice(1, 4).forEach(t => {
    const li = document.createElement('li');
    const name = document.createElement('span');
    name.textContent = `${t.tool} · ${t.project || '未命名'}`;
    const em = document.createElement('em');
    em.className = STATE_CLS[t.state] || '';
    em.textContent = STATE_NAMES[t.state] || t.state;
    li.append(name, em);
    others.append(li);
  });
  const t = d.telemetry || {};
  $('m-cpu').textContent = t.cpu === undefined ? 'CPU —' : `CPU ${t.cpu}%`;
  $('m-mem').textContent = t.mem === undefined ? 'MEM —' : `MEM ${t.mem}%`;
  $('m-tok').textContent = t.ait === undefined ? '— k tok' : `${String(t.ait).split(',')[0]}k tok`;
}
function fmt(sec){
  sec = Math.max(0, Math.round(sec || 0));
  return `${String(Math.floor(sec / 60)).padStart(2, '0')}:${String(sec % 60).padStart(2, '0')}`;
}
function renderConn(d){
  const on = (d.device || '').startsWith('connected:');
  $('m-dot').classList.toggle('on', on);
  $('m-online').textContent = on ? '小屏在线' : '小屏离线';
}
function tickClock(){
  $('m-clock').textContent = new Date().toLocaleTimeString('zh-CN',
    {hour:'2-digit', minute:'2-digit', hour12:false});
}
setInterval(tickClock, 1000);
tickClock();

async function poll(){
  try{
    const r = await fetch('/api/status');
    if(!r.ok) return;
    const d = await r.json();
    render(d);
    renderConn(d);
  }catch(e){ /* 客户端退出时静默，窗口由壳进程关闭 */ }
}
poll();
setInterval(poll, 2500);
// SSE 快路径：与主工作台一致，收到推送立即刷新，轮询兜底
try{
  const es = new EventSource('/api/stream');
  es.onmessage = e => {
    try{
      const d = JSON.parse(e.data);
      render(d);
      renderConn(d);
    }catch(err){}
  };
}catch(e){}
