# -*- coding: utf-8 -*-
"""数字人生 · 主代理终验（S43 对拍口径）。

为什么要有它：`scripts/smoke.py` 验的是**代码行为**（用临时库、不碰真实产物），
而日报 md / 时段 html 是**生成物**，它们的正确性只能用「产物 vs 代码现算」对拍来验。
两者不能互相替代 —— 本轮就出现过「冒烟 188/0 全绿，但 753 篇产出含两版占位文案」。

用法:
    python scripts/verify_vault.py            # 全部检查
    python scripts/verify_vault.py --fast     # 跳过 753 天全量对拍（只查结构+叙事指纹）

退出码 0 = 全过；1 = 有未达标项。

⚠️ S60：全量对拍前会先等 `20-日报/` 连续 90s 无写入，避免「跑到一半对拍」的假不一致。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# T1 (2026-10-25): sys.path.insert removed -- package is installable via pip install -e .

from digitallife import daily, paths  # noqa: E402

# 日报结构契约（唯一权威定义在 04-Areas/数字人生/AGENTS.md §D.1）
SIX = ['### 应用', '### 网站', '### 专注段', '### 时间线', '### 离开时段', '### 视觉总结']
OLD_H2 = ('## 今天', '## 展开看全量')

ok: list[str] = []
bad: list[str] = []


def ck(name: str, cond: bool, detail: str = '') -> None:
    (ok if cond else bad).append(name)
    print(('[PASS] ' if cond else '[FAIL] ') + name + ('' if cond else '  ' + str(detail)[:200]))


def is_dev_head(line: str) -> bool:
    """设备节标题：`## 电脑使用`（单台）或 `## iQQQZ3 使用`（多台）。"""
    s = line.strip()
    return s.startswith('## ') and s.endswith('使用')


def structure_checks(daily_dir: Path) -> None:
    files = sorted(daily_dir.glob('*.md'))
    if not files:
        ck('日报存在', False, '没找到任何 md')
        return
    bad_six, old_h2, no_dev = [], [], []
    dist: Counter = Counter()
    for p in files:
        lines = [l.rstrip() for l in p.read_text(encoding='utf-8').splitlines()]
        h2 = [l for l in lines if l.startswith('## ')]
        if any(h.strip() in OLD_H2 for h in h2):
            old_h2.append(p.stem)
        dev = [h for h in h2 if is_dev_head(h)]
        dist[len(dev)] += 1
        if not dev:
            no_dev.append(p.stem)
        cur, subs = None, {}
        for l in lines:
            if l.startswith('## '):
                cur, subs[l.rstrip()] = l.rstrip(), []
            elif l.startswith('### ') and cur:
                subs[cur].append(l.strip())
        for d, got in subs.items():
            if is_dev_head(d) and got != SIX:
                bad_six.append(p.stem)
    print('      日报 %d 篇；设备节分布 %s' % (len(files), dict(sorted(dist.items()))))
    ck('每个设备节下六节齐全且顺序固定', not bad_six, bad_six[:5])
    ck('无残留 ## 今天 / ## 展开看全量', not old_h2, old_h2[:5])
    ck('每篇至少一个设备节', not no_dev, no_dev[:5])


def narrative_check(con: sqlite3.Connection) -> None:
    """全库唯一的模型叙事（2026-08-13）必须逐字保留 —— 它不可重算。"""
    ref = ROOT / 'tmp' / 'a0_narrative_fingerprint.json'
    row = con.execute(
        "SELECT structured_json FROM daily_summary WHERE date_key='2026-08-13'").fetchone()
    narr = ''
    if row:
        try:
            j = json.loads(row[0] or '{}')
            narr = (j.get('narrative') or '') or ((j.get('narratives') or {}).get('pc-main') or '')
        except (TypeError, ValueError):
            narr = ''
    if not ref.exists():
        ck('叙事指纹基线存在', False, str(ref))
        return
    want = json.loads(ref.read_text(encoding='utf-8'))
    got = hashlib.sha256(narr.encode('utf-8')).hexdigest()
    ck('2026-08-13 叙事原文逐字不变（%d 字）' % want['narrative_chars'],
       got == want['narrative_sha256'], '%s vs %s' % (got[:16], want['narrative_sha256'][:16]))


def wait_quiet(daily_dir: Path, seconds: int = 90, timeout: int = 1800) -> bool:
    """S60：等生产者停止写入。连续 seconds 秒 mtime 不变才算静默。"""
    def newest() -> float:
        return max((p.stat().st_mtime for p in daily_dir.glob('*.md')), default=0.0)
    t0 = time.time()
    while time.time() - t0 < timeout:
        a = newest()
        time.sleep(seconds)
        if newest() == a:
            return True
        print('      仍在写入，继续等 %ds...' % seconds, flush=True)
    return False


def drift_check(con: sqlite3.Connection, daily_dir: Path) -> None:
    """S61：产物与当前代码现算逐字一致 —— 抓「跑到一半改了代码」的多版本血统。"""
    files = sorted(daily_dir.glob('*.md'))
    diff = []
    for i, p in enumerate(files, 1):
        try:
            now = daily.build_daily(con, p.stem, persist=False)['output_text'].strip()
        except Exception as exc:  # noqa: BLE001
            diff.append((p.stem, 'ERR ' + str(exc)[:50])); continue
        if now != p.read_text(encoding='utf-8').strip():
            diff.append((p.stem, 'DIFF'))
        if i % 150 == 0:
            print('      ... %d/%d' % (i, len(files)), flush=True)
    ck('产物与当前代码现算逐字一致（%d 篇）' % len(files), not diff, diff[:5])


def placeholder_check(daily_dir: Path) -> None:
    """S61 的产出层自证：同一个可变量在产物里只能有一种取值。"""
    kinds: Counter = Counter()
    for p in daily_dir.glob('*.md'):
        t = p.read_text(encoding='utf-8')
        for cand in ('- （无）', '- （本设备当日无此类记录）'):
            if cand in t:
                kinds[cand] += 1
    ck('空节占位文案只有一种（多版本血统自证）', len(kinds) <= 1, dict(kinds))


def slice_pages_check(slice_dir: Path, con: sqlite3.Connection) -> None:
    hs = sorted(slice_dir.glob('*.html'))
    ck('时段页 >= 753 个', len(hs) >= 753, len(hs))
    if not hs:
        return
    sizes = sorted(p.stat().st_size for p in hs)
    print('      合计 %.1f MB ｜ 中位 %.0f KB' % (sum(sizes) / 1048576, sizes[len(sizes) // 2] / 1024))
    p13 = slice_dir / '2026-08-13.html'
    if p13.exists():
        h = p13.read_text(encoding='utf-8', errors='replace')
        ck('单日页含 24 小时定位条', 'id="hourbar"' in h)
        ck('单日页四个 section 未退化', h.count('<section') >= 4, h.count('<section'))
        # N1（DL-20）：折叠必须真的发生，且**不许把内容折没**。
        # 判据 = 库里 summary 经 render_summary_md 渲染后的 HTML 逐字在页面里（渲染等价，S63）。
        try:
            import build_slice_html as _bsh
            from digitallife.render.vision import render_summary_md as _rsm
            _ctx = _bsh.build_ctx(con, '2026-08-13')
            _n = _miss = _fold = 0
            for _dev in _ctx.devices:
                for _s in (_ctx.slices.get(_dev) or []):
                    _sm = (_s.get('summary') or '').strip()
                    if not _sm:
                        continue
                    _n += 1
                    if _rsm(_sm) not in h:
                        _miss += 1
            _fold = h.count('<details class="vs-fold">')
            ck('N1 折叠已发生（08-13 有 vs-fold）', _fold > 0, _fold)
            ck('N1 折叠未截断（每片 summary 渲染后逐字仍在页面）', _miss == 0,
               '片 %d / 缺 %d' % (_n, _miss))
        except Exception as _e:  # noqa: BLE001
            ck('N1 折叠产物验证', False, '%s: %s' % (type(_e).__name__, _e))

        # N2/N3/N4（DL-19）：定位条必须覆盖**全量**条目，且降采样守恒。
        # 抽样 3 个有代表性的日子（纯 PC / 多设备 / 稀疏短事件）各验一次。
        try:
            import build_slice_html as _bsh2
            from digitallife.render import hourbar as _hb
            _bad = []
            for _day in ('2026-08-13', '2026-08-07', '2024-10-16'):
                _pp = slice_dir / (_day + '.html')
                if not _pp.exists():
                    continue
                _page = _pp.read_text(encoding='utf-8', errors='replace')
                _ctx2 = _bsh2.build_ctx(con, _day)
                _idset = set(re.findall(r'\sid="([^"]+)"', _page))
                _empty = True
                for _seg in ('timeline', 'vision'):
                    for _d, _items in _hb.rows_of_segment(_ctx2, _seg).items():
                        _empty = False
                        _m = re.search(
                            r'<div class="hb-row" data-dev="' + re.escape(_d)
                            + r'" data-seg="' + _seg + r'"[^>]*>(.*?)</div></div>',
                            _page, re.S)
                        if not _m:
                            _bad.append((_day, _d, _seg, '无行容器'))
                            continue
                        _cs = [int(x) for x in re.findall(r'data-c="(\d+)"', _m.group(1))]
                        if sum(_cs) != len(_items):
                            _bad.append((_day, _d, _seg,
                                         'Σ=%d 真实=%d' % (sum(_cs), len(_items))))
                        _missh = [x for x in re.findall(r'href="#([^"]+)"', _m.group(1))
                                  if x not in _idset]
                        if _missh:
                            _bad.append((_day, _d, _seg, 'href 不可解析 %s' % (_missh[:2],)))
                if _empty and 'id="hourbar"' in _page:
                    _bad.append((_day, '-', '-', '无条目却渲染了定位条'))
            ck('N2/N3/N4 定位条零丢失（Σdata-c == 全量条数，抽样 3 日）', not _bad, _bad[:3])
        except Exception as _e:  # noqa: BLE001
            ck('N2/N3/N4 定位条产物验证', False, '%s: %s' % (type(_e).__name__, _e))


def link_title_check(con: sqlite3.Connection) -> None:
    try:
        n = con.execute('SELECT COUNT(*) FROM link_title').fetchone()[0]
        ck('link_title 表有数据（标题入库而非磁盘缓存）', n > 0, n)
    except sqlite3.OperationalError as exc:
        ck('link_title 表有数据（标题入库而非磁盘缓存）', False, exc)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--fast', action='store_true', help='跳过 753 天全量对拍')
    args = ap.parse_args()

    vault = paths.VAULT
    daily_dir, slice_dir = paths.DAILY_DIR, paths.SLICE_DIR
    con = sqlite3.connect('file:' + str(paths.db_path()).replace(chr(92), '/') + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        print('== 1. 日报结构契约 ==')
        structure_checks(daily_dir)
        print('== 2. 叙事孤本零损失 ==')
        narrative_check(con)
        print('== 3. 产物版本一致性（S61）==')
        placeholder_check(daily_dir)
        if not args.fast:
            print('   等待生产者静默（S60）...', flush=True)
            if wait_quiet(daily_dir):
                drift_check(con, daily_dir)
            else:
                ck('等生产者静默', False, '超时，跳过对拍')
        print('== 4. 时段页 ==')
        slice_pages_check(slice_dir, con)
        print('== 5. 链接标题入库 ==')
        link_title_check(con)
    finally:
        con.close()

    print()
    print('=' * 60)
    print('主代理终验: 通过 %d / 失败 %d' % (len(ok), len(bad)))
    for b in bad:
        print('  - ' + b)
    return 0 if not bad else 1


if __name__ == '__main__':
    raise SystemExit(main())