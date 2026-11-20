# FormPilot 装机与配置

自动填网申表的浏览器扩展。[仓库](https://github.com/rockbenben/form-pilot)，MIT，简历数据只存本机。

---

## 1. 装扩展（3 分钟，得你自己点）

扩展**已经构建好了**，不用装依赖也不用编译：

1. Chrome 地址栏输 `chrome://extensions`
2. 右上角打开 **开发者模式**
3. 点 **加载已解压的扩展程序**
4. 选这个目录：

```
<form-pilot 源码目录>/.output/chrome-mv3
```

5. 把图标钉到工具栏

> 也可以直接装[商店版](https://chromewebstore.google.com/detail/formpilot/hhmmkjhbonceogplepkeildfepmcjaah)，会自动更新。
> 本地这份的好处是版本固定，出问题能对着源码查。**两个别同时装**，会重复填。

## 2. 灌两份资料

工作台里每条岗位都有「简历版本」下拉（技术版 / 销售版 / 综合版），对应 FormPilot 里的两份 profile。

### 走 JSON 模板（推荐，快）

`profiles/` 下有两份模板：

- `formpilot-tech.template.json` — 技术版（AI Agent / 大模型应用开发）
- `formpilot-sales.template.json` — 销售版（技术销售 / 售前 / 解决方案 / 管培生）

步骤：

1. 复制一份，把文件名里的 `.template` 去掉
2. 把所有 `<...>` 占位符换成真实信息，删掉顶上的 `_说明` 字段
3. 用不上的字段留空字符串或删掉都行 —— 导入时缺的会自动补空，不会报错
4. FormPilot 图标 → **编辑资料** → Dashboard 里找 **导入** → 选 **JSON** → 挑你的文件
5. 两份分别导一次

模板里的字段名和顺序跟 FormPilot 的 `Resume` 类型对得上（见 `sources/form-pilot/lib/storage/types.ts`），
`phone` / `email` 用的是字符串写法，`importResume` 会自动包成它内部的多值结构。

### 或者直接拖简历

Dashboard 的导入对话框也吃 `.pdf` / `.docx`，会解析出来预填，你再改错的地方。
**注意：`.doc`（老格式）不支持**，先另存成 `.docx` 或 PDF。

## 3. 用法

- **悬浮工具栏**：在内置支持的招聘站上，页面边上会出现 `[⚡ 填充]` 按钮
- **快捷键**：`Alt+Shift+F` 开关工具栏
- **切换资料**：填之前在 popup 里选技术版还是销售版
- **多值字段**：同一个字段存了多个值（比如个人邮箱和校园邮箱），字段边上会有个 `▾` 可以切
- **草稿**：长表单填一半 → `💾 保存草稿`，按 URL 存 30 天

官方说内置支持 Moka、北森、Workday、Greenhouse、BOSS 直聘、拉勾、智联、飞书等。
**这是它文档写的，我没实测过。**

## 4. 敏感字段

FormPilot 默认**跳过身份证、验证码、密码、银行卡**。设置里可以按站点打开，但——

> **别开。** 这条也写进了 `AGENTS.md`：这类字段你自己手填，AI 不碰。

## 5. 投递的最后一步永远是你按

FormPilot 只填，不提交。填完自己过一遍再点提交，重点看：

- **毕业时间 / 届别** —— 留学生尤其要对，见 [overseas-students.md](overseas-students.md)
- 是否需要签证担保、工作许可
- 各种日期（起止年月很容易被填串）
- 开放题（自我介绍、为什么选我们）—— 这些机器填出来的都得改

## 6. 还没做的

- **没在真实网申页实测过。** 建议先拿 3 家试，Moka / 北森 / 飞书各一个，
  记下哪些字段填不上，回来补 `custom` 字段或反馈给上游
- 实测结果记到 `GOALS.md` 的 G1

## 7. 排查

| 现象 | 看这里 |
|---|---|
| 页面上没有悬浮按钮 | 这个站不在内置列表里；用 popup 手动触发 |
| 填了但值不对 | 多半是多值字段选错了，点字段旁的 `▾` 切一下 |
| 下拉框/单选没填上 | 中文站爱用自定义控件，FormPilot 会模拟真人点击，但不是每种都认；手动补 |
| 改完资料没生效 | Dashboard 里确认存盘了，再刷新招聘页 |
| 想从源码重新构建 | 需要 Node 20+ 和 Yarn Classic：`yarn install && yarn build` |
