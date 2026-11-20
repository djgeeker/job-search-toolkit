# -*- coding: utf-8 -*-
"""
把一张网申表单里的问题全抽出来，打印成答案库条目，粘到
sources/form-pilot/lib/engine/answer-bank.data.ts 里填答案就行。

用法
----
1. 在浏览器打开网申页，Ctrl+S 另存为「网页，仅 HTML」
2. python scripts/extract_questions.py 那个文件.html
3. 加 --json 输出 JSON；加 --out 文件名 写到文件
4. 加 --skip-known 把简历里已经有的字段（姓名/手机/邮箱…）过滤掉，
   只留下需要你自己答的那些

说明
----
- 只做静态解析。表单是 JS 动态渲染的话，另存下来的 HTML 里可能没有内容，
  这种情况脚本会说抽到 0 条，不会假装成功。
- 抽出来的是**问题**，答案一律留空 —— 涉及你的经历和意愿，脚本不替你猜。
"""
import argparse
import html
import json
import os
import re
import sys

# 简历里已经有的字段，--skip-known 时过滤掉
KNOWN = [
    "姓名", "性别", "手机", "电话", "邮箱", "邮件", "出生", "生日", "年龄",
    "民族", "政治面貌", "国籍", "籍贯", "身份证", "证件号",
    "学校", "院校", "学历", "学位", "专业", "绩点", "gpa",
    "入学", "毕业时间", "开始时间", "结束时间", "在校时间",
    "公司", "职位", "岗位名称", "部门", "工作时间",
    "现居", "所在城市", "居住地", "地址",
]

# 这些不是问题
NOISE = re.compile(
    r"^\s*$|^[*＊·\s]+$|^(请输入|请选择|请填写|搜索|上传|下载|提交|保存|取消|返回|下一步|上一步)"
)

# 控件类型 -> 答案库里的 kinds
KIND_MAP = {
    "text": "text", "email": "text", "tel": "text", "number": "text",
    "url": "text", "date": "text", "password": "text",
    "textarea": "textarea", "select": "select",
    "radio": "radio", "checkbox": "checkbox",
}


def strip_tags(s):
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def clean_label(s):
    s = strip_tags(s)
    s = re.sub(r"^[*＊\s]+", "", s)          # 必填星号
    s = re.sub(r"\s*\d+\s*/\s*\d+\s*$", "", s)  # 「0/50」字数计数
    return s.strip(" :：*")


def extract(path, skip_known=False):
    raw = open(path, "rb").read()
    for enc in ("utf-8", "gb18030"):
        try:
            doc = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        doc = raw.decode("utf-8", "replace")

    found = []
    seen = set()

    # 1) <label for=x> + 控件
    for m in re.finditer(r'<label[^>]*\bfor="([^"]+)"[^>]*>(.*?)</label>', doc, re.S | re.I):
        target, inner = m.group(1), m.group(2)
        label = clean_label(inner)
        if not label or NOISE.match(label):
            continue
        kind = "text"
        em = re.search(r'<(input|select|textarea)[^>]*\bid="%s"[^>]*>' % re.escape(target), doc, re.I)
        if em:
            tag = em.group(1).lower()
            if tag == "input":
                tm = re.search(r'type="([^"]+)"', em.group(0), re.I)
                kind = KIND_MAP.get((tm.group(1).lower() if tm else "text"), "text")
            else:
                kind = KIND_MAP.get(tag, "text")
        found.append((label, kind))

    # 2) 常见的「标签块 + 控件块」结构（Ant Design / Element UI 这类）
    for m in re.finditer(
        r'<(?:div|td|span)[^>]*class="[^"]*(?:label|form-item__label|ant-form-item-label|title)[^"]*"[^>]*>(.*?)</(?:div|td|span)>',
        doc, re.S | re.I,
    ):
        label = clean_label(m.group(1))
        if not label or NOISE.match(label) or len(label) > 60:
            continue
        tail = doc[m.end():m.end() + 1200]
        if "<textarea" in tail.lower():
            kind = "textarea"
        elif re.search(r'type="radio"', tail, re.I):
            kind = "radio"
        elif re.search(r'type="checkbox"', tail, re.I):
            kind = "checkbox"
        elif "<select" in tail.lower():
            kind = "select"
        elif "<input" in tail.lower():
            kind = "text"
        else:
            continue
        found.append((label, kind))

    out = []
    for label, kind in found:
        key = re.sub(r"\s+", "", label)
        if key in seen:
            continue
        seen.add(key)
        # 只过滤"标签本身就是那个字段"的短标签。
        # 按子串一律过滤会误伤：「工作地点是否服从公司调配」含「公司」、
        # 「校招面试站点（请选择您大学所在城市）」含「所在城市」，
        # 这两个都是要你自己答的问题，不该被当成简历已有字段扔掉。
        if skip_known and len(key) <= 8 and any(k in label.lower() for k in KNOWN):
            continue
        out.append({"label": label, "kind": kind})
    return out


def slugify(label, used):
    base = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    if not base:
        base = "q" + str(len(used) + 1)
    base = base[:32]
    slug, n = base, 2
    while slug in used:
        slug = "%s-%d" % (base, n)
        n += 1
    used.add(slug)
    return slug


def to_ts(items):
    used = set()
    lines = [
        "// 由 scripts/extract_questions.py 生成。答案都留空了，自己填。",
        "// 填完粘到 sources/form-pilot/lib/engine/answer-bank.data.ts 的 ANSWER_BANK 里，",
        "// 然后 yarn build 重新构建扩展。",
        "",
    ]
    for it in items:
        lines.append("  {")
        lines.append("    id: %s," % json.dumps(slugify(it["label"], used), ensure_ascii=False))
        lines.append("    phrasings: [%s]," % json.dumps(it["label"], ensure_ascii=False))
        lines.append("    answer: '',")
        lines.append("    kinds: [%s]," % json.dumps(it["kind"]))
        lines.append("  },")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("html_file")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而不是 TS 片段")
    ap.add_argument("--skip-known", action="store_true", help="过滤掉简历里已有的字段")
    ap.add_argument("--out", help="写到文件，不给就打印")
    a = ap.parse_args()

    if not os.path.exists(a.html_file):
        sys.exit("找不到文件：%s" % a.html_file)

    items = extract(a.html_file, a.skip_known)
    if not items:
        sys.exit(
            "一条都没抽到。这个页面多半是 JS 动态渲染的，另存下来的 HTML 里没有表单。\n"
            "办法：在页面上按 F12 → Elements → 右键 <html> → Copy → Copy outerHTML，\n"
            "      贴进一个 .html 文件再跑一次。"
        )

    text = json.dumps(items, ensure_ascii=False, indent=2) if a.json else to_ts(items)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(text)
        print("抽到 %d 个问题，写入 %s" % (len(items), a.out))
    else:
        print(text)
        print("\n# 共 %d 个问题" % len(items), file=sys.stderr)


if __name__ == "__main__":
    main()
