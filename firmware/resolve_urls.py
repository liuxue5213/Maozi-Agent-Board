#!/usr/bin/env python3
"""解析 espressif32 平台全部依赖包的 macOS x64 下载地址（带重试）。"""
import urllib.request
import json
import socket
import tarfile
import io
import time

socket.setdefaulttimeout(25)


def get(url, retries=4):
    for i in range(retries):
        try:
            return urllib.request.urlopen(url).read()
        except Exception as e:
            if i == retries - 1:
                raise
            print(f"  retry {i+1} ({type(e).__name__})")
            time.sleep(2 * (i + 1))


def getj(url):
    return json.loads(get(url))


plat = getj("https://api.registry.platformio.org/v3/packages/platformio/platform/espressif32")
f = plat["version"]["files"][0]
print(f"平台包 v{plat['version']['name']}: {f['download_url']}")
data = get(f["download_url"])
pj = json.load(tarfile.open(fileobj=io.BytesIO(data)).extractfile("platform.json"))
pkgs = pj["packages"]
print(f"依赖包: {len(pkgs)} 个\n")

sysname = "macos_amd64"
lines = [f"# espressif32 v{plat['version']['name']} macOS x64 依赖包\n"]
total = 0
ok = 0
for name, spec in pkgs.items():
    ver = spec.get("version") or (spec.get("specs") or "")
    owner = spec.get("owner") or "platformio"
    try:
        info = getj(f"https://api.registry.platformio.org/v3/packages/{owner}/tool/{name}")
    except Exception as e:
        lines.append(f"{name}  v{ver}  [版本查询失败: {type(e).__name__}]")
        print(f"[-] {name} v{ver} 查询失败")
        continue
    versions = info.get("versions", [])
    chosen = None
    want = str(ver)
    for v in versions:  # 注册表按新→旧排序，匹配平台要求的版本段
        if want and want.split("+")[0].rstrip("*") in v["name"]:
            chosen = v
            break
    if not chosen and versions:
        chosen = versions[0]
    if not chosen:
        lines.append(f"{name}  v{ver}  [无可用版本]")
        continue
    hit = None
    for s in chosen.get("systems", []):
        if sysname in str(s.get("targets", "")):
            hit = s
            break
    if not hit:
        lines.append(f"{name}  v{chosen['name']}  [无 macOS x64 包]")
        print(f"[!] {name} 无 macos_amd64 包")
        continue
    total += hit.get("size", 0)
    ok += 1
    lines.append(f"{name}  v{chosen['name']}  {hit.get('size',0)/1048576:.0f}MB")
    lines.append(f"  {hit['download_url']}")
    print(f"[+] {name} v{chosen['name']} {hit.get('size',0)/1048576:.0f}MB")

lines.append(f"\n# 共 {ok} 个包，{total/1048576:.0f} MB")
with open("/Users/liuxue/Desktop/www/maozi-diy/firmware/toolchain_urls.txt", "w") as fp:
    fp.write("\n".join(lines) + "\n")
print(f"\n已保存 firmware/toolchain_urls.txt（{ok}/{len(pkgs)} 解析成功）")
