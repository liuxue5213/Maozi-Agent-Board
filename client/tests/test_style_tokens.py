"""样式层回归守卫：防止硬编码色值回流，并锁住令牌与 HTML 宿主完整性。

背景：app.css 重构前有约 40 处字面色值（#141e32 / #22d3ee / #0e3a38…），
散落在组件规则里，既无法统一换肤，也让「预览与真机同源」无法成立
（实机配色核验文档 §2：卡片色必须是运行时 profile 下发的值，
而 CSS 里写死的 #141E32 正是「卡片边界看不见」的旧色，对比度仅 1.12:1）。

这里锁三条：
  1. 除令牌定义块外，组件规则里不允许出现 hex / rgb() / hsl() 色值；
  2. 设备层令牌（--dev-*）必须齐备，否则预览会退回硬编码色；
  3. index.html 必须是真 HTML，且 app.js 引用的每个 id 都存在
     （历史事故：index.html 曾是 app.js 的字节副本，页面直接不可用）。
"""
import re
import unittest
from pathlib import Path

ASSETS = Path(__file__).resolve().parent.parent / "workbuddy" / "web_assets"
CSS = (ASSETS / "app.css").read_text(encoding="utf-8")
HTML = (ASSETS / "index.html").read_text(encoding="utf-8")
JS = (ASSETS / "app.js").read_text(encoding="utf-8")

# 这些选择器块是「声明值」的地方，允许出现字面色值。
TOKEN_BLOCKS = (":root", "@media (prefers-color-scheme", "@media (prefers-contrast")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def rule_blocks(css: str):
    """产出 (选择器, 声明块) —— 只处理顶层规则，忽略 @media 内的嵌套。"""
    css = strip_comments(css)
    out = []
    i = 0
    while i < len(css):
        open_brace = css.find("{", i)
        if open_brace == -1:
            break
        selector = css[i:open_brace].strip()
        depth, j = 1, open_brace + 1
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append((selector, css[open_brace + 1:j - 1]))
        i = j
    return out


COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|(?:rgba?|hsla?)\s*\(")


class NoHardcodedColorTests(unittest.TestCase):
    def test_component_rules_have_no_literal_colors(self):
        offenders = []
        for selector, body in rule_blocks(CSS):
            if any(selector.startswith(t) for t in TOKEN_BLOCKS):
                continue
            for decl in body.split(";"):
                name, _, value = decl.partition(":")
                if not COLOR_RE.search(value):
                    continue
                # color-mix() 里引用令牌是允许的，它本身不是字面色值
                if "color-mix" in value:
                    continue
                offenders.append(f"{selector} {{ {name.strip()}: {value.strip()} }}")
        self.assertEqual(
            offenders, [],
            "组件规则里出现字面色值，请改用 var(--token)：\n  " + "\n  ".join(offenders))

    def test_device_tokens_all_defined(self):
        required = ["--dev-bg", "--dev-grad", "--dev-card", "--dev-edge",
                    "--dev-accent", "--dev-text", "--dev-sub",
                    "--dev-card-opa", "--dev-card-ink", "--dev-sub-ink"]
        # 本文件有多个 :root 块（原色层 / 设备层 / 语义层），需合并后再判断，
        # 不能只取最后一个。
        declared = set(re.findall(r"(--dev-[\w-]+)\s*:", CSS))
        missing = [t for t in required if t not in declared]
        self.assertEqual(missing, [], f"缺少设备层令牌：{missing}")
        # 每个令牌都必须真的被消费，否则就是死变量
        for token in required:
            consumers = len(re.findall(rf"var\(\s*{re.escape(token)}\s*[,)]", CSS))
            self.assertGreater(consumers, 0, f"{token} 定义了却没有任何组件引用")

    def test_legacy_low_contrast_card_is_not_hardcoded(self):
        """旧默认卡片色 #141E32 对 bg 仅 1.12:1，组件里不得再写死。"""
        offenders = [s for s, b in rule_blocks(CSS)
                     if not any(s.startswith(t) for t in TOKEN_BLOCKS)
                     and re.search(r"#141e32|#141E32", b)]
        self.assertEqual(offenders, [], f"组件仍在写死低对比卡片色：{offenders}")


class HostPageTests(unittest.TestCase):
    def test_index_html_is_real_html(self):
        """历史事故：index.html 曾经是 app.js 的副本，页面完全不可用。"""
        self.assertTrue(HTML.lstrip().startswith("<!DOCTYPE html>"),
                        "index.html 不是以 DOCTYPE 开头，极可能是 JS 副本")
        self.assertIn("<body", HTML)
        self.assertNotIn("const $ = id =>", HTML,
                         "index.html 里出现了 app.js 的代码，文件被覆盖过")

    def test_every_js_referenced_id_exists(self):
        ids = set(re.findall(r"""\$\(['"]([\w-]+)['"]\)""", JS))
        # 模板拼接出的 id：$('theme-'+key)、$(key+'-list')
        ids |= {"theme-" + k for k in ("bg", "grad", "card", "accent", "text")}
        ids |= {k + "-list" for k in
                ("reminders", "calendar", "alarms", "apps", "countdowns")}
        present = set(re.findall(r'id="([^"]+)"', HTML))
        missing = sorted(ids - present)
        self.assertEqual(missing, [], f"app.js 引用了不存在的 id：{missing}")

    def test_data_add_targets_exist(self):
        keys = set(re.findall(r'data-add="(\w+)"', HTML))
        self.assertTrue(keys, "找不到任何 data-add 按钮")
        for key in keys:
            self.assertIn(key, JS, f"data-add=\"{key}\" 没有对应的 JS 分支")

    def test_app_js_has_no_inline_styles_or_scripts(self):
        """web.py 下发了严格 CSP（script-src 'self'），内联脚本会被直接拦截。"""
        html = strip_comments(HTML)
        self.assertNotRegex(html, r"<script(?![^>]*\bsrc=)",
                            "存在内联 <script>，会被 CSP 拦截")
        self.assertNotRegex(html, r"\son[a-z]+\s*=",
                            "存在内联事件处理器属性，与 CSP 冲突")


class StyleTokenTests(unittest.TestCase):
    def test_layout_scale_is_four_px_based(self):
        for name in ("--space-1", "--space-2", "--space-3", "--space-4"):
            m = re.search(rf"{name}\s*:\s*(\d+)px", CSS)
            self.assertIsNotNone(m, f"缺少间距刻度 {name}")
            self.assertEqual(int(m.group(1)) % 4, 0, f"{name} 不是 4 的倍数")

    def test_both_color_schemes_define_the_same_semantic_tokens(self):
        """深色映射必须覆盖浅色定义的每个语义 token，否则会出现「切深色后
        某组件仍是浅色硬编码」的割裂感。"""
        semantic = set(re.findall(r"--(?:bg|text|line|accent|ok|warn|err|info|btn|mark|toast|device)-[\w-]+\s*:", CSS))
        light = dict(rule_blocks(CSS))[":root"]
        light_names = {n.strip() for n in re.findall(r"(--[\w-]+)\s*:", light)}
        dark_block = re.search(
            r":root\[data-theme=\"dark\"\]\s*\{(.*?)\n\}", strip_comments(CSS), re.S)
        self.assertIsNotNone(dark_block, "缺少 :root[data-theme=dark] 深色映射")
        dark_names = {n.strip() for n in re.findall(r"(--[\w-]+)\s*:", dark_block.group(1))}
        shared = {n for n in semantic if n in light_names}
        missing = sorted(shared - dark_names)
        self.assertEqual(missing, [], f"深色主题缺少语义 token：{missing}")

    def test_focus_visible_is_defined(self):
        self.assertIn(":focus-visible", CSS,
                      "缺少键盘焦点样式，键盘用户无法定位当前控件")

    def test_reduced_motion_is_respected(self):
        self.assertIn("prefers-reduced-motion", CSS)

    def test_responsive_breakpoints_present(self):
        widths = [int(m) for m in re.findall(r"@media \(max-width:(\d+)px\)", CSS)]
        self.assertGreaterEqual(len(widths), 4, "响应式断点不足")
        self.assertEqual(widths, sorted(widths, reverse=True), "断点未按降序书写")


if __name__ == "__main__":
    unittest.main()