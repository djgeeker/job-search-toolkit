# -*- coding: utf-8 -*-
"""
把各来源的秋招/校招信息，归一化成统一的岗位库。

输入（raw/snap/<日期>/ 下，全部快照都读）
  campus.sma-wiki.cn.html   静态页，数据在 `const RAW_DATA = [...]` 里
  niuqizp/ sasac/           牛企直聘列表页、国资委招聘栏目
  mianling/                 面灵AI 接口返回（岗位级，自称每天从官方站重抓）
  givemeoc/                 givemeoc 公开列表页（公告级，链接要登录，不碰）
  以及 sources/xixicc2027/jobs.json（GitHub 汇总仓库）

输出
  data/pool.json      公告级（一条 = 一个公司的一个招聘批次）
  data/all.json       岗位级全量（一条 = 公司 x 岗位名），**采集阶段不丢数据**
  data/matched.json   从 all.json 收紧出来的子集：命中方向 + 目标城市，tracker 用这个
  data/stats.json     各口径的计数，用来核对"到底有多少条"
  tracker/data.js     浏览器用的精简包（file:// 打开时 fetch 本地 json 会被拦）

原则：
  - 薪资、双休、加班一律不猜。脚本只写入 status = "待核实"，证据字段留空。
  - 计数分四种口径，不把"公告数"说成"岗位数"。
  - **采集要宽，筛选才收紧。** expand() 保留全部岗位，tighten() 才按方向和城市筛。
    以后改口径不用重抓，重跑一次就行。
"""
import json
import os
import re
import hashlib
import collections
import html as html_lib
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "raw")
DATA = os.path.join(ROOT, "data")
SOURCES = os.path.join(ROOT, "sources")

SNAP = os.path.join(RAW, "snap")   # raw/snap/<YYYY-MM-DD>/...，fetch_sources.py 按天写


def snapshots():
    """所有快照目录，新的在前。合并时先读到的优先，所以新数据覆盖旧数据。"""
    if not os.path.isdir(SNAP):
        return []
    days = sorted((d for d in os.listdir(SNAP) if os.path.isdir(os.path.join(SNAP, d))),
                  reverse=True)
    return [(d, os.path.join(SNAP, d)) for d in days]


LATEST = snapshots()[0][0] if snapshots() else date.today().isoformat()
FETCHED_ON = LATEST  # 给没有快照概念的来源（xixicc 是 git 仓库）用

# ---------------------------------------------------------------- 城市

# 目标城市：上海优先，其次北上广深，再次新一线
CITY_TIERS = {
    "上海": 0,
    "北京": 1, "深圳": 1, "广州": 1,
    "杭州": 2, "成都": 2,
}
CITY_ALIASES = {
    "上海市": "上海", "北京市": "北京", "深圳市": "深圳", "广州市": "广州",
    "杭州市": "杭州", "成都市": "成都", "沪": "上海", "京": "北京",
    "香港特别行政区": "香港", "澳门特别行政区": "澳门",
}
# 面灵AI 常只写区（「南山区」「武侯区」），不映射的话目标城市筛选会漏掉。
# 只收目标城市的区，而且只收全国唯一（或压倒性）的区名：朝阳区（长春也有）、通州区（南通）、
# 普陀区（舟山）、西湖区（南昌）、白云区（贵阳）、宝山区（双鸭山）、龙华区（海口）不收，不猜。
DISTRICT_CITY = {d: city for city, ds in {
    "上海": "浦东新区 黄浦区 徐汇区 长宁区 静安区 虹口区 杨浦区 闵行区 嘉定区 松江区 青浦区 奉贤区 崇明区 金山区",
    "北京": "东城区 西城区 丰台区 石景山区 海淀区 门头沟区 房山区 顺义区 昌平区 大兴区 怀柔区 平谷区 密云区 延庆区 亦庄开发区",
    "深圳": "福田区 罗湖区 南山区 盐田区 宝安区 龙岗区 坪山区 光明区",
    "广州": "越秀区 海珠区 荔湾区 天河区 黄埔区 番禺区 花都区 南沙区 从化区 增城区",
    "杭州": "上城区 拱墅区 滨江区 萧山区 余杭区 临平区 钱塘区 富阳区 临安区",
    "成都": "锦江区 青羊区 金牛区 武侯区 成华区 龙泉驿区 青白江区 新都区 温江区 双流区 郫都区 新津区 天府新区",
}.items() for d in ds.split()}
# 用户明确说的范围：北上广深 + 杭州成都这类新一线
TARGET_CITIES = {"上海", "北京", "深圳", "广州", "杭州", "成都"}


def norm_city(raw):
    c = (raw or "").strip()
    if not c:
        return None
    c = CITY_ALIASES.get(c, DISTRICT_CITY.get(c, c))
    if c.endswith("市"):
        c = c[:-1]
    return c or None


def parse_cities(raw):
    if not raw:
        return []
    out = []
    for p in re.split(r"[,，、/|\s]+", str(raw)):
        c = norm_city(p)
        if c and c not in out:
            out.append(c)
    return out


# ---------------------------------------------------------------- 企业性质

NATURE_MAP = {
    "外资企业": "外企", "外企": "外企", "中外合资": "外企",
    "合资企业": "外企", "台资企业": "外企", "国际组织": "外企",
    "国有企业": "国企", "中央企业": "央企", "央国企": "国企",
    "混合所有制": "国企", "农村商业银行": "国企",
    "事业单位": "事业单位", "政府机关": "事业单位",
    "民营企业": "民企", "民企": "民企", "私企": "民企",
    "外企/合资": "外企",                      # givemeoc 的写法
    "社会组织": "其他", "民办非企业": "其他", "社会机构": "其他", "": "未标注",
}
# 用户偏好：外企 / 国企央企 优先，大厂也可以
PREFERRED_NATURE = {"外企", "央企", "国企"}


def norm_nature(raw):
    return NATURE_MAP.get((raw or "").strip(), (raw or "").strip() or "未标注")


# ---------------------------------------------------------------- 批次 / 届别

def parse_batch(raw):
    """'27届秋招' -> (2027, '秋招')"""
    s = (raw or "").strip()
    m = re.search(r"(\d{2})\s*届", s)
    cohort = 2000 + int(m.group(1)) if m else None
    if not cohort:
        m2 = re.search(r"^(\d{2})(?=春招|秋招|提前批)", s)
        cohort = 2000 + int(m2.group(1)) if m2 else None
    # 各来源写法差别很大："27届秋招"、"校招,内推,宣讲会"、"26届校招及社招"…
    # 统一归到一小组值上，顺序即优先级
    if "实习" in s:
        kind = "实习"
    elif "提前批" in s:
        kind = "提前批"
    elif "补录" in s or "补招" in s:
        kind = "补录"
    elif "秋招" in s or "秋季" in s:
        kind = "秋招"
    elif "春招" in s or "春季" in s:
        kind = "春招"
    elif "校招" in s or "校园" in s:
        kind = "校招"          # 只说了校招，没分秋春
    elif "社招" in s:
        kind = "社招"
    elif "博士后" in s:
        kind = "博士后"
    else:
        kind = "未标注"
    return cohort, kind


# ---------------------------------------------------------------- 岗位方向

# 销售系（含技术销售 / toB / 售前 / 解决方案）
SALES_HIT = [
    "销售", "客户经理", "客户代表", "大客户", "客户成功", "客户拓展",
    "商务拓展", "商务经理", "商务专员", "渠道", "BD", "售前",
    "解决方案", "方案顾问", "行业顾问", "政企", "客户总监",
    "market development", "sales", "account manager", "presales",
]
SALES_BLOCK = ["销售支持文员", "销售内勤", "门店导购", "促销员", "电话客服"]

# AI Agent / 大模型应用开发
AI_HIT = [
    "大模型", "大语言模型", "LLM", "AIGC", "AI Agent", "Agent 开发", "智能体",
    "人工智能", "机器学习", "深度学习", "自然语言", "NLP", "算法工程师",
    "AI 应用", "AI应用", "AI 产品", "生成式", "多模态", "RAG", "prompt",
    "计算机视觉", "推荐算法", "搜索算法",
]
AI_BLOCK = ["信号算法", "控制算法"]

# 裸的 AI / Agent / LLM 不能用子串匹配 —— "Trainee" "Retail" "Maintenance" 里都有 ai。
# 用词边界正则单独判，别污染 AI_HIT 的子串匹配。
AI_TOKEN = re.compile(
    r"(?<![a-z])(ai|agent|llm|gpt|rag|nlp|cv|mllm|aigc)(?![a-z])", re.I)
AI_EXTRA = ["强化学习", "知识图谱", "数据挖掘", "语音识别", "图像识别",
            "智能算法", "算法研究", "模型训练", "推理引擎", "具身智能"]

# 管培生 / 管理培训生。轮岗性质，不要求对口专业，是补央国企机会的口子
MGMT_HIT = [
    "管培生", "管培", "管理培训生", "储备干部", "培训生",
    "management trainee", "储备人才",
]
# 我没有这些职能的专业和经历，投了也是浪费
MGMT_BLOCK = [
    "财务", "会计", "税务", "审计", "资金",
    "人力", "人资", "HR", "招聘", "薪酬",
    "法务", "合规", "风控", "党建", "纪检",
    "供应链", "采购", "仓储", "物流", "关务",
    "质量", "生产", "制造", "工艺", "设备", "安环", "安全",
    "行政", "后勤", "文秘",
    "教学", "教师", "护理", "药学", "临床",
    "零售", "门店", "餐饮", "厨", "客服", "导购",
    "游戏策划", "游戏发行", "设计", "商品", "美术", "翻译",
    # 用户明确说了：不是市场营销专业，也没有相关经验
    "营销", "市场",
]

# ── 两个更细的方向 ────────────────────────────────────────────────────────
# 我技术线主投 Agent，销售线主投销售工程师，所以这两个单独拎出来，
# 不跟宽泛的 "AI/大模型" 和 "销售系" 混在一起。它们是子集，会同时打两个标。

# Agent / 大模型应用。刻意不含纯 CV、语音、推荐——那些不是他要投的
AGENT_HIT = [
    "智能体", "多智能体", "大模型", "大语言模型", "AIGC", "生成式",
    "AI应用", "AI 应用", "大模型应用", "模型应用", "工作流引擎", "智能助手",
]
AGENT_TOKEN = re.compile(r"(?<![a-z])(agent|llm|rag|mcp|prompt|gpt)(?![a-z])", re.I)

# 销售工程师系：技术型销售岗，跟纯销售/客户经理分开
SALES_ENG_HIT = [
    "销售工程师", "技术销售", "售前", "解决方案工程师", "方案顾问",
    "技术支持工程师", "FAE", "现场应用工程师", "系统应用工程师",
    "应用工程师", "技术顾问", "解决方案架构",
]
# "AI应用工程师" 是算法岗，不是 FAE，别混进来
SALES_ENG_BLOCK = ["AI应用", "AI 应用", "算法应用", "大模型应用"]

# 跨境电商 / 外贸。我自己做过 Shopify 独立站，加上英语好 + 留学背景，
# 这条线的匹配度不比前两条低，而且留学生在这类岗上是加分项
TRADE_HIT = [
    "跨境", "外贸", "海外销售", "国际销售", "海外业务", "国际业务",
    "出海", "海外运营", "国际贸易", "外销", "海外市场", "海外电商",
    "独立站", "亚马逊运营", "shopify", "海外拓展", "国际商务",
    "海外客户", "外贸业务", "外贸跟单", "国际市场",
]
# 纯营销/单证/报关这类他没背景的，还是排掉
TRADE_BLOCK = ["单证", "报关", "关务", "货代", "船务"]

DIRECTIONS = {
    "sales": "销售系",
    "sales_eng": "销售工程师",
    "ai": "AI/大模型",
    "agent": "Agent方向",
    "trade": "跨境/外贸",
    "mgmt": "管培生",
}

# 纯市场营销岗一律不收。带销售/客户/商务/售前/解决方案的算销售系，保留。
MARKETING_MARK = ["营销", "市场营销", "市场推广", "品牌", "公关", "广告"]
SALES_EXEMPT = ["销售", "客户", "商务", "售前", "解决方案", "BD", "sales", "大客户"]


def _hit(text, needles):
    low = text.lower()
    return [n for n in needles if n.lower() in low]


def is_marketing_only(title):
    """名字里带营销/品牌/公关，又没有任何销售信号 —— 这就是市场营销岗，跳过。"""
    return bool(_hit(title, MARKETING_MARK)) and not _hit(title, SALES_EXEMPT)


def classify_position(title):
    """返回 (方向列表, 命中的关键词)。一个岗位可以同时属于多个方向。"""
    t = (title or "").strip()
    if is_marketing_only(t):
        return [], []
    dirs, kws = [], []
    if _hit(t, SALES_HIT) and not _hit(t, SALES_BLOCK):
        dirs.append("sales")
        kws += _hit(t, SALES_HIT)
    if _hit(t, SALES_ENG_HIT) and not _hit(t, SALES_ENG_BLOCK):
        dirs.append("sales_eng")
        kws += _hit(t, SALES_ENG_HIT)
        if "sales" not in dirs:          # 销售工程师也算销售系
            dirs.insert(0, "sales")
    ai_kws = _hit(t, AI_HIT) + _hit(t, AI_EXTRA)
    ai_kws += [m.group(0) for m in AI_TOKEN.finditer(t)]
    if ai_kws and not _hit(t, AI_BLOCK):
        dirs.append("ai")
        kws += ai_kws
    agent_kws = _hit(t, AGENT_HIT) + [m.group(0) for m in AGENT_TOKEN.finditer(t)]
    if agent_kws and not _hit(t, AI_BLOCK):
        dirs.append("agent")
        kws += agent_kws
        if "ai" not in dirs:             # Agent 也算 AI/大模型
            dirs.append("ai")
    if _hit(t, TRADE_HIT) and not _hit(t, TRADE_BLOCK):
        dirs.append("trade")
        kws += _hit(t, TRADE_HIT)
    if _hit(t, MGMT_HIT) and not _hit(t, MGMT_BLOCK):
        dirs.append("mgmt")
        kws += _hit(t, MGMT_HIT)
    return dirs, kws


# ---------------------------------------------------------------- 公司名清洗

TITLE_NOISE = re.compile(
    r"(?:20\d{2}\s*届|20\d{2}|\d{2}\s*届)?\s*"
    r"(?:全球|全国|海外|春季|秋季|夏季)?\s*"
    r"(?:校园招聘|校园招募|校招|秋季招聘|秋招|春季招聘|春招|提前批|正式批|补录|补招"
    r"|实习生招聘|实习招聘|实习生|实习|招聘公告|招聘启事|招聘简章|招聘)"
    r"(?:公告|启事|简章|正式启动|正式开启|全面启动|全面开启|火热进行中"
    r"|正式开始|启动|开启|开始|进行中|已开启)?\s*$"
)


# 开头的招聘年份前缀："2027届华沿机器人"、"2026年度飞博"、"2027 Sea 全球管培生计划"。
# 限定 2024–2030：华为的「2012实验室」是公司真名，剥掉会变成更糟的东西。
TITLE_YEAR_PREFIX = re.compile(r"^(?:20(?:2[4-9]|30))\s*(?:届|年度|年)?\s*")


def clean_company(raw):
    s = (raw or "").strip()
    prev = None
    while prev != s:
        prev = s
        s = TITLE_YEAR_PREFIX.sub("", s).strip()
        s = TITLE_NOISE.sub("", s).strip()
        s = re.sub(r"[（(]\s*[）)]$", "", s).strip()
        s = re.sub(r"[|｜\-—·:：]+$", "", s).strip()
        # "易方达基金20" 这类：批次年份被切掉一半后留下的孤立数字
        s = re.sub(r"(?<=[一-龥A-Za-z])\s*20\d{0,2}$", "", s).strip()
    return s or (raw or "").strip()


# ---------------------------------------------------------------- 岗位串清洗

POS_SPLIT = re.compile(r"[、，,;；|｜]+|\.{3,}|…+")
# 超过这个长度的多半不是一个岗位名，而是"招聘方向"整段
POS_GROUP_LEN = 18


def split_positions(raw_list):
    """把 '金融项目经理、评级项目经理、投研助理' 拆开；拆不动的后面会标成岗位组。"""
    out = []
    for item in raw_list:
        for p in POS_SPLIT.split(str(item)):
            p = p.strip().strip("【】[]()（）").strip()
            p = re.sub(r"^\d+[.、)]\s*", "", p)
            if not p or p in out:
                continue
            out.append(p)
    return out


def norm_key(s):
    return re.sub(r"[\s（）()·、,，\-—_]+", "", (s or "")).lower()


# 各站对同一个批次写法不一样："27届校招"（牛企直聘）＝"27届秋招"（campus）。
# 合并和去重都必须用归一化后的批次，否则同一个岗位会重复出现好几条。
# 但提前批、实习、春招、补录是**真正不同的申请轮次**，不能合并——那些可以都投。
MERGE_KIND = {"校招": "秋招", "正式批": "秋招", "未标注": "秋招"}


def merge_kind(kind):
    return MERGE_KIND.get(kind, kind)


def batch_bucket(cohort, kind):
    """合并/去重用的批次标识。'27届校招' 和 '2027届秋招' 会落到同一个桶。"""
    return "%s|%s" % (cohort or "?", merge_kind(kind))


def make_id(company, position, cohort, kind):
    """
    岗位级去重键：公司 + 岗位 + 归一化批次。
    同一公司的不同岗位算不同申请；同一岗位在提前批和秋招算不同申请（可以都投）；
    但同一岗位被两个站用不同措辞收录，算同一条。
    """
    h = hashlib.sha1(
        "|".join([norm_key(company), norm_key(position),
                  batch_bucket(cohort, kind)]).encode("utf-8")
    ).hexdigest()
    return h[:16]


# ---------------------------------------------------------------- 截止日期

def norm_deadline(raw):
    """'2026/10/11' -> '2026-10-11'；'招满为止' 不是日期，只留原文。"""
    s = (raw or "").strip()
    if not s:
        return "", ""
    m = re.match(r"^(20\d{2})[/\-.年](\d{1,2})[/\-.月](\d{1,2})", s)
    if m:
        y, mo, d = m.groups()
        return "%s-%02d-%02d" % (y, int(mo), int(d)), s
    return "", s


# ---------------------------------------------------------------- 证据骨架

CHECK_FIELDS = ["base_salary", "weekend", "overtime", "written_test", "overseas"]


def blank_evidence():
    """薪资 / 双休 / 加班 / 笔试 / 留学生：只留待核实的空位，绝不预填结论。"""
    return {k: {"status": "待核实", "value": None, "evidence": "",
                "source": "", "checked_at": ""} for k in CHECK_FIELDS}


# ---------------------------------------------------------------- 留学生信号

# 只认公告文字里真写了的，不靠"外企大概率收留学生"这种推断
OVERSEAS_KW = [
    "留学生", "海归", "海外院校", "境外院校", "境内外", "海内外",
    "海外高校", "留学回国", "归国", "全球校园招聘", "海外应届", "海外往届", "QS",
]


def scan_overseas(*texts):
    """在公告标题/简介里找留学生信号。返回命中的词，没有就空列表。"""
    blob = " ".join(t for t in texts if t)
    low = blob.lower()
    return [k for k in OVERSEAS_KW if k.lower() in low]


# ---------------------------------------------------------------- 来源 1：campus.sma-wiki.cn

def load_campus():
    """所有快照都读。从聚合站首页掉下去的旧公告靠旧快照留住（采集要宽）。"""
    out = []
    for day, d in snapshots():
        out += load_campus_one(d, day)
    return out


def load_campus_one(day_dir, day):
    path = os.path.join(day_dir, "campus.sma-wiki.cn.html")
    if not os.path.exists(path):
        return []
    html = open(path, encoding="utf-8", errors="replace").read()
    marker = "const RAW_DATA = "
    i = html.index(marker) + len(marker)
    data, _ = json.JSONDecoder().raw_decode(html, i)

    out = []
    for r in data:
        cohort, kind = parse_batch(r.get("batch"))
        out.append({
            "source": "campus.sma-wiki.cn",
            "source_url": "https://campus.sma-wiki.cn/",
            "fetched_on": day,
            "title": (r.get("company") or "").strip(),
            "company": clean_company(r.get("company")),
            "batch_raw": (r.get("batch") or "").strip(),
            "cohort": cohort,
            "batch_kind": kind,
            "industry": (r.get("industry") or "").strip(),
            "nature": norm_nature(r.get("nature")),
            "nature_raw": (r.get("nature") or "").strip(),
            "cities": parse_cities(r.get("location")),
            "positions": split_positions((r.get("positions") or "").split(",")),
            "apply_url": (r.get("appLink") or "").strip(),
            "notice_url": (r.get("sourceLink") or "").strip(),
            "deadline": (r.get("deadline") or "").strip(),
            "posted_on": (r.get("fullDate") or "").strip(),
            "blurb": (r.get("evaluation") or "").strip(),
            "is_ad": bool(r.get("isCommercial")),
        })
    return out


# ---------------------------------------------------------------- 来源 2：xixicc2027

def load_xixicc():
    path = os.path.join(SOURCES, "xixicc2027", "jobs.json")
    if not os.path.exists(path):
        return []
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for r in data:
        batch_raw = " ".join(x for x in [r.get("cohort"), r.get("batch")] if x).strip()
        cohort, kind = parse_batch(batch_raw)
        cities = []
        for c in (r.get("locations") or []):
            c = norm_city(c)
            if c and c not in cities:
                cities.append(c)
        out.append({
            "source": "github/xixicc2027",
            "source_url": "https://github.com/xixicc186/xixicc2027",
            "fetched_on": FETCHED_ON,
            "title": (r.get("company") or "").strip(),
            "company": clean_company(r.get("company")),
            "batch_raw": batch_raw,
            "cohort": cohort,
            "batch_kind": kind,
            "industry": (r.get("industry") or "").strip(),
            "nature": "未标注",
            "nature_raw": "",
            "cities": cities,
            "positions": split_positions(r.get("positions") or []),
            "apply_url": (r.get("apply_url") or "") or "",
            "notice_url": "",
            "deadline": (r.get("deadline") or "").strip(),
            "posted_on": (r.get("first_seen") or "").strip(),
            "blurb": "",
            "is_ad": False,
        })
    return out


# ---------------------------------------------------------------- 来源 3：牛企直聘

NQ_CARD = re.compile(r'<article class="schedule-card">(.*?)</article>', re.S)


def _nq_field(card, cls, tag="span"):
    m = re.search(r'class="%s"[^>]*>(.*?)</%s>' % (cls, tag), card, re.S)
    if not m:
        return ""
    t = re.sub(r"<svg.*?</svg>", "", m.group(1), flags=re.S)
    t = re.sub(r"<\?xml.*?\?>", "", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _nq_positions(card):
    """
    岗位和专业混在一个容器里，中间用一个空的 job-postion span 隔开：
      岗位A, 岗位B, <空>, 电气工程, 计算机…（后半段是专业不是岗位）
    只取空 span 之前的部分。
    """
    m = re.search(r'class="schedule-card-positions"[^>]*>(.*?)</div>', card, re.S)
    if not m:
        return []
    spans = re.findall(r'<span class="job-postion">(.*?)</span>', m.group(1), re.S)
    out = []
    for s in spans:
        s = re.sub(r"<[^>]+>", "", s).strip()
        if not s:              # 空 span = 岗位段结束，后面是专业
            break
        out.append(s)
    return out


def load_niuqizp():
    out = []
    for day, d in snapshots():
        out += load_niuqizp_one(d, day)
    return out


def load_niuqizp_one(day_dir, day):
    d = os.path.join(day_dir, "niuqizp")
    if not os.path.isdir(d):
        return []
    out = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".html"):
            continue
        html = open(os.path.join(d, fn), encoding="utf-8", errors="replace").read()
        for card in NQ_CARD.findall(html):
            m = re.search(r'class="schedule-card-company"[^>]*>\s*<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                          card, re.S)
            if not m:
                continue
            company_batch = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
            # "凯发电气 27届秋招" -> 公司名 + 批次
            bm = re.search(r"\s(\d{2}届\S*|\S*(?:秋招|春招|实习|提前批|补录)\S*)$", company_batch)
            if bm:
                company = company_batch[:bm.start()].strip()
                batch_raw = bm.group(1)
            else:
                company, batch_raw = company_batch, ""

            tm = re.search(r'class="schedule-card-title"[^>]*>\s*<a[^>]*href="([^"]*)"', card)
            notice = "https://campus.niuqizp.com" + tm.group(1) if tm else ""
            title = _nq_field(card, "schedule-card-title", "h4")
            title = re.sub(r"\s*荐\s*$", "", title).strip()

            # "2026-09-06 ~ 2026-11-05"
            window = _nq_field(card, "schedule-card-date")
            wm = re.match(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", window)
            opens, closes = (wm.group(1), wm.group(2)) if wm else ("", "")

            cohort, kind = parse_batch(batch_raw)
            tag = _nq_field(card, "schedule-card-tags")
            if kind in ("未标注", "") and tag:
                cohort2, kind = parse_batch(tag)
                cohort = cohort or cohort2

            out.append({
                "source": "campus.niuqizp.com",
                "source_url": "https://campus.niuqizp.com/",
                "fetched_on": day,
                "title": title or company_batch,
                "company": clean_company(company),
                "batch_raw": batch_raw or tag,
                "cohort": cohort,
                "batch_kind": kind,
                "industry": "",
                "nature": "未标注",
                "nature_raw": "",
                "cities": parse_cities(_nq_field(card, "schedule-card-location")),
                "positions": split_positions(_nq_positions(card)),
                "apply_url": "",
                "notice_url": notice,
                "deadline": closes,
                "opens_on": opens,
                "posted_on": _nq_field(card, "schedule-card-post-date"),
                "blurb": "",
                "is_ad": False,
            })
    return out


# ---------------------------------------------------------------- 来源 4：国资委

SASAC_ARTICLE = "http://www.sasac.gov.cn/n2588035/n2588325/n2588350/c%s/content.html"


def load_sasac():
    """
    国资委招聘栏目只挂最近十几条，而且 WAP 版正文是空的（拿不到岗位和投递链接）。
    所以它在这里的作用不是补岗位，而是**权威确认哪些公司是央企、且 2027 届校招已开**，
    用来纠正其他来源里 nature = 未标注 的记录。
    """
    out = []
    for day, d in snapshots():
        out += load_sasac_one(d, day)
    return out


def load_sasac_one(day_dir, day):
    d = os.path.join(day_dir, "sasac")
    idx = os.path.join(d, "index.html")
    if not os.path.exists(idx):
        return []
    html = open(idx, encoding="utf-8", errors="replace").read()
    out = []
    for m in re.finditer(r'<a[^>]*href="[^"]*c(\d+)/content\.html"[^>]*>(.*?)</a>', html, re.S):
        cid = m.group(1)
        title = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).strip()
        if not title or not re.search(r"校园招聘|校招|届|毕业生|招募", title):
            continue
        cohort, kind = parse_batch(title)
        out.append({
            "source": "sasac.gov.cn",
            "source_url": "http://www.sasac.gov.cn/n2588035/n2588325/n2588350/index.html",
            "fetched_on": day,
            "title": title,
            "company": clean_company(title),
            "batch_raw": ("%d届%s" % (cohort, kind)) if cohort else kind,
            "cohort": cohort,
            "batch_kind": kind,
            "industry": "",
            "nature": "央企",          # 这个栏目登的就是央企/委属单位，来源本身即证据
            "nature_raw": "国资委招聘栏目",
            "cities": [],
            "positions": [],           # WAP 正文为空，岗位得点进官方公告看
            "apply_url": "",
            "notice_url": SASAC_ARTICLE % cid,
            "deadline": "",
            "posted_on": "",
            "blurb": "",
            "is_ad": False,
        })
    return out


# ---------------------------------------------------------------- 来源 5：面灵AI

ML_PLATFORM = {"beisen": "北森", "moka": "Moka", "feishu-hire": "飞书招聘", "tencent": "腾讯招聘",
               "netease": "网易招聘", "meituan": "美团招聘", "jd": "京东招聘", "workday": "Workday"}
# 标题里的岗位编号和届别括注："算法设计师(J11101)"、"（2027届校招）金融科技岗"、"2027届-智能体开发工程师"
ML_JOB_CODE = re.compile(r"\s*[（(]\s*[A-Za-z]{0,4}[-_]?\d{3,}\s*[)）]\s*$")
ML_COHORT_GROUP = re.compile(
    r"[【\[（(][^】\]）)]{0,14}?(?:届|秋招|春招|校招|应届|提前批|正式批)[^】\]）)]{0,14}?[】\]）)]")
ML_COHORT_PREFIX = re.compile(r"^(?:20)?\d{2}\s*届\s*[-—－_:：]?\s*")


def clean_ml_title(t):
    s = (t or "").strip()
    s = ML_JOB_CODE.sub("", s)
    s = ML_COHORT_GROUP.sub("", s)
    s = ML_COHORT_PREFIX.sub("", s)
    return re.sub(r"\s+", " ", s).strip(" -—－_:：") or (t or "").strip()


def ml_date(iso):
    """
    面灵AI 的时间是 UTC。'2026-10-11T16:00:00Z' 是北京时间 10-12 零点——
    这通常是「截至 10-11 24:00」，所以正好零点的往前算一天：宁可早报一天，不能晚报。
    """
    if not iso:
        return ""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(hours=8)
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        dt -= timedelta(seconds=1)
    return dt.date().isoformat()


def load_mianling():
    out = []
    for day, d in snapshots():
        out += load_mianling_one(d, day)
    return out


def load_mianling_one(day_dir, day):
    """
    面灵AI（mianlingai.com）。站点自述：2027届秋招/校招/实习汇总，每天从各公司官方校招站全量重抓，
    源站撤下的岗位标成已关闭；抓的时候只要了在招的。一条就是一个岗位。
    投递链接要登录才给（applyUrlLocked），这里没有 apply_url，只记官方用的招聘系统。
    企业性质只在「外企/国企/央企」筛选里查到时才标（面灵自己的分类），按省份查到的不标。
    届别：标题/批次里写了就用写的；没写就是站点口径的 2027 届。
    """
    d = os.path.join(day_dir, "mianling")
    if not os.path.isdir(d):
        return []
    items, org = {}, {}
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".json"):
            continue
        blob = json.load(open(os.path.join(d, fn), encoding="utf-8"))
        ot = blob["query"].get("orgType")
        for it in blob["items"]:
            if not it.get("active", True):
                continue
            items[it["id"]] = it
            if ot:
                org[it["id"]] = ot

    out = []
    for iid, it in items.items():
        title = (it.get("title") or "").strip()
        batch_raw = " ".join(x for x in [it.get("batch"), it.get("employmentType")] if x)
        cohort, kind = parse_batch(batch_raw + " " + title)
        if it.get("employmentType") == "实习":
            kind = "实习"
        elif kind == "未标注":
            kind = "校招"
        cities = []
        for c in it.get("cities") or []:
            c = norm_city(c)
            if c and c not in cities:
                cities.append(c)
        blurb = ["面灵AI %s 从官方站重抓时在招" % ((it.get("lastSeenAt") or "")[:10] or day)]
        if it.get("platform"):
            blurb.append("官方系统：" + ML_PLATFORM.get(it["platform"], it["platform"]))
        if it.get("companyTags"):
            blurb.append("、".join(it["companyTags"]))
        out.append({
            "source": "mianlingai.com",
            "source_url": "https://www.mianlingai.com/",
            "fetched_on": day,
            "title": title,
            "company": clean_company(it.get("company")),
            "batch_raw": batch_raw,
            "cohort": cohort or 2027,
            "batch_kind": kind,
            "industry": "",
            "nature": norm_nature(org.get(iid)),
            "nature_raw": ("面灵AI 筛选：" + org[iid]) if iid in org else "",
            "cities": cities,
            "positions": [clean_ml_title(title)],
            "apply_url": "",
            "notice_url": "",
            "deadline": ml_date(it.get("deadline")),
            "posted_on": ml_date(it.get("publishedAt")),
            "blurb": "；".join(blurb),
            "is_ad": False,
        })
    return out


# ---------------------------------------------------------------- 来源 6：givemeoc

GM_ROW = re.compile(r'<tr data-id="(\d+)">(.*?)</tr>', re.S)

# 「岗位」列是岗位和专业混写的（表头就叫「专业/岗位」）：
#   '设计研发岗,工艺技术岗,…电机与电器技术、机械电子工程'
# 专业拆出来会变成「中国移动 | 人工智能」这种假岗位。像专业名、又不带任何岗位字眼的片段丢掉。
GM_MAJOR_TAIL = re.compile(
    r"(?:工程|技术|科学|学|专业|管理|营销|金融|财会|会计|法律|经济|贸易|物流|统计|数学|通信"
    r"|电子|计算机|自动化|商务|英语|日语|信息|人工智能|大数据|机械|电气|化工|材料|土木|建筑"
    r"|水利|交通|能源|农业|生态|医学|药学|设计类|相关)$")
GM_POS_WORD = re.compile(
    r"岗|师|员|经理|专员|主管|顾问|代表|助理|总监|负责人|开发|运营|销售|实习|储备|培训|管培|生$"
    r"|方向|项目|计划|BD|sales|engineer|manager|trainee|intern", re.I)


def _gm_cell(row, col):
    m = re.search(r'<td class="crt-col-%s">(.*?)</td>' % col, row, re.S)
    if not m:
        return ""
    s = m.group(1)
    t = re.search(r'data-tooltip="([^"]*)"', s)   # 地点太长时，完整列表在 tooltip 里
    if t:
        return html_lib.unescape(t.group(1)).strip()
    return re.sub(r"\s+", " ", html_lib.unescape(re.sub(r"<[^>]+>", "", s))).strip()


def gm_positions(raw):
    return [p for p in split_positions([raw])
            if not (GM_MAJOR_TAIL.search(p) and not GM_POS_WORD.search(p))]


def gm_city_vocab(cells):
    """本站用逗号分开写的那些地点里出现过 2 次以上的地名，拿来切没分隔符的地点。"""
    cnt = collections.Counter()
    for cell in cells:
        cs = parse_cities(cell)
        if len(cs) > 1:
            cnt.update(c for c in cs if 2 <= len(c) <= 4)
    return {c for c, n in cnt.items() if n >= 2} | TARGET_CITIES


def gm_cities(cell, vocab):
    """'深圳杭州厦门上海成都珠海'（源站原样如此）-> 按地名表从左往右、长的优先切开。切不出就原样留着。"""
    out = []
    for c in parse_cities(cell):
        got = []
        if len(c) >= 4 and c not in vocab:
            i = 0
            while i < len(c):
                for n in (4, 3, 2):
                    if c[i:i + n] in vocab:
                        got.append(c[i:i + n])
                        i += n
                        break
                else:
                    i += 1
        for x in (got or [c]):
            x = norm_city(x)
            if x and x not in out:
                out.append(x)
    return out


def load_givemeoc():
    out = []
    for day, d in snapshots():
        out += load_givemeoc_one(d, day)
    return out


def load_givemeoc_one(day_dir, day):
    """
    givemeoc 公开列表页，公告级（一行 = 一个公司的一个批次）。
    投递链接、招聘公告要登录，笔试情况、公司规模要会员，都不碰；所以这里没有任何链接。
    招聘对象常写多届（'2026,2027届'）：包含 2027 就记成 2027 届，好和别的来源合并。
    """
    d = os.path.join(day_dir, "givemeoc")
    if not os.path.isdir(d):
        return []
    rows = collections.OrderedDict()          # 站点超出末页会重复回最后一页，按 data-id 去重
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".html"):
            page = open(os.path.join(d, fn), encoding="utf-8", errors="replace").read()
            for m in GM_ROW.finditer(page):
                rows.setdefault(m.group(1), m.group(2))
    vocab = gm_city_vocab(_gm_cell(row, "location") for row in rows.values())

    out = []
    for row in rows.values():
        company = _gm_cell(row, "company")
        if not company:
            continue
        typ, industry = _gm_cell(row, "type"), _gm_cell(row, "industry")
        target, rtype = _gm_cell(row, "target"), _gm_cell(row, "recruitment-type")
        years = sorted({2000 + int(y) for y in re.findall(r"20(2\d)", target)})
        cohort = 2027 if 2027 in years else (years[-1] if years else None)
        if typ == "银行":
            # 银行有国有也有股份制，只在行业栏也写了「国企」时才标国企
            nature = "国企" if "国企" in industry else "未标注"
        else:
            nature = norm_nature(typ)
        notes = _gm_cell(row, "notes")
        out.append({
            "source": "givemeoc.com",
            "source_url": "https://www.givemeoc.com/",
            "fetched_on": day,
            "title": company,
            "company": clean_company(company),
            "batch_raw": ("%s %s" % (target, rtype)).strip(),
            "cohort": cohort,
            "batch_kind": parse_batch(rtype)[1],
            "industry": industry,
            "nature": nature,
            "nature_raw": ("givemeoc：" + typ) if typ else "",
            "cities": gm_cities(_gm_cell(row, "location"), vocab),
            "positions": gm_positions(_gm_cell(row, "position")),
            "apply_url": "",
            "notice_url": "",
            "deadline": _gm_cell(row, "deadline"),
            "posted_on": "",
            "blurb": "" if notes in ("", "-") else "givemeoc 备注：" + notes,
            "is_ad": False,
        })
    return out


# ---------------------------------------------------------------- 合并 / 展开

def merge_pool(records):
    """同一公司 + 同一批次，多来源合并成一条公告。"""
    buckets = collections.OrderedDict()
    for r in records:
        k = (norm_key(r["company"]), batch_bucket(r["cohort"], r["batch_kind"]))
        if k not in buckets:
            r = dict(r)
            r["sources"] = [r["source"]]
            buckets[k] = r
            continue
        cur = buckets[k]
        if r["source"] not in cur["sources"]:
            cur["sources"].append(r["source"])
        for c in r["cities"]:
            if c not in cur["cities"]:
                cur["cities"].append(c)
        for p in r["positions"]:
            if p not in cur["positions"]:
                cur["positions"].append(p)
        for f in ("apply_url", "notice_url", "industry", "blurb",
                  "opens_on", "posted_on"):
            if not cur.get(f) and r.get(f):
                cur[f] = r[f]
        # 截止日期取最晚的一个，并记下所有见过的说法
        seen_dl = cur.setdefault("deadline_seen", [])
        for d in (cur.get("deadline"), r.get("deadline")):
            if d and d not in seen_dl:
                seen_dl.append(d)
        a, b = norm_deadline(cur.get("deadline"))[0], norm_deadline(r.get("deadline"))[0]
        if b > a:
            cur["deadline"] = r["deadline"]
        elif not cur.get("deadline") and r.get("deadline"):
            cur["deadline"] = r["deadline"]
        # 批次原文保留信息量最大的那个（带"届"、且更长的）
        if ("届" in r["batch_raw"]) and ("届" not in cur["batch_raw"] or
                                         len(r["batch_raw"]) > len(cur["batch_raw"])):
            cur["batch_raw"] = r["batch_raw"]
        if cur["nature"] == "未标注" and r["nature"] != "未标注":
            cur["nature"] = r["nature"]
            cur["nature_raw"] = r["nature_raw"]
    return list(buckets.values())


def absorb_unknown_cohort(pool):
    """
    有的公告批次只写了"提前批"没写届别，会和同一家的"2027届 提前批"分成两条。
    如果这家公司在这个批次类型下只有一个已知届别，就把届别未知的那条并进去。
    有多个已知届别时不动 —— 猜错届别比留个重复更糟。
    """
    known = collections.defaultdict(set)
    for r in pool:
        if r["cohort"]:
            known[(norm_key(r["company"]), merge_kind(r["batch_kind"]))].add(r["cohort"])

    merged, out = 0, []
    index = {}
    for r in pool:
        if not r["cohort"]:
            cands = known.get((norm_key(r["company"]), merge_kind(r["batch_kind"])))
            if cands and len(cands) == 1:
                r["cohort"] = next(iter(cands))
                r["cohort_inferred"] = True
                merged += 1
        k = (norm_key(r["company"]), batch_bucket(r["cohort"], r["batch_kind"]))
        if k in index:
            cur = index[k]
            for p in r["positions"]:
                if p not in cur["positions"]:
                    cur["positions"].append(p)
            for c in r["cities"]:
                if c not in cur["cities"]:
                    cur["cities"].append(c)
            for s in r["sources"]:
                if s not in cur["sources"]:
                    cur["sources"].append(s)
            for f in ("apply_url", "notice_url", "deadline", "industry", "blurb"):
                if not cur.get(f) and r.get(f):
                    cur[f] = r[f]
            continue
        index[k] = r
        out.append(r)
    return out, merged


def backfill_nature(pool):
    """
    同一家公司在别的批次里已经标了性质，就把「未标注」的那条补上。
    依据是同名公司的另一条公告，不是猜的；冲突时不补。
    """
    by_company = collections.defaultdict(set)
    for r in pool:
        if r["nature"] not in ("未标注", ""):
            by_company[norm_key(r["company"])].add(r["nature"])
    filled = 0
    for r in pool:
        if r["nature"] in ("未标注", ""):
            cands = by_company.get(norm_key(r["company"]))
            if cands and len(cands) == 1:
                r["nature"] = next(iter(cands))
                r["nature_raw"] = "同公司其它公告标注"
                filled += 1
    return filled


def mark_central(pool, sasac):
    """
    国资委栏目登过的公司，就是央企——这是来源本身构成的证据，不是推断。
    只用来把别处标成"未标注/民企以外"的记录纠正过来，不覆盖已有的明确标注。
    """
    names = {norm_key(r["company"]) for r in sasac if r["company"]}
    names = {n for n in names if len(n) >= 3}
    fixed = 0
    for r in pool:
        if r["nature"] in ("未标注", "") and norm_key(r["company"]) in names:
            r["nature"] = "央企"
            r["nature_raw"] = "国资委招聘栏目"
            fixed += 1
    return fixed


def expand(pool):
    """
    公告 -> 岗位级行。**采集阶段不丢数据**：每一条岗位都留下，方向和城市只做标记。

    收紧发生在下游：
      - `data/all.json`   全量，scripts/shortlist.py 需要放宽条件时查这个
      - `data/matched.json` + `tracker/data.js` 才按方向和城市筛

    这样以后改筛选口径（比如再加一个方向）不用重抓数据，重跑一次就行。
    """
    rows = []
    for r in pool:
        target_cities = [c for c in r["cities"] if c in TARGET_CITIES]
        city_unknown = not r["cities"]
        deadline_iso, deadline_raw = norm_deadline(r["deadline"])
        tier = min([CITY_TIERS.get(c, 9) for c in target_cities], default=9)
        overseas_kw = scan_overseas(r["title"], r["blurb"], r["batch_raw"])
        for pos in r["positions"]:
            dirs, kws = classify_position(pos)
            checks = blank_evidence()
            if overseas_kw:
                # 公告文字里真写了"全球校园招聘""海内外"这类，才敢标疑似；
                # 具体能不能投还是要看公司公告写的毕业时间窗口
                checks["overseas"]["status"] = "疑似"
                checks["overseas"]["evidence"] = "公告标题/简介出现：" + "、".join(overseas_kw)
                checks["overseas"]["source"] = r["notice_url"] or r["apply_url"] or r["source_url"]
            rows.append({
                "id": make_id(r["company"], pos, r["cohort"], r["batch_kind"]),
                "company": r["company"],
                "position": pos,
                "position_is_group": len(pos) > POS_GROUP_LEN,
                "directions": dirs,
                "direction_labels": [DIRECTIONS[d] for d in dirs],
                "match_keywords": sorted(set(kws)),
                "batch": r["batch_raw"],
                "cohort": r["cohort"],
                "batch_kind": r["batch_kind"],
                "nature": r["nature"],
                "preferred_nature": r["nature"] in PREFERRED_NATURE,
                "industry": r["industry"],
                "cities": target_cities,
                "in_target_city": bool(target_cities),
                "city_status": "待核实" if city_unknown else "已知",
                "all_cities": r["cities"],
                "city_tier": tier,
                "apply_url": r["apply_url"],
                "notice_url": r["notice_url"],
                "deadline": deadline_iso,
                "deadline_raw": deadline_raw,
                # 只有两个不同的**日期**才算冲突；'招满为止' 对上一个日期、'2026/10/11' 对
                # '2026-10-11' 都不算（givemeoc 大部分写的是招满为止）
                "deadline_conflict": len({norm_deadline(x)[0] for x in r.get("deadline_seen", [])
                                          if norm_deadline(x)[0]}) > 1,
                "deadline_seen": r.get("deadline_seen", []),
                "opens_on": r.get("opens_on", ""),
                "overseas_keywords": overseas_kw,
                "posted_on": r["posted_on"],
                "blurb": r["blurb"],
                "sources": r["sources"],
                "source_url": r["source_url"],
                "fetched_on": r["fetched_on"],
                "is_ad": r["is_ad"],
                "checks": checks,
                # 投递侧字段，由 tracker 写回
                "status": "not_sent",
                "resume_version": "",
                "applied_at": "",
                "notes": "",
            })
    seen, out = set(), []
    for row in rows:
        if row["id"] in seen:
            continue
        seen.add(row["id"])
        out.append(row)
    # 排序：城市优先级 -> 企业性质偏好 -> 截止日期近的在前
    out.sort(key=lambda x: (
        x["city_tier"],
        0 if x["preferred_nature"] else 1,
        x["deadline"] or "9999-99-99",
        x["company"],
    ))
    return out


# 过于偏云的销售 / 售前岗（2026-09-15 我决定不投）。
# 卖云产品要懂云架构，云厂商的解决方案架构师还考编码和系统设计，
# 和他「技术销售 / 解决方案销售 / 售前技术支持」的定位不符。
#
# 两种判法：标题本身就是云；或者岗位在云厂商、做的是销售 / 售前。
# 只对销售方向生效 —— 云厂商的 AI / Agent 研发岗是 AI 岗，照样保留。
# 注意别用裸的「云」：会误伤云南、云迹（机器人）、诺瓦星云（LED）、网易云音乐。
CLOUD_TITLE = re.compile(
    r"云计算|云架构|云原生|云解决方案|云产品|云服务|云平台|云安全|云网络|云存储|云运维"
    r"|云技术|公有云|私有云|混合云|云销售|云客户|云业务|云方案|云售前|云和大模型"
    r"|(?<![a-z])cloud(?![a-z])", re.I)
CLOUD_VENDOR = re.compile(
    r"阿里云|腾讯云|华为云|百度智能云|金山云|天翼云|移动云|联通云|京东云|火山引擎"
    r"|优刻得|UCloud|青云|七牛|浪潮云|紫光云|曙光云|噢易云|亚马逊|AWS|Azure|微软云"
    r"|谷歌云|Google Cloud", re.I)


def is_cloud_sales(r):
    if not set(r["directions"]) & {"sales", "sales_eng"}:
        return False
    return bool(CLOUD_TITLE.search(r["position"]) or CLOUD_VENDOR.search(r["company"]))


def tighten(rows):
    """
    收紧发生在这里，不在采集阶段。
    条件：命中至少一个方向，且城市在目标范围内（城市没写的先留着，标了待核实）；
    过于偏云的销售 / 售前岗排除（all.json 里还在，改主意了删掉这一条就能找回来）。
    """
    out = [r for r in rows
           if r["directions"] and (r["in_target_city"] or r["city_status"] == "待核实")
           and not is_cloud_sales(r)]
    out.sort(key=lambda x: (
        x["city_tier"],
        0 if x["preferred_nature"] else 1,
        x["deadline"] or "9999-99-99",
        x["company"],
    ))
    return out


# ---------------------------------------------------------------- 浏览器包

SLIM_KEYS = [
    ("i", "id"), ("c", "company"), ("p", "position"), ("d", "directions"),
    ("b", "batch"), ("co", "cohort"), ("k", "batch_kind"), ("n", "nature"),
    ("ind", "industry"), ("ct", "cities"), ("t", "city_tier"),
    ("dl", "deadline"), ("dlr", "deadline_raw"), ("ps", "posted_on"),
    ("au", "apply_url"), ("nu", "notice_url"), ("bl", "blurb"),
    ("g", "position_is_group"), ("cs", "city_status"), ("src", "sources"),
    ("f", "fetched_on"), ("op", "opens_on"), ("ov", "overseas_keywords"),
    ("dc", "deadline_conflict"), ("ds", "deadline_seen"),
    ("fs", "first_seen"), ("nw", "is_new"),
]


def write_browser_bundle(matched, stats, aliases=None):
    """tracker 用 file:// 打开时 fetch 本地 json 会被拦，所以打成 js 直接 script 引入。"""
    slim = []
    for r in matched:
        o = {}
        for short, full in SLIM_KEYS:
            v = r.get(full)
            if v is None or v == "" or v is False or v == []:
                continue
            o[short] = v
        slim.append(o)
    out = os.path.join(ROOT, "tracker", "data.js")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        print("// 由 scripts/build_dataset.py 生成，不要手改", file=f)
        print("window.JOB_POOL_META = %s;" % json.dumps(stats, ensure_ascii=False), file=f)
        print("window.JOB_POOL_KEYS = %s;" % json.dumps(dict(SLIM_KEYS), ensure_ascii=False), file=f)
        print("window.JOB_POOL_ALIASES = %s;" % json.dumps(aliases or {}), file=f)
        print("window.JOB_POOL = %s;" % json.dumps(slim, ensure_ascii=False,
                                                   separators=(",", ":")), file=f)
    return out


# ---------------------------------------------------------------- 首次出现登记

FIRST_SEEN = os.path.join(DATA, "first_seen.json")


def stamp_first_seen(matched):
    """
    每条岗位第一次进入岗位库的日期，跨构建持久化在 data/first_seen.json。
    这样每次更新后能看出「这次新增了什么」——不然重跑一遍，用户分不清哪些是新的。

    第一次建登记表时，用上一版 matched.json 里已有的 id 做种子（记成上一次的数据日期），
    免得首次运行把几千条全标成「新」。
    """
    reg = {}
    if os.path.exists(FIRST_SEEN):
        reg = json.load(open(FIRST_SEEN, encoding="utf-8"))
    else:
        prev = os.path.join(DATA, "matched.json")
        if os.path.exists(prev):
            prev_rows = json.load(open(prev, encoding="utf-8"))
            prev_day = min((r.get("fetched_on") or LATEST) for r in prev_rows) if prev_rows else LATEST
            reg = {r["id"]: prev_day for r in prev_rows}
    had_history = bool(reg)
    current = {r["id"] for r in matched}
    aliases = {}
    for r in matched:
        for a in alt_ids(r):
            if a in reg and a not in current:
                aliases[a] = r["id"]
                if r["id"] not in reg:
                    reg[r["id"]] = reg[a]      # 同一条岗位换了 id，不算新增
    new = 0
    for r in matched:
        if r["id"] not in reg:
            reg[r["id"]] = LATEST
        r["first_seen"] = reg[r["id"]]
        r["is_new"] = had_history and reg[r["id"]] == LATEST
        new += r["is_new"]
    json.dump(reg, open(FIRST_SEEN, "w", encoding="utf-8"), ensure_ascii=False)
    return new, aliases


def alt_ids(r):
    """
    这条岗位在「届别未知」时用过的 id。有的公告起初没写届别，后来别的来源补上了，
    absorb_unknown_cohort 把它并进已知届别以后 id 就变了（届别在 id 里）。
    tracker 靠 旧 id -> 新 id 这张表把用户已经标过的投递记录搬过来，不然会丢。
    """
    if r["cohort"] is None:
        return []
    return [make_id(r["company"], r["position"], None, r["batch_kind"])]


def bust_cache(bundle_path):
    """
    给 tracker/index.html 里的 data.js 换一个新的版本号。
    不加这个，浏览器会一直用缓存里的旧岗位库——重跑完脚本刷新页面看到的还是旧数据，
    很容易误以为脚本没生效。版本号用 data.js 的内容长度，变了才会变。
    """
    idx = os.path.join(ROOT, "tracker", "index.html")
    if not os.path.exists(idx):
        return
    ver = str(os.path.getsize(bundle_path))
    html = open(idx, encoding="utf-8").read()
    new = re.sub(r'src="data\.js(?:\?v=\d+)?"', 'src="data.js?v=%s"' % ver, html)
    if new != html:
        with open(idx, "w", encoding="utf-8") as f:
            f.write(new)


def main():
    campus = load_campus()
    xixicc = load_xixicc()
    niuqizp = load_niuqizp()
    sasac = load_sasac()
    mianling = load_mianling()
    givemeoc = load_givemeoc()
    # 新来源排在后面：merge_pool 里链接、简介这些字段是先到先得，老来源有链接，新来源没有
    raw_all = campus + xixicc + niuqizp + sasac + mianling + givemeoc
    pool = merge_pool(raw_all)
    pool, cohort_absorbed = absorb_unknown_cohort(pool)
    central_fixed = mark_central(pool, sasac)
    nature_filled = backfill_nature(pool)
    all_rows = expand(pool)
    matched = tighten(all_rows)
    new_rows, aliases = stamp_first_seen(matched)

    os.makedirs(DATA, exist_ok=True)
    json.dump(pool, open(os.path.join(DATA, "pool.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump(all_rows, open(os.path.join(DATA, "all.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    json.dump(matched, open(os.path.join(DATA, "matched.json"), "w", encoding="utf-8"),
              ensure_ascii=False)

    stats = {
        "built_on": date.today().isoformat(),
        "sources": {
            "campus.sma-wiki.cn": {"announcements": len(campus), "fetched_on": FETCHED_ON},
            "github/xixicc2027": {"announcements": len(xixicc), "fetched_on": FETCHED_ON},
            "campus.niuqizp.com": {"announcements": len(niuqizp), "fetched_on": FETCHED_ON},
            "sasac.gov.cn": {"announcements": len(sasac), "fetched_on": FETCHED_ON,
                             "note": "官方央企公告；WAP 正文为空，只用于确认央企身份，不提供岗位"},
            "mianlingai.com": {"positions": len(mianling), "fetched_on": FETCHED_ON,
                               "note": "岗位级；不登录每个筛选只能看第 1 页；投递链接要登录，没有"},
            "givemeoc.com": {"announcements": len(givemeoc), "fetched_on": FETCHED_ON,
                             "note": "公告级；链接要登录，没有；岗位列混着专业，专业已剔除"},
        },
        "nature_fixed_by_sasac": central_fixed,
        "unknown_cohort_absorbed": cohort_absorbed,
        "nature_backfilled_by_company": nature_filled,
        "announcements_raw": len(raw_all),
        "announcements_deduped": len(pool),
        "companies_deduped": len({norm_key(r["company"]) for r in pool}),
        "position_strings_total": sum(len(r["positions"]) for r in pool),
        "all_rows": len(all_rows),
        "all_companies": len({norm_key(r["company"]) for r in all_rows}),
        "matched_rows": len(matched),
        "new_since_last": new_rows,
        "id_aliases": len(aliases),
        "snapshots": [d for d, _ in snapshots()],
        "matched_companies": len({norm_key(r["company"]) for r in matched}),
        "matched_by_direction": dict(collections.Counter(
            d for r in matched for d in r["direction_labels"])),
        "matched_by_city": dict(collections.Counter(
            c for r in matched for c in r["cities"])),
        "matched_by_nature": dict(collections.Counter(r["nature"] for r in matched)),
        "matched_by_cohort": dict(collections.Counter(str(r["cohort"]) for r in matched)),
        "matched_by_batch_kind": dict(collections.Counter(r["batch_kind"] for r in matched)),
        "position_groups": sum(1 for r in matched if r["position_is_group"]),
        "with_apply_url": sum(1 for r in matched if r["apply_url"]),
        "with_open_window": sum(1 for r in matched if r.get("opens_on")),
        "overseas_suspected": sum(1 for r in matched
                                  if r["checks"]["overseas"]["status"] == "疑似"),
        "overseas_verified": 0,
        "verified_base_salary": 0,
        "verified_weekend": 0,
        "note": "matched_rows 是岗位级行数，不是可投岗位数；底薪/双休全部为待核实。",
    }

    bundle = write_browser_bundle(matched, stats, aliases)
    bust_cache(bundle)
    stats["browser_bundle_kb"] = round(os.path.getsize(bundle) / 1024)
    json.dump(stats, open(os.path.join(DATA, "stats.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
