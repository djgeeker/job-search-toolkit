# -*- coding: utf-8 -*-
"""
抓取各来源，按日期存成快照：raw/snap/<YYYY-MM-DD>/...

只走公开 HTTP，不绕登录、不过验证码。raw/ 只增不改——每次抓都是新的一天一个目录，
旧快照原样保留。build_dataset.py 会把所有快照合在一起读，所以从聚合站首页
掉下去的旧公告也不会丢（采集要宽）。

用法：
  python scripts/fetch_sources.py all            # 今天的快照，全部来源
  python scripts/fetch_sources.py campus niuqizp # 只抓这几个
  python scripts/fetch_sources.py all --date 2026-09-15

来源：
  campus   campus.sma-wiki.cn   单页，数据在内联的 RAW_DATA 里（约 5 MB，慢）
  niuqizp  campus.niuqizp.com   列表页，一页 31 条，抓到没有卡片为止
  sasac    国资委招聘栏目（WAP） 列表页 + 校招相关公告
  xixicc   GitHub xixicc2027    git pull，仓库自己带 first_seen/last_seen

抓完跑 python scripts/build_dataset.py 重建岗位库。
"""
import argparse
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP = os.path.join(ROOT, "raw", "snap")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
DELAY = 1.2   # 别把人家站点打疼了


def get(url, timeout=60):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# ---------------------------------------------------------------- campus.sma-wiki.cn

def fetch_campus(day_dir):
    dest = os.path.join(day_dir, "campus.sma-wiki.cn.html")
    if os.path.exists(dest) and os.path.getsize(dest) > 1_000_000:
        print("campus：今天已经抓过，跳过")
        return
    # 整页 5 MB 左右，这台机器上下载慢，超时给足
    html = get("https://campus.sma-wiki.cn/", timeout=300)
    if "const RAW_DATA = " not in html:
        print("campus：页面里没找到 RAW_DATA，站点结构可能变了，没保存")
        return
    write(dest, html)
    n = html.count('"company":')
    print("campus：%.1f MB，约 %d 条" % (len(html) / 1e6, n))


# ---------------------------------------------------------------- 牛企直聘

def fetch_niuqizp(day_dir, max_pages=300):
    out = os.path.join(day_dir, "niuqizp")
    os.makedirs(out, exist_ok=True)
    ok = fail = 0
    for n in range(1, max_pages + 1):
        dest = os.path.join(out, "p%03d.html" % n)
        if os.path.exists(dest) and os.path.getsize(dest) > 50000:
            ok += 1
            continue
        try:
            html = get("https://campus.niuqizp.com/schedulenew-%d/" % n)
        except Exception as e:
            fail += 1
            print("  p%-3d 失败: %s" % (n, e))
            if fail >= 5:
                print("  连续失败太多，停")
                break
            time.sleep(DELAY)
            continue
        cards = html.count('class="schedule-card"')
        if cards == 0:
            print("  p%-3d 没有卡片，到底了" % n)
            break
        # 超出最后一页时站点会返回最后一页的内容，靠和上一页比对来识别
        prev = os.path.join(out, "p%03d.html" % (n - 1))
        if n > 1 and os.path.exists(prev):
            if first_card_link(html) == first_card_link(open(prev, encoding="utf-8").read()):
                print("  p%-3d 和上一页一样，到底了" % n)
                break
        write(dest, html)
        ok += 1
        if n % 10 == 0:
            print("  p%-3d …" % n)
        time.sleep(DELAY)
    print("牛企直聘：%d 页，失败 %d 页" % (ok, fail))


def first_card_link(html):
    m = re.search(r'class="schedule-card-title"[^>]*>\s*<a[^>]*href="([^"]*)"', html)
    return m.group(1) if m else None


# ---------------------------------------------------------------- 国资委

SASAC_BASE = "http://wap.sasac.gov.cn/n2588035/n2588325/n2588350/"


def fetch_sasac(day_dir, max_details=60):
    """
    国资委招聘栏目只挂最近十几条，WAP 版正文是空的（拿不到岗位和投递链接）。
    在这里只用来确认哪些公司是央企，所以只存列表 + 校招公告标题页。
    """
    out = os.path.join(day_dir, "sasac")
    index = get(SASAC_BASE + "index.html")
    write(os.path.join(out, "index.html"), index)
    items = []
    for m in re.finditer(r'<a[^>]*href="[^"]*c(\d+)/content\.html"[^>]*>(.*?)</a>', index, re.S):
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if title and re.search(r"校园招聘|校招|届|毕业生|招募", title):
            items.append((m.group(1), title))
    print("国资委：校招相关 %d 条" % len(items))
    for cid, title in items[:max_details]:
        dest = os.path.join(out, "c%s.html" % cid)
        if os.path.exists(dest):
            continue
        try:
            write(dest, get(SASAC_BASE + "c%s/content.html" % cid))
        except Exception as e:
            print("  c%s 失败: %s" % (cid, e))
        time.sleep(DELAY)


# ---------------------------------------------------------------- xixicc2027

def fetch_xixicc(_day_dir):
    repo = os.path.join(ROOT, "sources", "xixicc2027")
    r = subprocess.run(["git", "-C", repo, "pull", "--ff-only"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    print("xixicc2027：", (r.stdout or r.stderr).strip().splitlines()[-1:] or "")


# ---------------------------------------------------------------- 面灵AI

MIANLING_API = "https://api.mianlingai.com/trpc/campusJob.list"
ML_FIELDS = ["company", "province", "city", "employmentType", "batch", "jobFamily", "jobFunction",
             "facet", "orgType", "industry", "companyTag", "keyword", "deadlineWithinDays",
             "publishedWithinDays"]
# 只按 我的方向查。第 2 页起站点要求登录（接口回 401「登录后可以继续翻页」），
# 不绕：每个筛选只取第 1 页，等于不登录的访客在网站上点筛选能看到的量。
ML_TARGETS = [("jobFunction", "销售"), ("jobFunction", "技术支持"), ("jobFunction", "商务拓展"),
              ("jobFunction", "管理培训生"), ("facet", "大模型与Agent")]
ML_PROVINCES = ["上海", "北京", "广东", "浙江", "四川"]
ML_ORGTYPES = ["外企", "国企", "央企"]


def _ml_call(filters):
    import json, urllib.parse
    j = {"page": 1, "pageSize": 50, "hideClosed": True}
    for f in ML_FIELDS:
        j[f] = filters.get(f)
    j["sort"] = "hot"
    meta = {"values": {f: ["undefined"] for f in ML_FIELDS if j[f] is None}, "v": 1}
    inp = urllib.parse.quote(json.dumps({"0": {"json": j, "meta": meta}},
                                        ensure_ascii=False, separators=(",", ":")))
    req = urllib.request.Request(MIANLING_API + "?batch=1&input=" + inp, headers={
        "User-Agent": UA, "Referer": "https://www.mianlingai.com/", "Origin": "https://www.mianlingai.com"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())[0]["result"]["data"]["json"]


def fetch_mianling(day_dir):
    import json
    out = os.path.join(day_dir, "mianling")
    os.makedirs(out, exist_ok=True)
    queries = []
    for k, v in ML_TARGETS:
        for prov in ML_PROVINCES:
            queries.append({k: v, "province": prov})
        for ot in ML_ORGTYPES:
            queries.append({k: v, "orgType": ot})
    got = 0
    for q in queries:
        name = "_".join("%s-%s" % kv for kv in sorted(q.items()))
        dest = os.path.join(out, name + ".json")
        if os.path.exists(dest):
            continue
        try:
            d = _ml_call(q)
            # 单独调接口时 items 在顶层（SSR 打包的那份才包在 list 里）
            json.dump({"query": q, "total": d["total"], "items": d["items"]},
                      open(dest, "w", encoding="utf-8"), ensure_ascii=False)
            got += len(d["items"])
        except Exception as e:
            print("  %s 失败: %s" % (name, e))
        time.sleep(DELAY)
    print("面灵AI：%d 个筛选，拿到 %d 条（每个筛选只取公开的第 1 页）" % (len(queries), got))


# ---------------------------------------------------------------- givemeoc

def fetch_givemeoc(day_dir, max_pages=400):
    """公开列表页 ?paged=N。投递链接/公告要登录、公司规模/笔试要会员，都不碰。"""
    out = os.path.join(day_dir, "givemeoc")
    os.makedirs(out, exist_ok=True)
    ok = 0
    for n in range(1, max_pages + 1):
        dest = os.path.join(out, "p%03d.html" % n)
        if os.path.exists(dest):
            ok += 1
            continue
        try:
            html = get("https://www.givemeoc.com/" + ("" if n == 1 else "?paged=%d" % n))
        except Exception as e:
            print("  p%-3d 失败: %s" % (n, e))
            time.sleep(DELAY)
            continue
        if html.count("<tr") < 3:
            print("  p%-3d 没有表格行，到底了" % n)
            break
        # 超出最后一页时站点照样回最后一页（2026-09-15 实测第 232 页之后全是它），和上一页比首行
        prev = os.path.join(out, "p%03d.html" % (n - 1))
        if n > 1 and os.path.exists(prev):
            if first_row_id(html) == first_row_id(open(prev, encoding="utf-8").read()):
                print("  p%-3d 和上一页一样，到底了" % n)
                break
        write(dest, html)
        ok += 1
        if n % 25 == 0:
            print("  p%-3d …" % n)
        time.sleep(DELAY)
    print("givemeoc：%d 页" % ok)


def first_row_id(html):
    m = re.search(r'<tr data-id="(\d+)"', html)
    return m.group(1) if m else None


FETCHERS = {
    "campus": fetch_campus,
    "niuqizp": fetch_niuqizp,
    "sasac": fetch_sasac,
    "xixicc": fetch_xixicc,
    "mianling": fetch_mianling,
    "givemeoc": fetch_givemeoc,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("what", nargs="+", help="all 或者 %s 里的若干个" % "/".join(FETCHERS))
    ap.add_argument("--date", default=date.today().isoformat())
    a = ap.parse_args()

    names = list(FETCHERS) if "all" in a.what else a.what
    day_dir = os.path.join(SNAP, a.date)
    os.makedirs(day_dir, exist_ok=True)
    print("快照目录：", day_dir)
    for n in names:
        if n not in FETCHERS:
            sys.exit("不认识的来源：%s" % n)
        print("\n=== %s ===" % n)
        try:
            FETCHERS[n](day_dir)
        except Exception as e:
            # 一个源挂了不影响其他源
            print("%s 整体失败：%s" % (n, e))


if __name__ == "__main__":
    main()
