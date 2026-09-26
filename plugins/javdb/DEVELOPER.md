# JavDB 刮削插件 — 开发者文档

面向想阅读源码、修改逻辑或扩展功能的开发者。用户使用请看 **[README.md](README.md)**。

---

## 目录

- [文件职责](#文件职责)
- [数据流](#数据流)
- [关键实现细节](#关键实现细节)
  - [进度回调](#进度回调)
  - [番号匹配](#番号匹配)
  - [配置路径解析](#配置路径解析)
  - [首次页面加载超时](#首次页面加载超时)
  - [失败记录](#失败记录)
  - [进度表格](#进度表格)
  - [Excel 生成](#excel-生成)
  - [Cookie 导入](#cookie-导入)
- [扩展点](#扩展点)
- [已知问题](#已知问题)

---

## 文件职责

| 文件 | 职责 |
|---|---|
| `plugin.json` | 插件清单：name、version、entry、dependencies |
| `config.py` | `JavdbConfig` 数据类 + JSON 读写 |
| `fetch.py` | Playwright 爬取 + CSV 导出 |
| `parse.py` | 演员 / 评分 / 类别 / 评论字段提取（纯函数） |
| `report.py` | 生成带封面嵌入的 Excel |
| `tab.py` | GUI，实现 `Plugin` 协议 |

---

## 数据流

```
input_excel（第一列番号）
    │ load_targets()  读 Excel，跳过 NaN / "null" / 空
    ▼
targets: list[str]
    │ scrape_javdb()  Playwright 逐个搜索
    ▼
records: list[dict]  每个 dict 含 6 个字段
    │
    ├──► save_csv()  →  output_csv
    │
    └──► build_excel()
            │ parse_row() 提取演员 / 评分 / 类别 / 评论
            ▼
         output_excel  带封面嵌入的最终报告
```

### 爬取阶段的 record 结构

| 字段 | 说明 |
|---|---|
| `目标番号` | 用户输入的番号 |
| `链接` | 详情页 URL |
| `番号` | 详情页显示的实际番号 |
| `名称` | 标题 |
| `信息` | 详情页完整文本，供 `parse_row` 二次提取 |
| `评论` | 评论文本 |

失败时**仍会记录一条**：只填 `目标番号`，其余字段为空字符串。这保证最终报告里能看到失败的番号。

---

## 关键实现细节

### 进度回调

`scrape_javdb` 接受一个 `on_progress` 回调，签名：

```python
ProgressFn = Callable[[int, str, Optional[dict], str], None]
# (idx0, keyword, payload, status)
```

`payload` 是一个字典：

- `status == "ok"` 时：`{"fanhao": "...", "name": "..."}`
- 其他状态：`None`

之所以不用裸字符串传名称，是因为 GUI 需要**实际番号**做匹配判断，而不仅仅是名称。

### 番号匹配

`parse.is_matched(target, scraped)` 做归一化比较：

```python
def normalize_code(code: str) -> str:
    s = code.upper()
    s = s.replace("FC2PPV", "FC2").replace("FC2-PPV", "FC2")
    return s.replace("-", "").replace("_", "")
```

覆盖的场景：

| 目标 | 刮到的 | 结果 |
|---|---|---|
| `ABC-123` | `ABC-123` | ✓ |
| `abc-123` | `ABC-123` | ✓ |
| `ABC_123` | `ABC-123` | ✓ |
| `FC2-1234567` | `FC2PPV-1234567` | ✓ |
| `ABC-123` | `ABC-123B` | ✗ |

**注意**：目前用这个规则是因为 javdb 上有 `FC2PPV` 和 `FC2` 两种写法。如果你的场景有其它变体，在 `normalize_code` 里补规则即可。

### 配置路径解析

`JavdbPlugin._resolve_config_path`：

```python
def _resolve_config_path(self) -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and str(self.ctx.plugin_dir).startswith(str(meipass)):
        return Path(sys.executable).resolve().parent / "javdb_config.json"
    return self.ctx.plugin_dir / "config.json"
```

- **源码运行**：配置写到 `plugins/javdb/config.json`，随插件目录一起复制；
- **完整版 exe**：`plugin_dir` 在只读的 `_MEIPASS` 里，改为写到 exe 同级的 `javdb_config.json`。

### 首次页面加载超时

`page.set_default_timeout(config.page_timeout)` 设置了所有操作的默认超时。首次打开主页网络慢，单独传 5 倍：

```python
first_load_timeout = config.page_timeout * 5
await page.goto(
    "https://javdb.com/",
    wait_until="load",
    timeout=first_load_timeout,
)
```

后续搜索、点击等操作仍用 `config.page_timeout` 的默认值。

### 失败记录

爬取循环里，失败分支会往 `table_data` 补一条：

```python
table_data.append({
    "目标番号": keyword,
    "链接": "",
    "番号": "",
    "名称": "",
    "信息": "",
    "评论": "",
})
```

字段和成功记录完全一致（6 个 key），避免 pandas 建 DataFrame 时列错位。

**用户手动停止时不记录**，因为那些番号根本没跑。

### 进度表格

`tab.py` 的表格有四列：

| 列 | 宽度 | 内容 |
|---|---|---|
| 目标番号 | 140 | 用户输入的 |
| 实际番号 | 140 | 刮到的 |
| 名称 | 420 | 标题 |
| 状态 | 180 | 带颜色 |

Tag 颜色约定：

```python
self._tree.tag_configure("ok",      foreground="#1a7f37")   # 绿色
self._tree.tag_configure("running", foreground="#0a58ca")   # 蓝色
self._tree.tag_configure("failed",  foreground="#c0392b")   # 红色
self._tree.tag_configure("stopped", foreground="#999999")   # 灰色
self._tree.tag_configure("warn",    foreground="#c77b00")   # 橙色
```

### Excel 生成

`report.build_excel` 的列布局：

| 索引 | 宽度 | 内容 |
|---|---|---|
| 0 | 12.32 | 目标番号 |
| 1 | 12.32 | 番号 |
| 2 | 21.86 | 封面 |
| 3 | 35 | 名称 |
| 4 | 20 | 演员 |
| 5 | 3 | 空 |
| 6 | 12 | 评分 |
| 7 | 25 | 类别 |
| 8 | 3 | 空 |
| 9 | 50 | 评论 |

**行底色**：

```python
MISMATCH_BG = "#FFC7CE"    # 浅红：番号不一致
FAILED_BG   = "#FFF2CC"    # 浅黄：刮削失败
```

判断顺序：

```python
failed     = target and not scraped
mismatched = target and scraped and not is_matched(target, scraped)
```

**封面嵌入**：`worksheet.embed_image(row_idx, 2, img_path)`，列索引是 `2`（C 列）。行高由 `_estimate_row_height` 计算，按图片宽高比和列宽算出对应的 pt 值。

**NaN 处理**：CSV 里的空单元格读进 pandas 后是 `float('nan')`，直接传给 xlsxwriter 会报错。处理方式：

- `report.py` 中 `df = df.fillna("")`；
- `parse.py` 中提供 `_s()` 辅助函数，所有字符串字段都过一遍 `pd.isna()` 检查。

### Cookie 导入

`tab._on_import_cookies` 处理两种输入：

1. **已经是 Playwright storage_state**：`{"cookies": [...], "origins": []}`，直接取 `cookies`；
2. **EditThisCookie / Cookie-Editor 导出的 list**：把每个条目的 `sameSite` / `expirationDate` 转换为 Playwright 格式。

转换规则：

```python
SAMESITE_MAP = {
    "no_restriction": "None",
    "none": "None",
    "lax": "Lax",
    "strict": "Strict",
    "unspecified": "Lax",
}
```

`expires` 必须是整数，从 `expirationDate`（float）或 `expires` 转过来。

导入后自动写回 `_var_state` 并保存配置。

---

## 扩展点

### 抓取更多字段

在 `fetch._process_one` 里，从详情页额外提取字段，加到返回的 record 里。常见可以加：

- 发行日期（详情页有 `<strong>日期:</strong>` 之类的标签）
- 时长（`<strong>時長:</strong>`）
- 导演
- 制作商 / 发行商

然后在 `parse.parse_row` 里映射到最终列，在 `report.build_excel` 里增加列并调整列宽。

### 支持更多图片

`fetch._process_one` 目前只下载 `cover_url` 一张。想抓多张预览图：

1. 找到详情页的预览图选择器；
2. `await page.query_selector_all(...)` 拿到所有 URL；
3. 循环下载，文件名加后缀（`ABC-123_1.jpg` 等）。

### 更改搜索策略

现在固定"点击第一个结果"。想改成"点击标题匹配度最高的结果"：

1. `page.query_selector_all(".movie-list .item")` 拿到所有结果；
2. 遍历每个结果的标题，用相似度算法（如 `difflib.SequenceMatcher`）选最佳；
3. 找到对应的 `<a>` 元素再 `click`。

### 换用同步 Playwright

现在用 `async_playwright`，`tab._fetch_worker` 里用 `asyncio.run` 包裹。如果觉得异步调试麻烦，可以改成 `sync_playwright`，`tab._fetch_worker` 直接同步调用。改动集中在 `fetch.py`。

### 添加代理支持

在 `fetch.scrape_javdb` 里，`browser.new_context` 时传：

```python
context = await browser.new_context(
    storage_state=state_file,
    proxy={"server": "http://127.0.0.1:7890"},
)
```

配置项里加一个 `proxy` 字段即可。

---

## 已知问题

### Cloudflare 拦截

无头模式最容易触发。当前默认 `show_browser=True`，代价是必须有显示器。要在无头环境里跑，需要：

- 加载有效的登录状态；
- 降低请求频率；
- 使用真实浏览器指纹（Playwright 的 Chromium 默认指纹容易被识别）。

### Playwright 打包兼容性

PyInstaller 打 Playwright 需要 `--collect-all playwright`。不同版本之间行为略有差异，升级 Playwright 前先在测试目录试打。

### 番号格式差异

部分番号在 javdb 上有多种写法（`FC2PPV-1234567` vs `FC2-1234567`，`ABP-123` vs `ABP00123`）。`normalize_code` 目前只处理了 FC2 的前缀问题，其它格式差异需要用户手动调整输入。

### 评论提取的脆弱性

`parse.extract_comments` 依赖评论文本里的日期格式（`YYYY-MM-DD`）。javdb 改版后如果日期格式变了，提取会失败。当前有兜底，失败时返回「暂无评论」。

### 封面 URL 失效

部分老番号的封面 URL 可能已经 404。`fetch` 里会记录 `封面下载失败: HTTP 404`，但不会阻塞流程。

### `playwright install` 的离线问题

`playwright install msedge` 需要联网下载浏览器驱动。完整版 exe 里内置了驱动，但用户第一次运行插件时如果缺少驱动，会报错并提示执行 `playwright install`。