# -*- coding: utf-8 -*-
"""
从岗位库里拉一份可直接投的短名单。

用法：
  python scripts/shortlist.py                      # 默认：外企 售前/技术销售
  python scripts/shortlist.py --preset ai-foreign  # 外企 AI/大模型
  python scripts/shortlist.py --preset mgmt        # 外企/国企央企 管培生
  python scripts/shortlist.py --kw 售前 解决方案 --nature 外企 国企 --city 上海

默认只列**没过期**的，排序：外企/国企央企优先 → 上海优先 → 截止日期近的在前。
加 --broad 从 data/all.json（采集全量）里找，用来临时放宽条件。
薪资和双休一律不带 —— 那些是待核实状态，见 AGENTS.md 第 3 节。
"""
import argparse
import json
import os
import re
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TODAY = date.today().isoformat()

PRESETS = {
    "presales-foreign": {
        "kw": ["售前", "解决方案", "方案顾问", "技术销售", "技术支持", "行业顾问", "技术顾问"],
        "nature": ["外企"],
        "desc": "外企 · 售前/技术销售",
    },
    "presales-all": {
        "kw": ["售前", "解决方案", "方案顾问", "技术销售", "技术支持", "行业顾问", "技术顾问"],
        "nature": ["外企", "央企", "国企"],
        "desc": "外企/国企央企 · 售前/技术销售",
    },
    "ai-foreign": {
        "dirs": ["ai"],
        "nature": ["外企"],
        "desc": "外企 · AI/大模型",
    },
    "mgmt": {
        "dirs": ["mgmt"],
        "nature": ["外企", "央企", "国企"],
        "desc": "外企/国企央企 · 管培生",
    },
    "ai-all": {
        "dirs": ["ai"],
        "desc": "全部 · AI/大模型",
    },
    # 我技术线主投这个
    "agent": {
        "dirs": ["agent"],
        "desc": "Agent / 大模型应用",
    },
    # 自己做过 Shopify 独立站 + 英语好 + 留学背景，这条线单独看
    "trade": {
        "dirs": ["trade"],
        "desc": "跨境电商 / 外贸",
    },
    # 销售线主投这个
    "sales-eng": {
        "dirs": ["sales_eng"],
        "desc": "销售工程师 / 售前 / 解决方案",
    },
    # 不收紧：三条线全要，性质不限。排序会把外企/国企央企和上海顶到前面
    "master": {
        "dirs": ["sales", "sales_eng", "ai", "agent", "trade", "mgmt"],
        "desc": "全部符合条件的岗位",
    },
}


def load(broad=False):
    """
    默认读 matched.json（已按方向 + 目标城市收紧过）。
    --broad 读 all.json：采集阶段留下的全量，用来临时放宽条件找机会，
    比如想看目标城市之外的、或者还没定义成方向的岗位。
    """
    name = "all.json" if broad else "matched.json"
    with open(os.path.join(ROOT, "data", name), encoding="utf-8") as f:
        return json.load(f)


def pick(rows, kw=None, dirs=None, nature=None, city=None, industry=None,
         include_expired=False):
    out = []
    seen = set()
    for r in rows:
        if nature and r["nature"] not in nature:
            continue
        if dirs and not any(d in r["directions"] for d in dirs):
            continue
        if kw and not re.search("|".join(map(re.escape, kw)), r["position"]):
            continue
        # cities 只装目标城市；放宽找机会时要看 all_cities 里的完整清单
        if city and not any(c in city for c in (r.get("all_cities") or r["cities"])):
            continue
        if industry and r["industry"] not in industry:
            continue
        if not include_expired and r["deadline"] and r["deadline"] < TODAY:
            continue
        # 同公司同岗位只留一条，跨批次的重复没必要都列
        k = (r["company"], r["position"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    out.sort(key=lambda r: (
        0 if r["nature"] in ("外企", "央企", "国企") else 1,
        r["city_tier"],
        r["deadline"] or "9999-99-99",
        r["company"]))
    return out


DIR_LABEL = {"sales": "销售系", "sales_eng": "销售工程师",
             "ai": "AI/大模型", "agent": "Agent方向",
             "trade": "跨境/外贸", "mgmt": "管培生"}


def render(rows, title):
    lines = ["# %s" % title,
             "",
             "生成于 %s，共 **%d** 条（同公司同岗位只留一条、已排除过期）。" % (TODAY, len(rows)),
             "",
             "排序：外企/国企央企优先 → 上海优先 → 截止日期近的在前。",
             "",
             "**薪资、双休、是否有笔试、留学生能不能投，全都还是待核实** —— 投之前自己看公告。",
             "标「岗位组」的是源站把一堆岗位挤成了一条，得到官网才知道具体有哪些岗位。",
             "",
             "| 公司 | 岗位 | 方向 | 城市 | 性质 | 行业 | 批次 | 网申开放 | 截止 | 备注 | 投递 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        link = "[投递](%s)" % r["apply_url"] if r["apply_url"] else \
               ("[公告](%s)" % r["notice_url"] if r["notice_url"] else "官网搜")
        notes = []
        if r.get("position_is_group"):
            notes.append("岗位组")
        if r.get("overseas_keywords"):
            notes.append("公告提到" + "/".join(r["overseas_keywords"]))
        if r.get("city_status") == "待核实":
            notes.append("城市待核实")
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            r["company"], r["position"].replace("|", "/"),
            "/".join(DIR_LABEL.get(d, d) for d in r["directions"]),
            "/".join(r["cities"]) or "待核实", r["nature"], r["industry"] or "-",
            r["batch"] or "-", r.get("opens_on") or "-",
            r["deadline"] or r["deadline_raw"] or "-",
            "、".join(notes) or "-", link))
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="presales-foreign", choices=sorted(PRESETS))
    ap.add_argument("--kw", nargs="*")
    ap.add_argument("--dirs", nargs="*")
    ap.add_argument("--nature", nargs="*")
    ap.add_argument("--city", nargs="*")
    ap.add_argument("--include-expired", action="store_true")
    ap.add_argument("--broad", action="store_true",
                    help="从全量 all.json 里找，而不是收紧过的 matched.json")
    ap.add_argument("--out", help="写到这个 md 文件，不给就只打印")
    ap.add_argument("--title", help="覆盖标题；自定义筛选时用，别沿用 preset 的描述")
    ap.add_argument("--industry", nargs="*", help="只要这些行业")
    a = ap.parse_args()

    p = PRESETS[a.preset]
    # 给了任何自定义条件，就整套用自定义的，别再混进 preset 的默认值
    custom = any([a.kw, a.dirs, a.nature, a.city, a.industry])
    rows = pick(load(a.broad),
                kw=a.kw if custom else p.get("kw"),
                dirs=a.dirs if custom else p.get("dirs"),
                nature=a.nature if custom else p.get("nature"),
                city=a.city,
                industry=a.industry,
                include_expired=a.include_expired)
    title = a.title or ((("自定义筛选" if custom else p["desc"])) + " 短名单")
    text = render(rows, title)
    if a.out:
        path = a.out if os.path.isabs(a.out) else os.path.join(ROOT, a.out)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("写入 %s（%d 条）" % (path, len(rows)))
    else:
        print(text)


if __name__ == "__main__":
    main()
