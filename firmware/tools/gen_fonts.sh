#!/bin/zsh
# 16/24 像素覆盖 GB2312；32/48 像素覆盖界面文案并回退到 24 像素。
set -euo pipefail
cd "$(dirname "$0")/.."

FONT="tools/.font-cache/NotoSansCJKsc-Regular.otf"
if [[ ! -f "$FONT" ]]; then
  mkdir -p "$(dirname "$FONT")"
  curl -fL --retry 3 -o "$FONT" \
    'https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf'
fi

COMMON="$(python3 - <<'PY'
from pathlib import Path
chars = set()
for lead in range(0xB0, 0xF8):
    for trail in range(0xA1, 0xFF):
        try:
            chars.add(bytes((lead, trail)).decode('gb2312'))
        except UnicodeDecodeError:
            pass
for c in Path('src/ui.cpp').read_text():
    if '\u3400' <= c <= '\u9fff' or c in '℃·…°，。：！？（）【】':
        chars.add(c)
print(''.join(sorted(chars)), end='')
PY
)"
UI_ONLY="$(python3 - <<'PY'
from pathlib import Path
chars = {c for c in Path('src/ui.cpp').read_text()
         if '\u3400' <= c <= '\u9fff' or c in '℃·…°，。：！？（）【】'}
print(''.join(sorted(chars)), end='')
PY
)"

mkdir -p src/fonts
for SIZE in 16 24 32 48; do
  if (( SIZE <= 24 )); then
    SYMBOLS="$COMMON"
    FALLBACK=()
  else
    SYMBOLS="$UI_ONLY"
    FALLBACK=(--lv-fallback cn_24)
  fi
  npx --yes lv_font_conv \
    --font "$FONT" --size $SIZE --bpp 4 --format lvgl --no-compress \
    -r 0x20-0x7E --symbols "$SYMBOLS" \
    --lv-font-name cn_${SIZE} "${FALLBACK[@]}" \
    -o src/fonts/cn_${SIZE}.c
  echo "✓ cn_${SIZE}.c  ($(du -h src/fonts/cn_${SIZE}.c | cut -f1))"
done
