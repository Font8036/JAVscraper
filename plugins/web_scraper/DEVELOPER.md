# 爬虫插件（web_scraper）— 开发者文档

面向想阅读源码、修改逻辑或扩展功能的开发者。用户使用请看 **[README.md](README.md)**。

---

## 目录

- [设计目标](#设计目标)
- [文件职责](#文件职责)
- [插件结构](#插件结构)
- [Source 协议](#source-协议)
- [配置组织](#配置组织)
- [JavDB 源实现](#javdb-源实现)
  - [数据流](#javdb-数据流)
  - [进度回调](#进度回调)
  - [番号匹配](#番号匹配)
  - [首次页面加载超时](#首次页面加载超时)
  - [失败记录](#失败记录)
  - [Excel 生成](#excel-生成)
- [字幕猫源实现](#字幕猫-源实现)
  - [数据流](#字幕猫-数据流)
  - [搜索与选择策略](#搜索与选择策略)
  - [下载 / 翻译状态机](#下载--翻译状态机)
  - [超时机制](#超时机制)
  - [等待翻译轮次](#等待翻译轮次)
  - [语言映射](#语言映射)
  - [Excel 生成](#字幕猫-excel-生成)
- [共享能力](#共享能力)
  - [BrowserSession](#browsersession)
  - [notify](#notify)
  - [Cookie 导入](#cookie-导入)
- [扩展点](#扩展点)
  - [新增一个源](#新增一个源)
  - [JavDB 抓取更多字段](#javdb-抓取更多字段)
  - [字幕猫选择策略调优](#字幕猫选择策略调优)
- [已知问题](#已知问题)

---

## 设计目标

- **一个插件，多个源**。爬虫相关的功能（javdb 信息、字幕下载……）放同一个插件，共享 Playwright 依赖，避免重复打包 93 MB 的 Node 驱动。
- **源彼此隔离**。每个源有自己的配置类、UI、业务逻辑，互不干扰。加一个源 = 加一个目录 + 在注册表加一行。
- **公共能力下沉**。浏览器会话、完成提示、Cookie 转换这类跨源复用的能力，放在 `shared/` 里。
- **薄壳**。顶层 `tab.py` 只做三件事：建子 Notebook、转发 `config_pages()`、转发 `apply_layout()`。

---

## 文件职责

```
plugins/web_scraper/
├── plugin.json                       # 插件清单
├── __init__.py
├── config.py                         # ScraperPluginConfig：聚合各源配置
├── tab.py                            # WebScraperPlugin：顶层薄壳
├── shared/
│   ├── browser.py                    # BrowserSession
│   └── notify.py                     # 完成提示
└── sources/
    ├── __init__.py                   # Source 协议 + SOURCES 注册表
    ├── javdb/
    │   ├── config.py                 # JavdbSourceConfig
    │   ├── fetch.py                  # Playwright 爬取 + 读 Excel + CSV 导出
    │   ├── parse.py                  # 字段提取（纯函数）
    │   ├── report.py                 # 生成带封面嵌入的 Excel
    │   └── source.py                 # JavdbSource：UI + Source 协议
    └── subtitlecat/
        ├── config.py                 # SubtitleCatConfig
        ├── fetch.py                  # Playwright 搜索 + 下载 + 翻译重试
        ├── parse.py                  # 结果解析 + 语言映射 + 排序策略
        ├── report.py                 # 生成结果 Excel
        └── source.py                 # SubtitleCatSource：UI + Source 协议
```

| 文件 | 职责 |
|---|---|
| `plugin.json` | 插件清单，声明依赖 |
| `config.py` | 顶层配置，聚合各源的子配置 |
| `tab.py` | 顶层插件类，实现 `Plugin` 协议 |
| `shared/browser.py` | 独立 Playwright 会话，供用户手动登录 |
| `shared/notify.py` | 完成提示音 / Windows 系统通知 |
| `sources/__init__.py` | `Source` 协议 + `SOURCES` 列表 |
| `sources/javdb/*` | JavDB 源的全部实现 |
| `sources/subtitlecat/*` | 字幕猫源的全部实现 |

---

## 插件结构

### 顶层插件：`tab.py`

```python
class WebScraperPlugin:
    name = "爬虫"
    version = "1.0.0"

    def create_tab(self, notebook):
        root = ttk.Frame(notebook)
        inner = ttk.Notebook(root)
        inner.pack(fill="both", expand=True)
        self._sources = []
        for SourceCls in SOURCES:
            source = SourceCls(self, self.ctx)
            frame = source.build_tab(inner)
            if frame is not None:
                inner.add(frame, text=source.name)
                self._sources.append(source)
        return root

    def config_pages(self):
        pages = []
        for source in self._sources:
            page = source.config_page()
            if page is not None:
                pages.append(page)
        return pages

    def sync_from_config(self):
        for source in self._sources:
            source.sync_from_config()

    def apply_layout(self):
        for source in self._sources:
            source.apply_layout()
```

**职责极其单一**：

- **主界面 Tab**：一个 Notebook，每个源一个子 Tab；
- **配置窗口**：每个源提供自己的 `ConfigPage`，顶层汇总为一个列表；
- **生命周期转发**：`sync_from_config`、`apply_layout` 逐个源调用。

**不做的事**：不知道具体源的业务逻辑，不知道源里有几个按钮，不知道源配置长什么样。

### 主界面效果

```
1-扫描   2-移动   3-爬虫   4-Jellyfin NFO
                └── 内部子 Notebook
                    ├── JavDB 信息
                    └── 字幕猫
```

### 配置窗口效果

```
[核心配置]  [JavDB 信息]  [字幕猫]  [Jellyfin NFO]
```

每个源占一个平行的 Tab。

---

## Source 协议

定义在 `sources/__init__.py`：

```python
class Source(Protocol):
    name: str

    def __init__(self, plugin: Any, ctx: Any) -> None: ...

    def build_tab(self, parent) -> ttk.Frame: ...

    def config_page(self) -> ConfigPage | None: ...

    def is_busy(self) -> bool: ...

    def sync_from_config(self) -> None: ...

    def apply_layout(self) -> None: ...
```

| 方法 | 必须 | 说明 |
|---|---|---|
| `build_tab(parent)` | ✅ | 构建并返回主界面子 Tab 的 Frame |
| `config_page()` | ✅ | 返回一个 `ConfigPage`；返回 `None` 表示该源不参与配置窗口 |
| `is_busy()` | 建议 | 是否有后台任务；核心的关闭确认会用到 |
| `sync_from_config()` | 可选 | 核心刷新配置后通知源重新读取 |
| `apply_layout()` | 可选 | 核心应用布局偏好（表格/日志高度）时调用 |

**注册表**：

```python
from .javdb.source import JavdbSource
from .subtitlecat.source import SubtitleCatSource

SOURCES: list[type] = [
    JavdbSource,
    SubtitleCatSource,
]
```

**加新源 = 加一行 import + 在 SOURCES 里加一项**。顶层 `tab.py`、`config.py`、`app.py`、`plugin_loader.py` 都不用动。

---

## 配置组织

### 顶层配置：`config.py`

```python
@dataclass
class ScraperPluginConfig:
    javdb: JavdbSourceConfig = field(default_factory=JavdbSourceConfig)
    subtitlecat: SubtitleCatConfig = field(default_factory=SubtitleCatConfig)
```

**存储**：

- 源码运行：`plugins/web_scraper/config.json`
- 完整版 exe：exe 同级的 `scraper_config.json`

**JSON 结构**：

```json
{
  "javdb": { ... },
  "subtitlecat": { ... }
}
```

### 加载与保存

`ScraperPluginConfig.load`：

```python
for f in dataclasses.fields(cls):
    sub = data.get(f.name)
    if isinstance(sub, dict):
        sub_cls = _sub_config_class(f.name)
        if sub_cls is not None:
            kwargs[f.name] = sub_cls.from_dict(sub)
```

`_sub_config_class` 是**加新源时唯一需要改的地方之一**：

```python
def _sub_config_class(field_name: str):
    if field_name == "javdb":
        return JavdbSourceConfig
    if field_name == "subtitlecat":
        return SubtitleCatConfig
    return None
```

每个源的子配置类需要提供 `from_dict(data)` 类方法（用于过滤未知字段 + 做老版本兼容）。

### 源访问自己的配置

每个源持有 `self.plugin`（顶层插件）的引用，通过 property 读写：

```python
@property
def _config(self) -> JavdbSourceConfig:
    return self.plugin.config.javdb

@_config.setter
def _config(self, value: JavdbSourceConfig) -> None:
    self.plugin.config.javdb = value
```

**保存**：源通过 `self.plugin.save_config()` 触发顶层一次性写盘。

**好处**：

- 每个源只读自己那一段，不用关心 JSON 的整体结构；
- 顶层统一管理文件的读写路径；
- 加源时源不需要知道配置文件的路径。

---

## JavDB 源实现

### JavDB 数据流

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

| 目标 | 刮到的 | 结果 |
|---|---|---|
| `ABC-123` | `ABC-123` | ✓ |
| `abc-123` | `ABC-123` | ✓ |
| `ABC_123` | `ABC-123` | ✓ |
| `FC2-1234567` | `FC2PPV-1234567` | ✓ |
| `ABC-123` | `ABC-123B` | ✗ |

### 首次页面加载超时

`page.set_default_timeout(config.page_timeout_ms)` 设置了所有操作的默认超时。首次打开主页网络慢，单独传 5 倍：

```python
first_load_timeout = config.page_timeout_ms * 5
await page.goto(
    "https://javdb.com/",
    wait_until="load",
    timeout=first_load_timeout,
)
```

**进入详情页**也建议放宽：

```python
await page.goto(
    full_url,
    wait_until="domcontentloaded",
    timeout=config.page_timeout_ms * 2,
)
```

`domcontentloaded` 比 `load` 快很多（不等图片/字体），能解决首屏超时问题。

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

字段和成功记录完全一致（6 个 key），避免 CSV 列错位。

**用户手动停止时不记录**，因为那些番号根本没跑。

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

**行底色**（当前配色）：

```python
MISMATCH_BG = "#FFF2CC"    # 浅黄：番号不一致
FAILED_BG   = "#FFC7CE"    # 浅红：刮削失败
```

判断顺序：

```python
failed     = target and not scraped
mismatched = target and scraped and not is_matched(target, scraped)
```

**评分列始终写成超链接**（只要链接存在）：

```python
if rec["链接"]:
    display = rec["评分"] or "无评分"
    worksheet.write_url(row_idx, 6, rec["链接"], link_fmt, display)
else:
    worksheet.write(row_idx, 6, rec["评分"] or "", cell_fmt)
```

**封面嵌入**：`worksheet.embed_image(row_idx, 2, img_path)`，列索引是 `2`（C 列）。行高由 `_estimate_row_height` 计算。

---

## 字幕猫源实现

### 字幕猫数据流

```
input_excel（第一列番号）
    │ load_targets()  openpyxl 读
    ▼
targets: list[str]
    │
    ▼
[第一轮] 逐番号搜索 → 解析结果 → 四级排序选最佳 → 进详情页
    │
    ├── 有 #download_zh-CN → 直接下载
    ├── 有 #zh-CN → 点翻译，记 deadline，进 pending
    └── 都没有 → 失败
    │
    ▼
[后续轮] 逐个 pending 项
    │
    ├── 超时？（先尝试，再判超时）
    ├── 有下载按钮 → 下载成功（可能超时后抢救成功）
    ├── 有翻译按钮 → 继续 pending
    └── 都没有 → 失败
    │
    ▼
records: list[dict]  →  build_excel()  →  output_excel
```

### 搜索与选择策略

**解析搜索结果**（`fetch._parse_search_results`）：

```python
rows = await page.query_selector_all(".sub-table tbody tr")
for row in rows:
    td_el = await row.query_selector("td:nth-of-type(1)")
    a_el = await td_el.query_selector("a")
    # 关键：文本从 td 拿（含 (translated from Xxx)），链接从 a 拿
    raw_text = (await td_el.inner_text()).strip()
    title_raw = re.sub(r"\s+", " ", raw_text).strip()
    href = await a_el.get_attribute("href") or ""

    clean, lang_raw, lang_cn, is_cn = parse_title(title_raw)

    stars_el = await row.query_selector(".sub-table__stars")
    has_up = "👍" in (await stars_el.inner_text())

    metric_els = await row.query_selector_all(".sub-table__metric-value")
    downloads = await _parse_metric(metric_els[1]) if len(metric_els) >= 2 else 0
    languages = await _parse_metric(metric_els[2]) if len(metric_els) >= 3 else 0
```

**注意**：

- **文本用 `td`，链接用 `td a`**——后缀 `(translated from Xxx)` 在 `<a>` 之外；
- `_parse_metric` 是 `async` 函数，必须 `await`；
- `inner_text` 会把 HTML 换行变成实际 `\n`，用 `re.sub(r"\s+", " ", ...)` 归一化。

**四级排序**（`parse.pick_best`）：

```python
def sort_key(r: SearchResult):
    code = extractor.extract(r.title_clean)
    level1 = 1 if code and normalize_code(code) == target_norm else 0
    level2 = 1 if r.has_thumbs_up else 0
    level3 = 1 if r.translated_from_chinese else 0
    level4 = r.downloads
    return (level1, level2, level3, level4)

return max(results, key=sort_key)
```

利用 Python 元组的字典序比较实现四级优先。**只要 `results` 非空，`max` 一定会返回一条**。

**注意**：`pick_best` 接收核心的 `CodeExtractor` 实例，由 `SubtitleCatSource.__init__` 从 `ctx.app_config.scraper` 构造：

```python
from av_scraper.scraper import CodeExtractor
self._extractor = CodeExtractor(ctx.app_config.scraper)
```

### 下载 / 翻译状态机

`fetch._try_download_or_translate(page, keyword, config, log)` 返回三种状态：

```python
async def _try_download_or_translate(page, keyword, config, log) -> str:
    # 1. 有 #download_zh-CN → 直接下载
    dl = await page.query_selector("#download_zh-CN")
    if dl is not None and await dl.is_visible():
        return await _do_download(page, keyword, config, log)

    # 2. 有 #zh-CN → 点翻译
    zh = await page.query_selector("#zh-CN")
    if zh is not None and await zh.is_visible():
        await zh.click()
        log(f"{keyword}: 已点击翻译按钮，等待生成…")
        return "等待翻译"

    # 3. 都没有
    return "下载失败"
```

- **同页变元素**：点击 `#zh-CN` 后，网站会在**同一个页面**生成中文版本，稍后刷新即可看到 `#download_zh-CN`；
- 因此 pending 项的重试方式是**用记下的 href 直接重新进入详情页**，不用重新搜索。

### 超时机制

**全局秒表**方案：每个番号第一次点 `#zh-CN` 时记录一个 deadline：

```python
pending[idx] = {
    "href": record["_href"],
    "deadline": time.monotonic() + config.translate_timeout,
}
```

- 用 `time.monotonic()` 而非 `time.time()`——单调时钟不受系统时间调整影响；
- 每轮开始时检查 `time.monotonic() > item["deadline"]`；
- 一个全局时钟同时服务所有番号，O(n) 存储，无额外线程。

### 等待翻译轮次

```python
while pending and not stop:
    await asyncio.sleep(config.round_delay)

    next_pending = {}
    for idx, item in list(pending.items()):
        keyword = targets[idx]
        record = records[idx]
        expired = time.monotonic() > item["deadline"]

        # 关键：先尝试，再决定
        status = "下载失败"
        try:
            full_url = urljoin(config.base_url, item["href"])
            await page.goto(full_url, wait_until="domcontentloaded",
                            timeout=config.page_timeout_ms * 2)
            status = await _try_download_or_translate(
                page, keyword, config, log)
        except Exception as e:
            log(f"{keyword}: 重试异常：{e}")

        if status == "下载成功":
            # 即使超时，本次成功也认
            record["status"] = _finalize_success(record, keyword)
        elif status == "等待翻译":
            if expired:
                record["status"] = "下载失败（翻译超时）"
            else:
                next_pending[idx] = item
        else:
            record["status"] = status

    pending = next_pending
```

**核心改进**（相对早期版本）：**超时判断放在"尝试之后"而不是"尝试之前"**。这解决了"队列很长，早期点的翻译番号排到时已经超时，但其实网站已经生成好了"的场景。

### 语言映射

`parse._LANG_MAP`：中文名映射表。

```python
_LANG_MAP = {
    "chinese": "中文",
    "chinese (simplified)": "简体中文",
    "chinese (traditional)": "繁体中文",
    "english": "英语",
    "japanese": "日语",
    ...
}
```

**未在表中的语言显示原文**。

**正则提取后缀**：

```python
_TRANSLATED_RE = re.compile(
    r"\(\s*translated\s+from\s+([^)]+?)\s*\)",
    re.IGNORECASE,
)
```

**标题清理**：`_TRANSLATED_RE.sub("", raw)` 去掉后缀，剩余的空白再压缩。

### 字幕猫 Excel 生成

`report.build_excel` 的列布局：

| 索引 | 宽度 | 内容 |
|---|---|---|
| 0 | 14 | 目标番号 |
| 1 | 14 | 搜索结果数量（带超链接） |
| 2 | 14 | 实际番号 |
| 3 | 60 | 名称 |
| 4 | 12 | 源语言 |
| 5 | 10 | 下载量 |
| 6 | 22 | 状态 |

**搜索结果数量列**是超链接：

```python
if url:
    worksheet.write_url(row_idx, 1, url, link_fmt, str(count))
```

`url` 来自 `record["_search_url"]`，格式：

```python
f"{config.base_url.rstrip('/')}/index.php?search={quote(keyword)}"
```

**行底色**：

```python
FAILED_BG   = "#FFC7CE"    # 浅红：失败
MISMATCH_BG = "#FFF2CC"    # 浅黄：番号不一致
```

判断顺序：

```python
is_failed = ("下载失败" in status
             or status == "未搜到"
             or status == "已停止")
is_mismatch = "番号不一致" in status
```

**状态列的判断逻辑**（`fetch._finalize_success`）：

```python
def _finalize_success(record: dict, keyword: str) -> str:
    code = record.get("actual_code", "")
    if code and not is_matched(keyword, code):
        return "下载成功（番号不一致）"
    return "下载成功"
```

---

## 共享能力

### BrowserSession

`shared/browser.py` 里的独立会话，供用户手动登录后保存状态。

**接口**：

```python
session = BrowserSession(
    base_url="https://javdb.com/",
    channel="msedge",
    state_file="D:/javdb_state.json",
    on_log=some_callback,
)
session.start()
# ...用户操作...
session.stop()          # 请求保存并关闭

session.ready           # 是否已就绪
session.done            # 后台线程是否已结束
session.error           # 异常（若有）
```

**关键实现**：

- 后台线程跑 `asyncio.run(self._async_main())`，主线程通过 `threading.Event` 通信；
- **双重检测浏览器是否被关闭**：
  1. `browser.on("disconnected", ...)` 事件；
  2. 轮询 `context.pages` 里有没有活着的页面。

  单靠 `browser.is_connected()` 在 Windows + 系统 Edge 场景下不可靠（后台驻留进程保持 CDP 连接）。

- **保存登录状态**：用户通过程序按钮关闭时才保存，直接点浏览器 × 不保存（会记警告）。

**每个源各自持有一个 BrowserSession 实例**。不共享浏览器会话——不同网站登录需求不同，共享反而复杂。

### notify

`shared/notify.py` 提供两个函数：

```python
play_beep()                                    # 播放系统提示音
show_toast(title, message, duration_ms=8000)   # Windows 系统通知
```

- `play_beep` 用 `winsound.MessageBeep`（异步，非阻塞）；
- `show_toast` 用 PowerShell 调用 `System.Windows.Forms.NotifyIcon`，`Popen` 异步执行，`creationflags=CREATE_NO_WINDOW` 防止黑窗闪现；
- 非 Windows 平台两个函数都静默返回。

### Cookie 导入

每个源都实现了自己的 `_on_import_cookies`，逻辑基本一致：

- 从 UI 读「登录状态」路径；
- 弹 `filedialog` 让用户选浏览器扩展导出的 JSON；
- 转换成 Playwright storage_state 格式；
- 写入目标路径；
- 更新 `self._config.state_file`。

**可抽取为共享函数**（下一步优化的地方）：

```python
# shared/cookies.py
def import_browser_cookies(raw) -> list[dict]: ...
```

当前是分别在两个源的 `source.py` 里各写了一遍 `_convert_cookies`。

---

## 扩展点

### 新增一个源

1. **建目录**：`sources/my_source/`
2. **写 `config.py`**：

   ```python
   @dataclass
   class MySourceConfig:
       base_url: str = field(default="...", metadata={...})
       # ... 其他字段

       @classmethod
       def from_dict(cls, data: dict) -> "MySourceConfig":
           known = {f.name for f in dataclasses.fields(cls)}
           filtered = {k: v for k, v in data.items() if k in known}
           return cls(**filtered)
   ```

3. **写 `source.py`**：实现 `Source` 协议。参考 `javdb/source.py` 的骨架：

   ```python
   class MySource:
       name = "我的源"

       def __init__(self, plugin, ctx):
           self.plugin = plugin
           self.ctx = ctx
           # UI 控件引用、队列、工作状态等

       @property
       def _config(self) -> MySourceConfig:
           return self.plugin.config.my_source

       @_config.setter
       def _config(self, value: MySourceConfig):
           self.plugin.config.my_source = value

       def build_tab(self, parent) -> ttk.Frame: ...
       def config_page(self) -> ConfigPage: ...
       def is_busy(self) -> bool: ...
       def apply_layout(self) -> None: ...
   ```

4. **注册**：`sources/__init__.py`：

   ```python
   from .my_source.source import MySource
   SOURCES = [JavdbSource, SubtitleCatSource, MySource]
   ```

5. **配置聚合**：`web_scraper/config.py`：

   ```python
   @dataclass
   class ScraperPluginConfig:
       javdb: JavdbSourceConfig = field(default_factory=JavdbSourceConfig)
       subtitlecat: SubtitleCatConfig = field(default_factory=SubtitleCatConfig)
       my_source: MySourceConfig = field(default_factory=MySourceConfig)

   def _sub_config_class(field_name: str):
       if field_name == "javdb": return JavdbSourceConfig
       if field_name == "subtitlecat": return SubtitleCatConfig
       if field_name == "my_source": return MySourceConfig
       return None
   ```

6. **打包**：`run_full.py` 加对应的 `import`，打包命令加 `--collect-submodules web_scraper`（已有）。

**不用改**：`tab.py`、`app.py`、`plugin_loader.py`、`config_window.py`、`config_tab.py`。

### JavDB 抓取更多字段

在 `fetch._process_one` 里，从详情页额外提取字段，加到返回的 record 里。常见可以加：

- 发行日期（详情页有 `<strong>日期:</strong>` 之类的标签）
- 时长
- 导演
- 制作商 / 发行商

然后在 `parse.parse_row` 里映射到最终列，在 `report.build_excel` 里增加列并调整列宽。

### 字幕猫选择策略调优

**调整优先级**：改 `parse.pick_best` 的 `sort_key` 返回值。

**加语言数量作为第五级**：

```python
def sort_key(r):
    return (
        level1, level2, level3, level4,
        r.languages,        # 新增
    )
```

**调整超时容忍度**：改 `SubtitleCatConfig` 的 `translate_timeout` 默认值。

**调整重试间隔**：改 `round_delay` 默认值。

**换下载页面的元素选择器**：站点改版时，改 `fetch._try_download_or_translate` 里的 `#download_zh-CN` / `#zh-CN`。

---

## 已知问题

### 两个源的 BrowserSession 各持一份

用户需要为 javdb 和 subtitlecat 各登录一次。这是设计选择（源隔离），不是 bug。如果将来觉得麻烦，可以抽一个"共享浏览器"或"跨站点 storage_state 合并"机制。

### 字幕猫的状态机缺少"暂停"能力

爬取过程中只有"停止"，没有"暂停"。对于长时间运行的翻译等待轮次，用户想暂停得用停止。将来可以加"暂停"（用一个 `threading.Event` 控制）。

### JavDB 的 Cloudflare 拦截

无头模式最容易触发。当前默认 `show_browser=True`，代价是必须有显示器。要在无头环境里跑，需要加载有效的登录状态 + 降低请求频率 + 真实指纹。

### 番号格式差异

`normalize_code` 目前只处理了 FC2 的前缀问题，其它格式差异需要用户手动调整输入。

### 评论提取的脆弱性

JavDB 的 `parse.extract_comments` 依赖评论文本里的日期格式（`YYYY-MM-DD`）。javdb 改版后如果日期格式变了，提取会失败。当前有兜底，失败时返回「暂无评论」。

### 封面 URL 失效

部分老番号的封面 URL 可能已经 404。`fetch` 里会记录 `封面下载失败: HTTP 404`，但不会阻塞流程。

### Playwright 打包兼容性

PyInstaller 打 Playwright 需要 `--collect-all playwright`。不同版本之间行为略有差异，升级 Playwright 前先在测试目录试打。

### 首次进入 JavDB 详情页超时

原因是首次跳转要加载完整页面（CSS/JS/图片），5 秒不够。**建议**：

- 详情页用 `wait_until="domcontentloaded"`；
- 超时用 `page_timeout_ms * 2`。

字幕猫源已经是这个做法，JavDB 源也可以统一。

### Cookie 导入逻辑在两个源里各写一遍

`_convert_cookies` 在两个 `source.py` 里几乎一样。可以抽到 `shared/cookies.py`。当前没做——两个源独立演进，暂时能容忍。

### 表格字段命名不统一

JavDB 和字幕猫用 `self._result_tree`，Jellyfin NFO 用 `self.result_tree`。功能上无影响，代码走读时容易搞混。

### `apply_layout` 需要每个源显式实现

新增源时容易忘。可以考虑用默认实现：

```python
# 在 Source 协议里提供默认
class BaseSource:
    def apply_layout(self) -> None:
        pass
```

让源继承即可。当前没做——两个源都手写了，量不大。