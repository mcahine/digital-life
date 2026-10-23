"""单日时段页的公共底座：设计 token / 页面外壳 / 转义与链接工具。

设计来源：00-Inbox/index.html —— 用户 2026-10-22 指定「参考它是怎么做的，除了文件命名
不是对应日期，其他都挺好」。所以这里照搬那套视觉：
  MiSans VF + ZCOOL XiaoWei 展示字体、JetBrains Mono 数字、蓝色 --accent、
  分段式设备 tab（role=tablist）、data-extra + 「展开全部」按钮、
  右下 fab 目录浮钮 → 锚点跳转、localStorage 记忆主题。

结构变更（同日）：不再是「每天一个文件夹 + 9 页」，而是**一天一页**：
  04-Areas/数字人生/10-时段/YYYY-MM-DD.html
四个 section（时间线 / 应用 / 网站 / 视觉）用锚点 + 设备 tab 组织。
片层 md 不受影响，移在 10-时段/片层/YYYY-MM-DD/。

**这个文件是所有 section 模块的公共依赖**（单写者：主代理）。section 模块只产出 body 片段。
"""
from __future__ import annotations

import html
from pathlib import Path

CSS = """
:root{
  --bg:#F4F5F3; --panel:#FFFFFF; --panel2:#FAFAF8;
  --ink:#14171A; --ink2:#3D4349; --muted:#6B7178;
  --line:#E7E9E5; --line2:#D5D8D2; --lineSoft:#F1F2EE;
  --accent:#2C5CE6; --accentSoft:#EDF1FE;
  --shadow:0 1px 2px rgba(16,24,40,.04);
  --shadowLg:0 10px 30px rgba(16,24,40,.14);
  --r:12px; --rSm:8px;
  --sans:"MiSans VF","MiSans","Noto Sans SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif;
  --display:"ZCOOL XiaoWei","Noto Serif SC","Songti SC","STSong",serif;
  --mono:"JetBrains Mono",ui-monospace,"SF Mono",Consolas,"Cascadia Mono",monospace;
}
[data-theme="dark"]{
  --bg:#0F1113; --panel:#16191C; --panel2:#1B1F23;
  --ink:#E8EAE7; --ink2:#C4C9C3; --muted:#9AA0A6;
  --line:#2A2E32; --line2:#3A3F44; --lineSoft:#22262A;
  --accent:#8AB4F8; --accentSoft:#1B2A44;
  --shadow:none; --shadowLg:0 10px 30px rgba(0,0,0,.5);
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
  font-size:14.5px;line-height:1.6;font-weight:420;-webkit-font-smoothing:antialiased}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:4px}
.wrap{max-width:1200px;margin:0 auto;padding:0 24px}

/* header */
.site-header{padding:36px 0 22px}
.h-row{display:flex;align-items:flex-start;justify-content:space-between;gap:24px}
h1{display:flex;align-items:baseline;gap:10px;margin:0;font-weight:400;line-height:1.2}
h1 .date{font-family:var(--mono);font-size:16px;font-weight:500;color:var(--ink2)}
h1 .dot{color:var(--line2);font-size:14px;transform:translateY(-1px)}
h1 .title{font-family:var(--display);font-size:31px;letter-spacing:.05em;color:var(--ink)}
.h-sub{margin:7px 0 0;font-size:12px;color:var(--muted);letter-spacing:.18em}
.theme-btn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 12px;
  background:var(--panel);border:1px solid var(--line);border-radius:var(--rSm);
  color:var(--ink2);font-size:12.5px;cursor:pointer;box-shadow:var(--shadow);
  transition:background .14s ease,border-color .14s ease}
.theme-btn:hover{background:var(--panel2);border-color:var(--line2)}
.stats{display:flex;flex-wrap:wrap;gap:8px 18px;margin:16px 0 0;font-size:12.5px;color:var(--muted)}
.stats b{color:var(--ink2);font-weight:600;font-family:var(--mono)}

/* section */
.section{padding:30px 0;border-top:1px solid var(--line)}
.section-head{display:flex;align-items:center;gap:12px;margin:0 0 16px;flex-wrap:wrap}
.section-head h2{font-family:var(--display);font-size:22px;font-weight:400;
  letter-spacing:.05em;margin:0}
.count{font-family:var(--mono);font-size:12px;color:var(--muted)}
.seg{margin-left:auto;display:inline-flex;background:var(--panel2);border:1px solid var(--line);
  border-radius:var(--rSm);padding:2px;gap:2px}
.seg button{background:none;border:none;padding:5px 12px;border-radius:6px;font-size:12.5px;
  color:var(--muted);cursor:pointer;font-family:inherit;transition:background .14s,color .14s}
.seg button[aria-selected="true"]{background:var(--panel);color:var(--ink);box-shadow:var(--shadow)}
.seg .n{font-family:var(--mono);font-size:11px;color:var(--muted);margin-left:5px}
.panel[hidden]{display:none}

/* table */
.tw{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--lineSoft);vertical-align:top}
th{color:var(--muted);font-weight:500;font-size:11.5px;letter-spacing:.06em;
  border-bottom-color:var(--line);white-space:nowrap}
th.num,td.num{text-align:right;font-family:var(--mono);font-size:12.5px;white-space:nowrap}
th.t,td.t{white-space:nowrap}
td.muted{color:var(--muted)}
td.id{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
tbody tr:hover td{background:var(--panel)}
.ev{color:var(--ink2)}
.ev.major{color:var(--ink);font-weight:600}
.ttl a{color:inherit;text-decoration:none;border-bottom:1px dotted var(--line2)}
.ttl a:hover{border-bottom-color:var(--accent);color:var(--accent)}
ul.sub{list-style:none;margin:2px 0 2px 16px;padding:0}
ul.sub>li{font-size:12.5px;color:var(--muted);padding:1px 0}
ul.sub>li a{color:var(--muted);border-bottom:1px dotted var(--line2)}
ul.sub>li a:hover{color:var(--accent)}
/* 提示段落。类名不能叫 .bar —— 它和「百分比横条」撞名，
   实测把整段文字染成一条蓝色胶囊（2026-10-22 用户报「蓝条是什么鬼」）。 */
.lead{margin:0 0 14px;font-size:12.5px;color:var(--muted)}

/* sites */
.sites{display:flex;flex-direction:column;gap:8px}
.site{background:var(--panel);border:1px solid var(--line);border-radius:var(--rSm);
  box-shadow:var(--shadow);overflow:hidden}
.site>summary{display:flex;align-items:center;gap:12px;padding:11px 14px;cursor:pointer;
  list-style:none;font-size:13.5px}
.site>summary::-webkit-details-marker{display:none}
.site-name{font-weight:600;color:var(--ink)}
.site-figs{margin-left:auto;display:flex;align-items:baseline;gap:14px}
.site-meta{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.site-dur{font-family:var(--mono);font-size:12px;color:var(--ink2)}
.chev{width:16px;height:16px;color:var(--muted);transition:transform .14s ease;flex:none}
.site[open] .chev,.more[aria-expanded="true"] .chev{transform:rotate(180deg)}
.visits{padding:2px 14px 12px;border-top:1px solid var(--lineSoft);display:flex;
  flex-direction:column;gap:3px}
.visit{display:flex;gap:10px;align-items:baseline;font-size:12.5px;padding:3px 0}
.vt{font-family:var(--mono);font-size:11.5px;color:var(--muted);white-space:nowrap;
  flex:none;width:132px}
.vd{font-family:var(--mono);font-size:11.5px;color:var(--muted);white-space:nowrap;
  flex:none;width:62px}
.visit a{color:var(--ink2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
  border-bottom:1px dotted var(--line2)}
.visit a:hover{color:var(--accent);border-bottom-color:var(--accent)}

/* more */
.more{display:inline-flex;align-items:center;justify-content:center;gap:6px;width:100%;
  margin:10px 0 0;padding:8px;background:var(--panel);border:1px solid var(--line);
  border-radius:var(--rSm);color:var(--muted);font-size:12.5px;cursor:pointer;
  transition:background .14s,border-color .14s}
.more:hover{background:var(--panel2);border-color:var(--line2);color:var(--ink2)}

/* card */
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  padding:16px 18px;margin:0 0 10px;box-shadow:var(--shadow)}
.card h3{margin:0 0 4px;font-size:14.5px;font-weight:600}
.card .when{font-family:var(--mono);font-size:11.5px;color:var(--muted);margin:0 0 10px}
.card p{margin:0 0 8px;color:var(--ink2);font-size:13.5px}
.card ul{margin:6px 0 0;padding-left:20px;color:var(--ink2);font-size:13.5px}
.card strong{color:var(--ink)}
.empty{color:var(--muted);font-style:italic;padding:26px 0}

/* dock */
.dock{position:fixed;right:20px;bottom:20px;display:flex;flex-direction:column;
  align-items:flex-end;gap:10px;z-index:40}
.jump{background:var(--panel);border:1px solid var(--line);border-radius:var(--r);
  box-shadow:var(--shadowLg);padding:8px;min-width:158px}
.jump[hidden]{display:none}
.j-head{font-size:11px;color:var(--muted);letter-spacing:.14em;padding:4px 10px 8px}
.jump a{display:flex;justify-content:space-between;gap:16px;padding:7px 10px;
  border-radius:6px;color:var(--ink2);font-size:13px}
.jump a:hover{background:var(--panel2);text-decoration:none;color:var(--ink)}
.jump a span{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
.fab{display:inline-flex;align-items:center;gap:7px;height:40px;padding:0 15px;
  background:var(--panel);border:1px solid var(--line);border-radius:20px;
  color:var(--ink2);font-size:13px;cursor:pointer;box-shadow:var(--shadowLg)}
.fab:hover{background:var(--panel2);border-color:var(--line2)}
.fab svg{width:15px;height:15px}
"""

JS = """
(function () {
  var root = document.documentElement;
  var btn = document.getElementById('themeBtn');
  var label = document.getElementById('themeLabel');
  var KEY = 'dl-slice-theme';
  function apply(v) {
    root.setAttribute('data-theme', v);
    try { localStorage.setItem(KEY, v); } catch (e) {}
    if (btn) btn.setAttribute('aria-pressed', v === 'dark' ? 'true' : 'false');
    if (label) label.textContent = v === 'dark' ? '浅色' : '深色';
  }
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) {}
  var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  apply(saved || (prefersDark ? 'dark' : 'light'));
  if (btn) btn.addEventListener("click", function () {
    apply(root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
  });

  var fab = document.getElementById('fab');
  var jump = document.getElementById('jump');
  function setJump(open) {
    if (!jump || !fab) return;
    jump.hidden = !open;
    fab.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  if (fab && jump) {
    fab.addEventListener("click", function (ev) { ev.stopPropagation(); setJump(jump.hidden); });
    jump.addEventListener("click", function (ev) { if (ev.target.closest("a")) setJump(false); });
    document.addEventListener("click", function (ev) {
      if (!jump.hidden && !jump.contains(ev.target) && ev.target !== fab) setJump(false);
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key === "Escape" && !jump.hidden) { setJump(false); fab.focus(); }
    });
  }

  document.querySelectorAll('[role="tablist"]').forEach(function (list) {
    var tabs = Array.prototype.slice.call(list.querySelectorAll('[role="tab"]'));
    function select(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab;
        t.setAttribute('aria-selected', on ? 'true' : 'false');
        t.tabIndex = on ? 0 : -1;
        var p = document.getElementById(t.getAttribute('aria-controls'));
        if (p) p.hidden = !on;
      });
      if (focus) tab.focus();
    }
    tabs.forEach(function (tab) {
      tab.addEventListener("click", function () { select(tab); });
      tab.addEventListener("keydown", function (ev) {
        var i = tabs.indexOf(tab), j = -1;
        if (ev.key === "ArrowRight") j = (i + 1) % tabs.length;
        else if (ev.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
        else if (ev.key === "Home") j = 0;
        else if (ev.key === "End") j = tabs.length - 1;
        if (j > -1) { ev.preventDefault(); select(tabs[j], true); }
      });
    });
  });

  document.querySelectorAll(".more").forEach(function (b) {
    b.addEventListener("click", function () {
      var box = document.getElementById(b.getAttribute('data-for'));
      if (!box) return;
      var open = b.getAttribute('aria-expanded') === 'true';
      box.querySelectorAll("[data-extra]").forEach(function (el) { el.hidden = open; });
      b.setAttribute('aria-expanded', open ? 'false' : 'true');
      var lab = b.querySelector(".more-t");
      if (lab) lab.textContent = open ? b.getAttribute('data-label') : '收起';
    });
  });

  function reveal(id, scroll) {
    var el = document.getElementById(id);
    if (!el) return;
    var item = el.closest("[data-extra]");
    if (item) {
      var box = item.parentElement;
      var b = box ? document.querySelector('.more[data-for="' + box.id + '"]') : null;
      if (b && b.getAttribute('aria-expanded') !== 'true') b.click();
    }
    if (el.tagName === "DETAILS") el.open = true;
    if (scroll) el.scrollIntoView({ block: "center" });
  }
  function fromHash() { var id = location.hash.slice(1); if (id) reveal(id, true); }
  window.addEventListener("hashchange", fromHash);
  document.addEventListener("click", function (ev) {
    var a = ev.target.closest('a[href^="#site-"]');
    if (!a) return;
    var id = a.getAttribute('href').slice(1);
    setTimeout(function () { reveal(id, true); }, 0);
  });
  fromHash();
})();
"""


# ---------------------------------------------------------------- 工具

def esc(v) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def link(title, url, cls: str = "") -> str:
    """把「标题 + URL」渲染成 a 标签；URL 非法时退回纯文本。

    标题为空或恰好等于 URL 时，锚文本退化为域名+路径，避免同一串出现两遍。
    """
    url = str(url or "").strip()
    title = str(title or "").strip()
    if not url.startswith(("http://", "https://")):
        return esc(title)
    if not title or title == url:
        title = url.split("//", 1)[-1] or url
    c = (' class="%s"' % esc(cls)) if cls else ""
    return '<a href="%s" target="_blank" rel="noreferrer"%s>%s</a>' % (esc(url), c, esc(title))


def seg(target_id: str, options, current: int = 0) -> str:
    """设备切换分段控件（照搬参考页的 role=tablist 形态）。

    options = [(panel_id, label, count), ...]；count 为 None 时不显示数字徽标。
    """
    if len(options) < 2:
        return ""
    out = ['<div class="seg" role="tablist" aria-label="%s">' % esc(target_id)]
    for i, (pid, label, count) in enumerate(options):
        on = i == current
        n = (' <span class="n">%s</span>' % esc(count)) if count not in (None, "") else ""
        out.append('<button type="button" role="tab" id="tab-%s" aria-controls="%s" '
                   'aria-selected="%s"%s>%s%s</button>'
                   % (esc(pid), esc(pid), "true" if on else "false",
                      "" if on else ' tabindex="-1"', esc(label), n))
    out.append("</div>")
    return "".join(out)


def more_button(target_id: str, label: str) -> str:
    """「展开全部 N 行」按钮（data-extra 折叠，参考页同款）。"""
    return ('<button class="more" type="button" data-for="%s" data-label="%s" '
            'aria-expanded="false"><span class="more-t">%s</span>'
            '<svg class="chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 6l4 4 4-4" '
            'fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" '
            'stroke-linejoin="round"/></svg></button>'
            % (esc(target_id), esc(label), esc(label)))


def card(title: str, when: str, body_html: str) -> str:
    return ('<div class="card"><h3>%s</h3><p class="when">%s</p>%s</div>'
            % (esc(title), esc(when), body_html))


def page(*, date_key: str, weekday: str, stats_html: str, sections, body: str) -> str:
    """拼一整天单页 HTML（主题按钮 + 右下目录浮钮）。

    sections = [(anchor, label, count), ...]，用于右下目录跳转；只列实际渲染了的 section。
    """
    return "".join([
        '<!doctype html><html lang="zh-CN" data-theme="light"><head>',
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta name="color-scheme" content="light dark">',
        "<title>%s · 数字人生</title>" % esc(date_key),
        '<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>',
        '<link rel="stylesheet" '
        'href="https://cdn.jsdelivr.net/npm/misans@5.0.0/lib/Normal/MiSansVF.min.css">',
        '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
        'family=ZCOOL+XiaoWei&amp;family=JetBrains+Mono:wght@400;500&amp;display=swap">',
        "<style>%s</style>" % CSS,
        "</head><body>",
        '<header class="site-header"><div class="wrap">',
        '<div class="h-row"><div>',
        '<h1><span class="date">%s</span><span class="dot">·</span>'
        '<span class="title">数字人生</span></h1>' % esc(date_key),
        '<p class="h-sub">%s</p>' % esc(weekday),
        "</div>",
        '<button class="theme-btn" id="themeBtn" type="button" aria-pressed="false">',
        '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path '
        'd="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 '
        '1.1M12.6 3.4l-1.1 1.1M4.5 11.5l-1.1 1.1" fill="none" stroke="currentColor" '
        'stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="8" r="2.7" '
        'fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
        '<span id="themeLabel">深色</span></button>',
        "</div>",
        stats_html,
        "</div></header>",
        '<main class="wrap">',
        body,
        "</main>",
        body_dock(sections),
        "<script>%s</script>" % JS,
        "</body></html>",
    ])


def body_dock(sections) -> str:
    """右下目录浮钮 + 锚点列表（参考页 #timeline/#apps/#sites/#vision 形态）。"""
    links = "".join(
        '<a href="#%s">%s <span>%s</span></a>' % (esc(a), esc(lab), esc(cnt))
        for a, lab, cnt in (sections or [])
    )
    return ('<div class="dock"><nav class="jump" id="jump" aria-label="页面跳转" hidden>'
            '<div class="j-head">跳转</div>' + links + '</nav>'
            '<button class="fab" id="fab" type="button" aria-expanded="false" '
            'aria-controls="jump"><svg viewBox="0 0 16 16" aria-hidden="true"><path '
            'd="M2.5 4.5h11M2.5 8h11M2.5 11.5h11" fill="none" stroke="currentColor" '
            'stroke-width="1.5" stroke-linecap="round"/></svg><span>目录</span></button></div>')


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def fmt_dur(seconds) -> str:
    """秒 -> 1 小时 23 分 / 12 分 05 秒 / 38 秒。"""
    try:
        s = int(float(seconds or 0))
    except (TypeError, ValueError):
        return "0 秒"
    if s <= 0:
        return "0 秒"
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return "%d 小时 %02d 分" % (h, m)
    if m:
        return "%d 分 %02d 秒" % (m, sec)
    return "%d 秒" % sec


def span(a, b) -> str:
    return "%s-%s" % (a or "", b or "")
