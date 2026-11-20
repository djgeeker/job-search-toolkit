# 秋招求职工具箱

一套给校招用的本地工具：**岗位库爬取与清洗 + 投递工作台 + 网申自动填表**。所有数据和投递记录都只存在本机。

## 组成

| 模块 | 内容 |
|---|---|
| `scripts/fetch_sources.py` | 从 6 个校招聚合站和公开数据源抓取招聘公告，每次抓取存成一个带日期的新快照，旧快照不改 |
| `scripts/build_dataset.py` | 唯一的数据生成入口：合并多源多快照、按公司和批次去重、拆分岗位、识别城市和方向、标出疑似接收留学生的公告，输出岗位库和工作台数据 |
| `scripts/shortlist.py` | 按预设方向（Agent、销售工程师、外贸、管培生等）生成短名单 |
| `scripts/extract_questions.py` | 从另存的网申页面 HTML 里抽出所有表单问题，生成自动填表答案库的条目 |
| `tracker/` | 纯前端投递工作台，双击 `index.html` 就能用，不需要服务器 |
| `patches/form-pilot.patch` | 对 [FormPilot](https://github.com/rockbenben/form-pilot) 自动填表扩展的改造：新增校招字段和答案库 |

我自己跑的一次结果：12,560 条招聘公告，去重后 10,817 家公司，筛完城市和方向后剩 12,326 条岗位级记录。

## 投递工作台

- 实时筛选：城市、方向、企业性质、届别、批次，每个按钮都显示条数
- 「生成今日待投」：按城市优先级、企业性质、截止日期排序，同一家公司一天最多 2 个
- 防重复：同公司同岗位在别的批次投过会标黄
- 核查记录：底薪、双休、加班、笔试、是否收留学生，每条结论都要填证据原文和来源链接，否则保持「待核实」
- 重新生成岗位库后投递记录不丢：记录存在浏览器 localStorage，按去重键合并，岗位 id 变了也会自动迁移
- 导出 Excel（31 列，含证据列）和 JSON 备份

## FormPilot 改造

原版 FormPilot 主要面向社招平台。我加了这些：

- 15 个校招和留学生常见字段的识别规则（毕业届别、三方协议、学历认证状态、回国时间等），并修了宽泛规则抢字段的 3 个 bug
- **答案库**：同一个问题在不同公司有各种问法（「是否服从调剂」「能否接受工作地点调整」……），按问法匹配后自动填同一个答案
- 用基恩士网申页做的测试样例

详细说明见 [docs/formpilot-review.md](docs/formpilot-review.md)。应用补丁：

```bash
git clone https://github.com/rockbenben/form-pilot.git
cd form-pilot
git checkout 1b40c228b151894011af81c3a3230bad2a48fc90
git apply ../patches/form-pilot.patch
yarn install && yarn build
```

然后按 [docs/formpilot-setup.md](docs/formpilot-setup.md) 加载到 Chrome。个人资料从 `profiles/*.template.json` 复制一份填写。

## 快速开始

```bash
python scripts/fetch_sources.py all      # 抓取（存到 raw/snap/<日期>/）
python scripts/build_dataset.py          # 生成 data/*.json 和 tracker/data.js
python -m http.server 8765 --directory tracker
```

仓库里的 `tracker/data.js` 只是空的占位文件，跑完上面的命令才会有数据。抓取到的数据不放进仓库。

## 文档

- [docs/sources.md](docs/sources.md)：数据来源清单和各自的限制
- [docs/overseas-students.md](docs/overseas-students.md)：留学生投国内校招的规则整理

## 用到的开源项目

| 项目 | 许可 | 用法 |
|---|---|---|
| [FormPilot](https://github.com/rockbenben/form-pilot) | MIT | 在其基础上改造，见 `patches/` |
| [JobDrop Tracker](https://github.com/Abdallahabusnineh/jobdrop-tracker) | MIT | 沿用架构，复用 `style.css` 和 `xlsx.full.min.js`，许可见 `tracker/LICENSE.jobdrop` |
| [xixicc2027](https://github.com/xixicc186/xixicc2027) | 见原仓库 | 只读取它的 `jobs.json` 作为数据源 |
