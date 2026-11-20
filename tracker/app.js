/* 秋招投递工作台
 *
 * 结构沿用 JobDrop Tracker（MIT）：单份内存数组 + localStorage + 全量重渲染 +
 * SheetJS 导出。字段模型换成国内校招需要的那一套，去重键从"公司名"改成
 * 公司 + 岗位 + 批次，另加薪资/双休等证据列。
 *
 * 岗位库来自 data.js（脚本生成，只读）。投递状态和核查结论写在 localStorage，
 * 两边按 id 合并——重跑 build_dataset.py 换掉岗位库，投递记录不会丢。
 */

const LS_KEY = "jobtrack_cn_v1";
const LS_THEME = "jobtrack_cn_theme";
const PAGE_SIZE = 60;
const TODAY = new Date().toISOString().slice(0, 10);

const STATUSES = [
  { key: "not_sent",   label: "未投递",     color: "#9AA3B0" },
  { key: "drafted",    label: "已填未提交", color: "#f59e0b" },
  { key: "applied",    label: "已投递",     color: "#2F6DF6" },
  { key: "assessment", label: "测评/笔试",  color: "#8b5cf6" },
  { key: "interview",  label: "面试中",     color: "#0ea5e9" },
  { key: "offer",      label: "Offer",      color: "#16a34a" },
  { key: "closed",     label: "已结束",     color: "#ef4444" },
];
const STATUS_MAP = Object.fromEntries(STATUSES.map(s => [s.key, s]));
// 只有真正提交过的才算"已投递"，用来防重复
const SENT_STATUSES = new Set(["applied", "assessment", "interview", "offer"]);

const RESUME_VERSIONS = ["", "技术版", "销售版", "综合版"];

const CHECKS = [
  { key: "base_salary",  label: "底薪过万" },
  { key: "weekend",      label: "双休" },
  { key: "overtime",     label: "加班情况" },
  { key: "written_test", label: "是否有笔试" },
  { key: "overseas",     label: "留学生可投" },
];
// "疑似" 只由脚本按公告原文的关键词预填，人工核实后应改成明确结论
const CHECK_STATUSES = ["待核实", "疑似", "符合", "不符合", "查不到"];

const DIR_LABEL = {
  sales: "销售系", sales_eng: "销售工程师",
  ai: "AI/大模型", agent: "Agent方向",
  trade: "跨境/外贸",
  mgmt: "管培生",
};
// 筛选按钮的排列顺序：主投的两个方向放前面
const DIR_ORDER = ["agent", "sales_eng", "trade", "ai", "sales", "mgmt"];
const CITY_ORDER = ["上海", "北京", "深圳", "广州", "杭州", "成都"];
const NATURE_ORDER = ["外企", "央企", "国企", "事业单位", "民企", "未标注", "其他"];
const BATCH_ORDER = ["秋招", "提前批", "校招", "补录", "春招", "实习", "社招", "博士后"];

/* ---------------------------------------------------------------- 状态 */

let jobs = [];        // 岗位库 + 本地记录合并后的数组
let userState = {};   // id -> 本地记录
let filters = null;
let shown = PAGE_SIZE;
let lastSig = "";
let expanded = new Set();

/* ---------------------------------------------------------------- 载入 */

function defaultFilters() {
  return {
    q: "",
    dirs: new Set(),
    cities: new Set(),
    natures: new Set(),
    batches: new Set(["秋招", "提前批"]),
    cohorts: new Set(),
    statuses: new Set(),
    hideExpired: true,
    hideApplied: false,
    onlyPreferred: false,
    onlyVerifiable: false,
    onlyOverseas: false,
    onlyNew: false,
    sort: "priority",
  };
}

function blankChecks() {
  const o = {};
  for (const c of CHECKS) {
    o[c.key] = { status: "待核实", evidence: "", source: "", checked_at: "" };
  }
  return o;
}

function loadState() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    userState = raw ? JSON.parse(raw) : {};
  } catch (e) {
    console.warn("本地记录读取失败，按空处理", e);
    userState = {};
  }
  if (!userState || typeof userState !== "object") userState = {};
  // 重建岗位库时，届别被别的来源补上的岗位会换 id（见 build_dataset.py 的 alt_ids）。
  // 把旧 id 上的投递记录搬到新 id，不然已投递的会变回未投递。
  let moved = 0;
  for (const [oldId, newId] of Object.entries(window.JOB_POOL_ALIASES || {})) {
    if (userState[oldId] && !userState[newId]) {
      userState[newId] = userState[oldId];
      delete userState[oldId];
      moved++;
    }
  }
  if (moved) saveState();
}

function blankRec() {
  return { status: "not_sent", resume: "", applied_at: "", notes: "",
           checks: blankChecks(), history: [] };
}

/** 全是默认值的记录不值得存，否则光是划过列表就会把 localStorage 撑满。 */
function isBlank(r) {
  if (!r) return true;
  if (r.status !== "not_sent" || r.resume || r.applied_at || r.notes) return false;
  if (r.history && r.history.length) return false;
  return CHECKS.every(c => {
    const v = (r.checks || {})[c.key];
    return !v || (v.status === "待核实" && !v.evidence && !v.source);
  });
}

function saveState() {
  for (const [id, r] of Object.entries(userState)) {
    if (isBlank(r)) delete userState[id];
  }
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(userState));
  } catch (e) {
    alert("本地保存失败（浏览器可能禁用了存储，或记录太多）：" + e.message);
  }
}

function normalize(r) {
  if (!r.checks) r.checks = blankChecks();
  for (const c of CHECKS) {
    if (!r.checks[c.key]) {
      r.checks[c.key] = { status: "待核实", evidence: "", source: "", checked_at: "" };
    }
  }
  if (!Array.isArray(r.history)) r.history = [];
  return r;
}

/** 只读：渲染用。不会往 userState 里塞空记录。 */
function viewRec(id) {
  return userState[id] ? normalize(userState[id]) : blankRec();
}

/** 可写：改动用。这时才真正建记录。 */
function editRec(id) {
  if (!userState[id]) userState[id] = blankRec();
  return normalize(userState[id]);
}

function hydrate() {
  const K = window.JOB_POOL_KEYS || {};
  jobs = (window.JOB_POOL || []).map(o => {
    const j = {};
    for (const [short, full] of Object.entries(K)) j[full] = o[short];
    j.id = o.i;
    j.directions = j.directions || [];
    j.cities = j.cities || [];
    j.sources = j.sources || [];
    j.company = j.company || "";
    j.position = j.position || "";
    j.deadline = j.deadline || "";
    j.deadline_raw = j.deadline_raw || "";
    j.city_tier = j.city_tier == null ? 9 : j.city_tier;
    j.nature = j.nature || "未标注";
    j.batch_kind = j.batch_kind || "未标注";
    j.expired = !!(j.deadline && j.deadline < TODAY);
    j.preferred = ["外企", "央企", "国企"].includes(j.nature);
    j.hay = (j.company + " " + j.position + " " + (j.industry || "") + " " +
             (j.blurb || "") + " " + (j.batch || "")).toLowerCase();
    return j;
  });
}

/* --------------------------------------------------- 防重复：同公司同岗位 */

let sentIndex = new Map();   // "公司|岗位" -> [已投递的 id]

function rebuildSentIndex() {
  sentIndex = new Map();
  for (const j of jobs) {
    const r = userState[j.id];
    if (!r || !SENT_STATUSES.has(r.status)) continue;
    const k = dupKey(j);
    if (!sentIndex.has(k)) sentIndex.set(k, []);
    sentIndex.get(k).push(j.id);
  }
}

function dupKey(j) {
  return (j.company + "|" + j.position).replace(/\s+/g, "").toLowerCase();
}

/** 同一公司同一岗位、但不同批次，已经投过 -> 提醒，不阻止 */
function duplicateOf(j) {
  const ids = sentIndex.get(dupKey(j));
  if (!ids) return null;
  const other = ids.find(id => id !== j.id);
  return other || null;
}

/* ---------------------------------------------------------------- 筛选 */

function matches(j) {
  const f = filters;
  if (f.q && !j.hay.includes(f.q)) return false;
  if (f.dirs.size && !j.directions.some(d => f.dirs.has(d))) return false;
  if (f.cities.size && !j.cities.some(c => f.cities.has(c))) return false;
  if (f.natures.size && !f.natures.has(j.nature)) return false;
  if (f.batches.size && !f.batches.has(j.batch_kind)) return false;
  if (f.cohorts.size && !f.cohorts.has(String(j.cohort || "未标注"))) return false;

  const r = userState[j.id];
  const st = r ? r.status : "not_sent";
  if (f.statuses.size && !f.statuses.has(st)) return false;
  if (f.hideExpired && j.expired) return false;
  if (f.hideApplied && SENT_STATUSES.has(st)) return false;
  if (f.onlyPreferred && !j.preferred) return false;
  if (f.onlyVerifiable && !j.apply_url) return false;
  if (f.onlyNew && !j.is_new) return false;
  if (f.onlyOverseas) {
    const ov = (r && r.checks && r.checks.overseas) ? r.checks.overseas.status
             : (j.overseas_keywords && j.overseas_keywords.length ? "疑似" : "待核实");
    if (ov !== "疑似" && ov !== "符合") return false;
  }
  return true;
}

function sortJobs(list) {
  const f = filters;
  const far = "9999-99-99";
  const cmp = {
    priority: (a, b) =>
      a.city_tier - b.city_tier ||
      (a.preferred === b.preferred ? 0 : a.preferred ? -1 : 1) ||
      (a.deadline || far).localeCompare(b.deadline || far) ||
      a.company.localeCompare(b.company, "zh"),
    deadline: (a, b) =>
      (a.deadline || far).localeCompare(b.deadline || far) ||
      a.city_tier - b.city_tier,
    posted: (a, b) => (b.posted_on || "").localeCompare(a.posted_on || ""),
    company: (a, b) => a.company.localeCompare(b.company, "zh"),
  }[f.sort];
  return list.slice().sort(cmp);
}

/* ---------------------------------------------------------------- 渲染 */

function esc(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function renderChips() {
  const f = filters;
  const count = (pred) => jobs.filter(pred).length;

  const build = (el, items, set, key) => {
    el.innerHTML = items.map(it => {
      const on = set.has(it.value) ? " on" : "";
      return `<button class="chip${on}" data-group="${key}" data-value="${esc(it.value)}">` +
             `${esc(it.label)}<span class="n">${it.n}</span></button>`;
    }).join("");
  };

  build(document.getElementById("fDirection"),
    DIR_ORDER.map(v => [v, DIR_LABEL[v]]).map(([v, l]) =>
      ({ value: v, label: l, n: count(j => j.directions.includes(v)) })),
    f.dirs, "dirs");

  build(document.getElementById("fCity"),
    CITY_ORDER.map(c => ({ value: c, label: c, n: count(j => j.cities.includes(c)) })),
    f.cities, "cities");

  build(document.getElementById("fNature"),
    NATURE_ORDER.filter(n => count(j => j.nature === n))
      .map(n => ({ value: n, label: n, n: count(j => j.nature === n) })),
    f.natures, "natures");

  build(document.getElementById("fBatch"),
    BATCH_ORDER.filter(b => count(j => j.batch_kind === b))
      .map(b => ({ value: b, label: b, n: count(j => j.batch_kind === b) })),
    f.batches, "batches");

  const cohorts = [...new Set(jobs.map(j => String(j.cohort || "未标注")))].sort();
  build(document.getElementById("fCohort"),
    cohorts.map(c => ({ value: c, label: c === "未标注" ? "届别未标注" : c + "届",
                        n: count(j => String(j.cohort || "未标注") === c) })),
    f.cohorts, "cohorts");

  build(document.getElementById("fStatus"),
    STATUSES.map(s => ({ value: s.key, label: s.label,
      n: jobs.filter(j => (userState[j.id] ? userState[j.id].status : "not_sent") === s.key).length })),
    f.statuses, "statuses");

  const toggles = [
    ["hideExpired", "隐藏已过期"],
    ["hideApplied", "隐藏已投递"],
    ["onlyPreferred", "只看外企/国企央企"],
    ["onlyVerifiable", "只看有投递链接"],
    ["onlyOverseas", "只看疑似收留学生"],
    ["onlyNew", "只看本次新增"],
  ];
  document.getElementById("fToggles").innerHTML = toggles.map(([k, l]) =>
    `<button class="chip${f[k] ? " on" : ""}" data-group="toggle" data-value="${k}">${l}</button>`
  ).join("");
}

function renderPipeline(list) {
  const counts = {};
  for (const s of STATUSES) counts[s.key] = 0;
  for (const j of list) {
    const st = userState[j.id] ? userState[j.id].status : "not_sent";
    counts[st] = (counts[st] || 0) + 1;
  }
  const total = list.length || 1;
  document.getElementById("pipeline").innerHTML = STATUSES.map(s =>
    `<div class="seg" title="${s.label} ${counts[s.key]}" style="flex:${counts[s.key] || 0.02} 0 auto;background:${s.color}"></div>`
  ).join("");

  const applied = STATUSES.filter(s => SENT_STATUSES.has(s.key))
    .reduce((n, s) => n + counts[s.key], 0);
  const verified = Object.values(userState)
    .filter(r => r.checks && r.checks.base_salary && r.checks.base_salary.status !== "待核实").length;

  document.getElementById("stats").innerHTML =
    `<span>当前筛选 <b>${list.length}</b> 条</span>` +
    `<span>已投递 <b>${applied}</b></span>` +
    `<span>面试中 <b>${counts.interview}</b></span>` +
    `<span>Offer <b>${counts.offer}</b></span>` +
    `<span>底薪已核实 <b>${verified}</b></span>` +
    `<span>本次新增 <b>${list.filter(j => j.is_new).length}</b></span>` +
    `<span class="muted">占比按当前筛选结果计算</span>`;
}

/**
 * 取一条结论用于显示。留学生这项特殊：脚本按公告原文预标了"疑似"，
 * 用户还没自己填过时就沿用脚本的判断（连同命中的原文关键词一起显示）。
 */
function effectiveCheck(j, r, key) {
  const v = r.checks[key];
  if (key === "overseas" && v.status === "待核实" && !v.evidence &&
      j.overseas_keywords && j.overseas_keywords.length) {
    return {
      status: "疑似",
      evidence: "公告标题/简介出现：" + j.overseas_keywords.join("、"),
      source: j.notice_url || j.apply_url || "",
      checked_at: "",
      derived: true,
    };
  }
  return v;
}

function jobRow(j) {
  const r = viewRec(j.id);
  const dup = duplicateOf(j);
  const st = STATUS_MAP[r.status] || STATUS_MAP.not_sent;
  const open = expanded.has(j.id);

  const tags = [];
  if (j.is_new) tags.push(`<span class="tag new" title="首次出现于 ${j.first_seen}">新</span>`);
  for (const d of j.directions) tags.push(`<span class="tag dir">${DIR_LABEL[d] || d}</span>`);
  if (j.cities.length) tags.push(`<span class="tag city">${j.cities.join(" / ")}</span>`);
  else tags.push(`<span class="tag warn">城市待核实</span>`);
  tags.push(`<span class="tag${j.preferred ? " pref" : ""}">${esc(j.nature)}</span>`);
  if (j.industry) tags.push(`<span class="tag">${esc(j.industry)}</span>`);
  tags.push(`<span class="tag">${esc(j.batch || "批次未标注")}</span>`);
  if (j.deadline) {
    tags.push(`<span class="tag${j.expired ? " dead" : ""}">截止 ${j.deadline}${j.expired ? "（已过期）" : ""}</span>`);
  } else if (j.deadline_raw) {
    tags.push(`<span class="tag">截止 ${esc(j.deadline_raw)}</span>`);
  }
  if (j.opens_on) tags.push(`<span class="tag">网申开放 ${j.opens_on}</span>`);
  if (j.deadline_conflict) {
    // 两个源给的截止日期不一样，取的是最晚那个，别当准数
    tags.push(`<span class="tag warn" title="来源说法：${(j.deadline_seen || []).join(' / ')}">截止日各源不一致</span>`);
  }
  if (j.overseas_keywords && j.overseas_keywords.length) {
    tags.push(`<span class="tag oversea">公告提到：${j.overseas_keywords.join("、")}</span>`);
  }
  if (j.position_is_group) tags.push(`<span class="tag warn">岗位组·到官网确认具体岗位</span>`);
  if (dup) tags.push(`<span class="tag warn">同名岗位已投过</span>`);

  const checkSummary = CHECKS.map(c => {
    const v = effectiveCheck(j, r, c.key);
    return `<span class="tag st-${v.status}">${c.label}：${v.status}</span>`;
  }).join("");

  const links = [];
  if (j.apply_url) links.push(`<a class="lnk" href="${esc(j.apply_url)}" target="_blank" rel="noopener">投递入口 ↗</a>`);
  if (j.notice_url) links.push(`<a class="lnk" href="${esc(j.notice_url)}" target="_blank" rel="noopener">公告 ↗</a>`);

  let more = "";
  if (open) {
    more = `<div class="job-more">
      <div class="check-grid">${CHECKS.map(c => {
        const v = effectiveCheck(j, r, c.key);
        return `<div class="check">
          <div class="check-head">
            <b>${c.label}</b>
            <select data-act="check-status" data-id="${j.id}" data-check="${c.key}">
              ${CHECK_STATUSES.map(s => `<option${s === v.status ? " selected" : ""}>${s}</option>`).join("")}
            </select>
          </div>
          <textarea rows="2" placeholder="证据原文，例如「招聘公告写明大小周」" data-act="check-evidence" data-id="${j.id}" data-check="${c.key}">${esc(v.evidence)}</textarea>
          <input type="url" placeholder="证据来源链接" value="${esc(v.source)}" data-act="check-source" data-id="${j.id}" data-check="${c.key}">
          <div class="stamp">${v.derived ? "脚本按公告原文预标，未人工核实"
            : v.checked_at ? "核查于 " + v.checked_at : "尚未核查"}</div>
        </div>`;
      }).join("")}</div>
      <div class="notes-row">
        <textarea placeholder="备注：内推码、联系人、笔试时间…" data-act="notes" data-id="${j.id}">${esc(r.notes)}</textarea>
      </div>
      <div class="hist">${r.history.length
        ? r.history.map(h => `<div class="hist-row"><span class="hist-date">${h.at}</span><span>${esc(h.from)} → ${esc(h.to)}</span></div>`).join("")
        : "暂无状态变更记录"}</div>
      <div class="hist">来源：${j.sources.join("、")}｜抓取于 ${j.fetched_on || "-"}${j.posted_on ? "｜公告日期 " + j.posted_on : ""}</div>
    </div>`;
  }

  return `<div class="job${dup ? " dup" : ""}" data-id="${j.id}">
    <div class="job-top">
      <div class="job-main">
        <div class="job-name"><span class="pos">${esc(j.position)}</span><span class="co">${esc(j.company)}</span></div>
        <div class="job-sub">${tags.join("")}</div>
        <div class="job-sub">${checkSummary}</div>
        ${j.blurb ? `<div class="job-blurb">${esc(j.blurb)}</div>` : ""}
      </div>
      <div class="job-side">
        <select data-act="status" data-id="${j.id}" style="border-color:${st.color}">
          ${STATUSES.map(s => `<option value="${s.key}"${s.key === r.status ? " selected" : ""}>${s.label}</option>`).join("")}
        </select>
        <select data-act="resume" data-id="${j.id}">
          ${RESUME_VERSIONS.map(v => `<option value="${v}"${v === r.resume ? " selected" : ""}>${v || "简历版本"}</option>`).join("")}
        </select>
        ${links.join("")}
        <button class="lnk" data-act="toggle" data-id="${j.id}">${open ? "收起" : "核查/备注"}</button>
      </div>
    </div>
    ${more}
  </div>`;
}

function render() {
  const list = sortJobs(jobs.filter(matches));
  const sig = JSON.stringify([filters.q, [...filters.dirs], [...filters.cities],
    [...filters.natures], [...filters.batches], [...filters.cohorts], [...filters.statuses],
    filters.hideExpired, filters.hideApplied, filters.onlyPreferred,
    filters.onlyVerifiable, filters.onlyOverseas, filters.onlyNew, filters.sort]);
  if (sig !== lastSig) { shown = PAGE_SIZE; lastSig = sig; }

  renderChips();
  renderPipeline(list);

  const meta = window.JOB_POOL_META || {};
  document.getElementById("metaLine").textContent =
    `岗位库 ${meta.matched_rows || jobs.length} 条 · 公司 ${meta.matched_companies || "?"} 家 · 数据构建于 ${meta.built_on || "?"}`;

  const page = list.slice(0, shown);
  document.getElementById("resultCount").textContent =
    `显示 ${page.length} / ${list.length} 条（岗位库共 ${jobs.length} 条）`;
  document.getElementById("list").innerHTML = page.length
    ? page.map(jobRow).join("")
    : `<div class="empty-state">没有符合条件的岗位。放宽筛选，或点「重置筛选」。</div>`;
  document.getElementById("showMore").hidden = list.length <= shown;
}

/* ---------------------------------------------------------------- 今日待投 */

/** 管培生轮岗、不挑对口专业，两份简历都能投，用综合版 */
function resumeHint(j) {
  // 售前/销售工程师是技术型销售，用销售版但要把技术背景写进去
  if (j.directions.includes("sales_eng")) return "销售版(带技术)";
  if (j.directions.includes("agent") || j.directions.includes("ai")) return "技术版";
  if (j.directions.includes("trade")) return "销售版(跨境)";
  if (j.directions.includes("sales")) return "销售版";
  return "综合版";
}

function buildPlan(n) {
  const pool = sortJobs(jobs.filter(j => {
    if (!matches(j)) return false;
    const r = userState[j.id];
    if (r && r.status !== "not_sent") return false;
    if (j.expired) return false;
    return true;
  }));

  // 岗位组（源站把一堆岗位挤成一条）排到后面——它不是一个能直接投的岗位
  pool.sort((a, b) => (a.position_is_group ? 1 : 0) - (b.position_is_group ? 1 : 0));

  // 同一家公司当天最多推 2 个岗位，避免清单被一家刷屏
  const perCompany = {};
  const picked = [];
  for (const j of pool) {
    const c = perCompany[j.company] || 0;
    if (c >= 2) continue;
    perCompany[j.company] = c + 1;
    picked.push(j);
    if (picked.length >= n) break;
  }

  const box = document.getElementById("planbox");
  box.hidden = false;
  box.innerHTML = `
    <h3>今日待投 · ${TODAY}</h3>
    <div class="hint">按「城市优先级 → 外企/国企央企优先 → 截止日期最近」排的，只取当前筛选下still「未投递」且未过期的。
      底薪和双休一律还是待核实，清单不代表已确认符合条件。</div>
    <div class="plan-controls">
      <label>条数 <input type="number" id="planN" min="1" max="100" value="${n}"></label>
      <button class="chip" id="planGo">重新生成</button>
      <button class="chip" id="planCopy">复制清单</button>
      <button class="chip" id="planClose">关闭</button>
    </div>
    <ol id="planList">${picked.map(j => `<li>
      <b>${esc(j.company)}</b> — ${esc(j.position)}
      <span class="plan-meta">｜${j.cities.join("/") || "城市待核实"}｜${esc(j.nature)}｜${j.deadline ? "截止 " + j.deadline : esc(j.deadline_raw || "截止未标注")}｜建议简历：${resumeHint(j)}</span>
      ${j.apply_url ? `<a class="lnk" href="${esc(j.apply_url)}" target="_blank" rel="noopener">投递 ↗</a>` : "<span class=\"plan-meta\">（无直达链接，需到官网搜）</span>"}
    </li>`).join("")}</ol>
    ${picked.length < n ? `<div class="hint">符合条件的只有 ${picked.length} 条，没有凑数。</div>` : ""}`;

  document.getElementById("planGo").onclick = () =>
    buildPlan(Math.max(1, parseInt(document.getElementById("planN").value, 10) || n));
  document.getElementById("planClose").onclick = () => { box.hidden = true; };
  document.getElementById("planCopy").onclick = () => {
    const text = picked.map((j, i) =>
      `${i + 1}. ${j.company} — ${j.position}｜${j.cities.join("/") || "城市待核实"}｜${j.nature}｜${j.deadline || j.deadline_raw || "截止未标注"}｜${j.apply_url || "无直达链接"}`
    ).join("\n");
    navigator.clipboard.writeText(text).then(
      () => alert("已复制 " + picked.length + " 条"),
      () => alert("复制失败，请手动选中"));
  };
}

/* ---------------------------------------------------------------- 事件 */

function setStatus(id, next) {
  const r = editRec(id);
  if (r.status === next) return;
  r.history.unshift({ at: TODAY, from: (STATUS_MAP[r.status] || {}).label || r.status,
                      to: (STATUS_MAP[next] || {}).label || next });
  r.status = next;
  if (SENT_STATUSES.has(next)) {
    if (!r.applied_at) r.applied_at = TODAY;
  } else if (next === "not_sent") {
    // 撤回成"未投递"就不该再挂着投递日期，否则导出表里会自相矛盾
    r.applied_at = "";
  }
  saveState();
  rebuildSentIndex();
  render();
}

function wireEvents() {
  document.body.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip[data-group]");
    if (chip) {
      const g = chip.dataset.group, v = chip.dataset.value;
      if (g === "toggle") filters[v] = !filters[v];
      else {
        const set = filters[g];
        set.has(v) ? set.delete(v) : set.add(v);
      }
      render();
      return;
    }
    const btn = e.target.closest("[data-act='toggle']");
    if (btn) {
      const id = btn.dataset.id;
      expanded.has(id) ? expanded.delete(id) : expanded.add(id);
      render();
    }
  });

  document.body.addEventListener("change", (e) => {
    const el = e.target;
    const act = el.dataset.act, id = el.dataset.id;
    if (!act || !id) return;
    if (act === "status") { setStatus(id, el.value); return; }
    const r = editRec(id);
    if (act === "resume") r.resume = el.value;
    else if (act === "notes") r.notes = el.value;
    else if (act === "check-status") {
      const c = r.checks[el.dataset.check];
      c.status = el.value;
      c.checked_at = el.value === "待核实" ? "" : TODAY;
      saveState();
      render();
      return;
    } else if (act === "check-evidence") {
      const c = r.checks[el.dataset.check];
      c.evidence = el.value;
      c.checked_at = TODAY;
    } else if (act === "check-source") {
      const c = r.checks[el.dataset.check];
      c.source = el.value;
      c.checked_at = TODAY;
    }
    saveState();
  });

  document.getElementById("search").addEventListener("input", (e) => {
    filters.q = e.target.value.trim().toLowerCase();
    render();
  });
  document.getElementById("sortSelect").addEventListener("change", (e) => {
    filters.sort = e.target.value;
    render();
  });
  document.getElementById("btnReset").addEventListener("click", () => {
    filters = defaultFilters();
    document.getElementById("search").value = "";
    document.getElementById("sortSelect").value = "priority";
    render();
  });
  document.getElementById("showMore").addEventListener("click", () => {
    shown += PAGE_SIZE;
    render();
  });
  document.getElementById("btnPlan").addEventListener("click", () => buildPlan(20));
  document.getElementById("btnExportXlsx").addEventListener("click", exportXlsx);
  document.getElementById("btnExportJson").addEventListener("click", exportJson);
  document.getElementById("importFile").addEventListener("change", importJson);
  document.getElementById("btnClear").addEventListener("click", () => {
    if (!confirm("清空全部投递状态和核查记录？岗位库不受影响，但本地记录无法恢复。建议先「备份 JSON」。")) return;
    userState = {};
    saveState();
    rebuildSentIndex();
    render();
  });

  const themeBtn = document.getElementById("themeBtn");
  const applyTheme = (t) => {
    document.documentElement.dataset.theme = t;
    themeBtn.textContent = t === "dark" ? "☀️" : "🌙";
    try { localStorage.setItem(LS_THEME, t); } catch (e) { /* 无痕模式，忽略 */ }
  };
  let theme = "light";
  try { theme = localStorage.getItem(LS_THEME) || "light"; } catch (e) { /* 同上 */ }
  applyTheme(theme);
  themeBtn.addEventListener("click", () =>
    applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"));
}

/* ---------------------------------------------------------------- 导入导出 */

const XLSX_COLS = [
  ["公司", j => j.company],
  ["岗位", j => j.position],
  ["方向", j => j.directions.map(d => DIR_LABEL[d]).join("/")],
  ["城市", j => j.cities.join("/")],
  ["企业性质", j => j.nature],
  ["行业", j => j.industry || ""],
  ["批次", j => j.batch || ""],
  ["届别", j => j.cohort || ""],
  ["截止日期", j => j.deadline || j.deadline_raw || ""],
  ["投递状态", (j, r) => (STATUS_MAP[r.status] || {}).label || r.status],
  ["简历版本", (j, r) => r.resume],
  ["投递日期", (j, r) => r.applied_at],
  ["底薪过万", (j, r) => r.checks.base_salary.status],
  ["底薪证据", (j, r) => r.checks.base_salary.evidence],
  ["底薪证据来源", (j, r) => r.checks.base_salary.source],
  ["双休", (j, r) => r.checks.weekend.status],
  ["双休证据", (j, r) => r.checks.weekend.evidence],
  ["双休证据来源", (j, r) => r.checks.weekend.source],
  ["加班情况", (j, r) => r.checks.overtime.status],
  ["加班证据", (j, r) => r.checks.overtime.evidence],
  ["是否有笔试", (j, r) => r.checks.written_test.status],
  ["留学生可投", (j, r) => effectiveCheck(j, r, "overseas").status],
  ["留学生证据", (j, r) => effectiveCheck(j, r, "overseas").evidence],
  ["留学生证据来源", (j, r) => effectiveCheck(j, r, "overseas").source],
  ["网申开放日期", j => j.opens_on || ""],
  ["备注", (j, r) => r.notes],
  ["投递入口", j => j.apply_url || ""],
  ["公告链接", j => j.notice_url || ""],
  ["数据来源", j => j.sources.join("/")],
  ["抓取日期", j => j.fetched_on || ""],
  ["去重键", j => j.id],
];

function exportXlsx() {
  const list = sortJobs(jobs.filter(matches));
  const rows = [XLSX_COLS.map(c => c[0])];
  for (const j of list) {
    const r = viewRec(j.id);
    rows.push(XLSX_COLS.map(c => c[1](j, r)));
  }
  const wb = XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(rows), "岗位库");
  XLSX.writeFile(wb, `秋招投递表_${TODAY}.xlsx`);
}

function exportJson() {
  const blob = new Blob([JSON.stringify({
    version: 1, exported_at: TODAY, state: userState,
  }, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `投递记录备份_${TODAY}.json`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function importJson(e) {
  const file = e.target.files[0];
  if (!file) return;
  const fr = new FileReader();
  fr.onload = () => {
    try {
      const data = JSON.parse(fr.result);
      const incoming = data.state || data;
      let merged = 0;
      for (const [id, r] of Object.entries(incoming)) {
        if (!r || typeof r !== "object") continue;
        userState[id] = r;
        merged++;
      }
      saveState();
      rebuildSentIndex();
      render();
      alert(`已恢复 ${merged} 条记录。`);
    } catch (err) {
      alert("备份文件读不了：" + err.message);
    }
    e.target.value = "";
  };
  fr.readAsText(file);
}

/* ---------------------------------------------------------------- 启动 */

function boot() {
  if (!window.JOB_POOL) {
    document.getElementById("list").innerHTML =
      `<div class="empty-state">没找到 data.js。先在 job-search-toolkit 目录跑一次<br><code>python scripts/build_dataset.py</code></div>`;
    return;
  }
  loadState();
  hydrate();
  rebuildSentIndex();
  filters = defaultFilters();
  wireEvents();
  render();
}

document.addEventListener("DOMContentLoaded", boot);
