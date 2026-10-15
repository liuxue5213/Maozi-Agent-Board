import sys, json, threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

ROOT = Path("/Users/liuxue/Desktop/www/maozi-diy/client/workbuddy/web_assets")
sys.path.insert(0, "/Users/liuxue/Desktop/www/maozi-diy/client")
try:
    from workbuddy.profile import validate_profile
    PROFILE = validate_profile({"pages": [{"title": "状态聚焦", "layout": "list",
                                          "widgets": ["ai_list"]}]})
    print("validate_profile OK, pages:", len(PROFILE.get("pages", [])),
          "theme keys:", list((PROFILE.get("theme") or {}).keys())[:6])
except Exception as e:
    print("validate_profile failed:", e)
    PROFILE = {"pages": [{"id": "ai", "title": "状态聚焦", "layout": "list",
                          "widgets": ["ai_list"]}], "theme": {"bg": "#0e1726"}}

STATUS = {
    "profile": PROFILE, "profile_status": "待同步", "profile_error": "",
    "device": "offline:", "telemetry": {}, "telemetry_age": 1,
    "tasks": [], "usage": None, "pack_progress": {}, "tools": [],
    "work_break": {}, "applied_version": 1, "profile_version": PROFILE.get("version"),
}
CT = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
      ".json": "application/json", ".png": "image/png", ".svg": "image/svg+xml"}

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, ct="application/json"):
        data = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data))); self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        p = urlsplit(self.path).path
        if p == "/api/status":
            return self._send(200, json.dumps(STATUS, ensure_ascii=False, default=str))
        if p.startswith("/api/"):
            return self._send(200, "{}")
        f = (ROOT / p.lstrip("/"))
        if p == "/" or p == "":
            f = ROOT / "index.html"
        if f.exists() and f.is_file():
            return self._send(200, f.read_bytes(), CT.get(f.suffix, "application/octet-stream"))
        return self._send(404, "nf")

srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

from playwright.sync_api import sync_playwright
errors, console = [], []
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    pg = b.new_page()
    pg.on("pageerror", lambda e: errors.append("PAGEERROR: " + e.message))
    pg.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    pg.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="load", timeout=15000)
    pg.wait_for_timeout(3500)  # 等首轮 /api/status 吸收 + 渲染
    res = pg.evaluate("""() => {
        try {
            const pages = [
                {id:'ai', title:'状态聚焦', layout:'list', widgets:['ai_list']},
                {id:'pet', title:'宠物', layout:'grid_1', widgets:['pet']},
                {id:'pc', title:'看板', layout:'grid_2x2', widgets:['hw_cards']},
                {id:'env', title:'环境', layout:'grid_2x2', widgets:['environment','weather']},
                {id:'cost', title:'用量', layout:'grid_2x2', widgets:['ai_cost','clock']},
                {id:'music', title:'音乐', layout:'grid', widgets:['music']},
                {id:'free', title:'自由页', layout:'free',
                    widgets:['ai_list','pet','clock','weather'],
                    cells:[{x:0,y:0,w:160,h:120,kind:'pet'},{x:160,y:0,w:160,h:120,kind:'clock'}]},
            ];
            const st = {profile:{version:1, pages, theme:{bg:'#0e1726',accent:'#22d3ee',
                sub:'#7c8aa5',text:'#e6edf6',card:'#16223a',card_edge:'#22304d',
                topclock:1,topwx:1}}, profile_status:'待同步', device:'offline:',
                telemetry:{cpu:20,mem:40,disk:50,netrx:0,nettx:0}, tasks:[
                    {id:'s1',tool:'codex',project:'x',state:'THINKING',elapsed:30}]};
            absorb(st);
            const out = [];
            for (let i=0;i<pages.length;i++){
                previewPage = i;
                try { renderTaskPreview();
                    out.push({page:pages[i].id, ok:true,
                        children:(document.getElementById('screen')||{}).childElementCount||0});
                } catch(e){ out.push({page:pages[i].id, ok:false, err:e.message}); }
            }
            return {ok:true, perPage:out};
        } catch(e){ return {ok:false, err:e.message,
                    stack:(e.stack||'').split('\\n').slice(0,6)}; }
    }""")
    b.close()
srv.shutdown()

print("=== PAGE ERRORS ===")
print("\n".join(errors) or "(none)")
print("=== CONSOLE (last 12) ===")
print("\n".join(console[-12:]) or "(none)")
print("=== PREVIEW RESULT ===")
print(json.dumps(res, ensure_ascii=False, indent=2))
print("=== profile.pages count ===", len(PROFILE.get("pages", [])))
