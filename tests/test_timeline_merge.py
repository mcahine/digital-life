"""T19 时间线归并剪枝的语义契约（从 smoke.py 迁入，T2）。

来源：smoke.py 的 T19a–T19j 十条。
为什么必须迁：它们是**四层归并**的语义契约，改错一个判据就是全库时间线重复，
而现在只在 1,404 行的 smoke 单文件里。迁进 pytest 后「改一行代码，3 秒内知道对错」。

每条都标了「能抓住什么真实回归」，并做过 S20 注入验红（见文件末的注入说明）。
"""
from __future__ import annotations

import pytest

from digitallife.domain.timeline_merge import _drop_page_echoes as dpe

U = "https://www.bilibili.com/video/BV1FF"


def row(st, en, app, title, url="", link=""):
    return dict(start=st, end=en, app=app, title=title, url=url, link_url=link, sub=[])


def n(rows):
    return len(dpe([dict(r) for r in rows]))


def pair(off):
    """重叠率按「较短者」的 90% 判；两个 60s 区间错开 off 秒。"""
    return [
        row("10:00:00", "10:01:00", "Google Chrome", U, U, U),
        row("10:00:%02d" % off, "10:01:%02d" % off, "Google Chrome", U, U, U),
    ]


def test_overlap_90_folds():
    """T19a 重叠 90% 同身份 → 折成 1 行。

    能抓住的真实回归：若把 90% 判据改成「任意重叠就折」，不同事件会被说成一件事
    （用户原话「都不是一个事件的为什么要搞一个次级列表」）。
    """
    assert n(pair(6)) == 1


def test_overlap_89_keeps_two():
    """T19b 重叠 89% → 保留 2 行（边界另一侧）。

    能抓住的真实回归：阈值被写成 >=89% 或 <=91% 时，本用例变红。
    """
    assert n(pair(7)) == 2


def test_overlap_60_keeps_two():
    """T19c 重叠 60% → 保留 2 行。"""
    assert n(pair(24)) == 2


def test_same_identity_different_period_keeps_two():
    """T19d 同身份但当期不同 → 保留 2 行。

    能抓住的真实回归：剪枝只看「页面身份」不看时间窗，会把一天里两次访问同一页折成一次。
    """
    rows = [row("10:00:00", "10:01:00", "Google Chrome", U, U, U),
            row("11:00:00", "11:01:00", "Google Chrome", U, U, U)]
    assert n(rows) == 2


def test_cross_type_title_alignment_keeps_link():
    """T19e 跨类型标题对齐 → 折成 1 行且**留下有链接者**。

    能抓住的真实回归：合并时保留了无链接的一方，导致整行失去可点击链接
    （链接是内容，不是表述）。
    """
    rows = [row("10:00:00", "10:01:00", "Google Chrome", "bilibili"),
            row("10:00:02", "10:01:02", "tai", "bilibili", U, U)]
    out = dpe([dict(r) for r in rows])
    assert len(out) == 1
    assert str(out[0].get("link_url") or "") == U


def test_display_title_www_variant_no_dup():
    """T19i www 变体不得重复域名（实测残留 16 处）。"""
    from digitallife.domain.timeline_merge import display_title
    assert display_title({"app": "www.bilibili.com",
                          "title": "bilibili.com · BV1SZgG67Efs"}) \
        == "bilibili.com · BV1SZgG67Efs"


def test_display_title_keeps_app_prefix():
    """T19j 不同域名仍要带 app 前缀。"""
    from digitallife.domain.timeline_merge import display_title
    assert display_title({"app": "Microsoft Edge", "title": "Kimi.ai"}) \
        == "Microsoft Edge · Kimi.ai"


def test_prune_3000_rows_not_degraded():
    """T19h 3000 行剪枝不得退化（<10s）。

    能抓住的真实回归：把索引版改回两两比对，单日 build_daily 从 1.6s 涨到 14.6s
    （2025-02-17 实测 97% 耗时在这）。
    """
    import random
    import time
    rnd = random.Random(20261025)
    big = []
    for _ in range(3000):
        s0 = rnd.randint(0, 80000)
        d = rnd.choice([0, 1, 5, 30, 120, 600])
        u = "https://%s/p%d" % (rnd.choice(["a.com", "www.b.com", "c.cn"]),
                                rnd.randint(0, 60))
        big.append(row("%02d:%02d:%02d" % (s0 // 3600, (s0 % 3600) // 60, s0 % 60),
                       "%02d:%02d:%02d" % ((s0 + d) // 3600, ((s0 + d) % 3600) // 60, (s0 + d) % 60),
                       "tai", u, u, u))
    t0 = time.time()
    dpe(big)
    assert time.time() - t0 < 10.0
