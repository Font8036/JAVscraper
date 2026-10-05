# AV 文件名刮削与整理工具 — 开发者文档

面向想阅读源码、扩展功能或自行构建的开发者。普通使用请看 **[README.md](README.md)**；具体插件的使用和实现见各插件目录下的文档。

---

## 目录

- [项目定位与设计原则](#项目定位与设计原则)
- [目录结构](#目录结构)
- [环境搭建](#环境搭建)
- [核心模块](#核心模块)
  - [scraper.py](#scraperpy)
  - [processor.py](#processorpy)
  - [reports.py](#reportspy)
  - [config.py](#configpy)
  - [paths.py](#pathspy)
- [Widgets 组件库](#widgets-组件库)
  - [ConfigForm](#configform)
  - [SortableTreeview](#sortabletreeview)
  - [HistoryPathInput](#historypathinput)
  - [ToolTip / QuestionMark / LabelWithTip](#tooltip-questionmark-labelwithtip)
- [GUI 层](#gui-层)
  - [主窗口](#主窗口)
  - [配置窗口](#配置窗口)
  - [Tab 通用模式](#tab-通用模式)
- [插件系统](#插件系统)
  - [接口约定](#接口约定)
  - [加载流程](#加载流程)
  - [plugin.json 格式](#pluginjson-格式)
  - [源（Source）子结构](#源source子结构)
  - [开发一个新插件](#开发一个新插件)
  - [内置插件一览](#内置插件一览)
- [构建与分发](#构建与分发)
  - [核心版](#核心版)
  - [完整版](#完整版)
- [测试](#测试)
- [扩展点](#扩展点)
- [已知限制](#已知限制)
- [版本与兼容](#版本与兼容)

---

## 项目定位与设计原则

- **核心零依赖**。除标准库外不引入任何第三方包，让核心版保持在 15 MB 量级、能被单文件 exe 打包。
- **核心与 GUI 解耦**。`scraper` / `processor` / `reports` 是纯逻辑，可单测、可 CLI、可接任意 GUI。
- **配置即数据**。所有可调项都是 dataclass 字段，GUI 依据 metadata 自动生成控件，加字段不改 UI 代码。
- **插件与核心彻底分离**。核心不 import 插件的任何业务模块，只通过 `plugin_api.py` 约定接口。
- **插件内部再分"源"**。一个插件可以包含多个独立的功能单元（如 `web_scraper` 下有 JavDB 信息和字幕猫两个源），源之间彼此隔离。
- **UI 组件化**。表格、配置表单、路径输入等重复结构抽成组件，放在 `widgets/` 目录供核心和插件共同使用。
- **单文件入口 + 打包脚本**。源码和打包共用同一份代码，通过 `run.py` / `run_full.py` 区分核心版与完整版。

---

## 目录结构

```
JAVscraper/
├── pyproject.toml
├── README.md                       # 面向用户
├── DEVELOPER.md                    # 面向开发者（本文件）
├── .gitignore
├── .vscode/
│   └── settings.json               # 需将 "plugins" 加入 analysis.extraPaths
│
├── run.py                          # 核心版入口
├── run_full.py                     # 完整版入口（内置所有插件）
├── 清除缓存.bat                     # 递归清理 __pycache__
│
├── config.json                     # 核心配置，首次运行自动生成
├── scraper_config.json             # 爬虫插件配置，自动生成
├── jellyfin_nfo_config.json        # Jellyfin NFO 插件配置，自动生成
├── log/                            # 输出与日志，自动生成
│
├── plugins/                        # 插件目录
│   ├── web_scraper/                # 爬虫插件
│   │   ├── plugin.json
│   │   ├── README.md               # 插件文档
│   │   ├── __init__.py
│   │   ├── config.py               # ScraperPluginConfig：各源子配置聚合
│   │   ├── tab.py                  # WebScraperPlugin：内部子 Notebook
│   │   ├── shared/                 # 插件内公共能力
│   │   │   ├── browser.py          # BrowserSession：Playwright 会话
│   │   │   └── notify.py           # 声音 / 系统通知
│   │   └── sources/                # 各个爬取源
│   │       ├── __init__.py         # Source 协议 + SOURCES 注册表
│   │       ├── javdb/              # JavDB 信息源
│   │       │   ├── __init__.py
│   │       │   ├── config.py       # JavdbSourceConfig
│   │       │   ├── fetch.py
│   │       │   ├── parse.py
│   │       │   ├── report.py
│   │       │   └── source.py       # JavdbSource（UI + Source 协议）
│   │       └── subtitlecat/        # 字幕猫源
│   │           ├── __init__.py
│   │           ├── config.py       # SubtitleCatConfig
│   │           ├── fetch.py
│   │           ├── parse.py
│   │           ├── report.py
│   │           └── source.py       # SubtitleCatSource
│   │
│   └── jellyfin_nfo/               # Jellyfin NFO 插件
│       ├── plugin.json
│       ├── README.md
│       ├── __init__.py
│       ├── config.py
│       ├── parser.py
│       ├── excel_reader.py
│       ├── image_extractor.py
│       ├── nfo_builder.py
│       ├── organizer.py
│       └── tab.py                  # 兼作插件与配置页提供者
│
├── test/                           # 测试与生成脚本
│   ├── gen_test_files.py
│   └── .gitignore
│
└── av_scraper/                     # 主包
    ├── __init__.py                 # __version__
    ├── __main__.py                 # python -m av_scraper 入口
    ├── paths.py                    # 定位 config/log/plugins
    ├── defaults.py                 # 默认前缀与扩展名
    ├── config.py                   # AppConfig / ScraperConfig / ProcessorConfig
    ├── scraper.py                  # 文件名解析
    ├── processor.py                # 文件移动 / 撤回
    ├── reports.py                  # JSON / TXT / CSV 导出
    ├── plugin_api.py               # 插件接口定义（PluginContext）
    ├── plugin_loader.py            # 插件发现与加载
    ├── widgets/                    # 可复用 UI 组件
    │   ├── __init__.py
    │   ├── config_form.py          # ConfigForm / calc_label_width
    │   ├── history_path.py         # HistoryPathInput
    │   ├── sortable_treeview.py    # SortableTreeview / Column
    │   └── tooltip.py              # ToolTip / QuestionMark / LabelWithTip
    └── gui/
        ├── __init__.py
        ├── common.py               # QueueLogHandler / human_size
        ├── app.py                  # 主窗口（含插件加载、状态栏）
        ├── config_window.py        # ConfigPage / ConfigWindow（统一配置窗口）
        ├── scan_tab.py
        └── move_tab.py
```

---

## 环境搭建

### 源码运行

```bash
git clone https://github.com/Font8036/JAVscraper.git
cd JAVscraper
pip install -e .
```

### 开发环境（含打包、测试）

```bash
pip install -e ".[dev]"
```

### 插件依赖

```bash
# 爬虫插件（包含 JavDB + 字幕猫）
pip install -e ".[web_scraper]"
playwright install msedge

# Jellyfin NFO 插件
pip install -e ".[jellyfin]"

# 所有插件
pip install -e ".[full]"
```

### VSCode 建议配置

`.vscode/settings.json`：

```json
{
  "python.analysis.extraPaths": [".", "plugins"],
  "python.defaultInterpreterPath": "<你的解释器路径>"
}
```

### 清理缓存

项目根目录的 `清除缓存.bat` 会递归清理所有 `__pycache__` 目录。**改了插件代码后，如果行为不符预期，先双击它一次再重启程序**——这是避免"改了代码但 Python 加载的还是旧 pyc"这类诡异问题的常用手段。

---

## 核心模块

### scraper.py

**职责**：从文件名提取标准代码。

**关键类**：`CodeExtractor(config: ScraperConfig)`

- 构造时预编译所有正则，`scan_directory` 里不再做编译；
- 三种识别策略，按优先级：FC2 → 字母前缀 → 6 位数字前缀；
- 前缀按**长度降序**匹配，保证最长优先（如 `ABCD` 优先于 `BCD`）；
- 前缀列表在构造时**去重**（`sorted(set(...))`），配置里可以有重复项；
- 大小写不敏感：匹配前输入被 `.upper()`，前缀也在编译时统一大写。

**数据类**：`ScrapeResult`

```python
@dataclass
class ScrapeResult:
    file_path: str
    filename: str
    extracted_code: str
    status: str         # "extracted" | "original"
    file_size: int
    inherited: bool = False   # 是否为父目录继承
```

**扫描接口**：

```python
def scan_directory(
    self,
    directory: str | Path,
    recursive: Optional[bool] = None,
    progress: Optional[Callable[[ScrapeResult], None]] = None,
    on_total: Optional[Callable[[int], None]] = None,
) -> list[ScrapeResult]:
```

**注意点**：

- `rglob` / `glob` 返回的迭代器**只能消费一次**。当前实现先收集候选列表再逐个处理，两轮循环分别用 `iterator` 和 `candidates`。**这是一个容易踩的坑**——曾导致扫描结果为空；
- 总数回调 `on_total` 在收集完成后触发一次；单文件回调 `progress` 在每个文件处理完后触发；
- 从父目录继承番号：文件名匹配失败时，尝试用父文件夹名再匹配一次。这是为了处理 `ABP-123/xxx.mp4` 这种"文件夹带番号、文件名不带"的结构。

**扩展识别规则**：

1. 在 `_compile_patterns` 里按需新增预编译正则集合；
2. 在 `extract()` 里按优先级调用新的匹配方法；
3. 注意返回的是标准化代码（如 `ABC-123`），大小写和分隔符统一。

---

### processor.py

**职责**：按刮削结果规划并执行文件移动。

**关键类**：`FileProcessor(config: ProcessorConfig)`

**核心流程**：

```
results(JSON list) --plan--> [PlannedOperation] --execute--> [MoveOperation]
```

- `plan()` 是**纯计算**，不碰磁盘。冲突策略（`skip` / `overwrite` / `rename`）在此阶段决定；
- `execute()` 才真正动文件，并在执行后返回 `MoveOperation` 列表，供落盘供撤回；
- `undo()` 按 `move_operations.json` 记录逆向操作。

**数据类**：

```python
@dataclass
class PlannedOperation:
    src: Path
    dst: Path
    status: str               # "move" | "rename" | "skip" | "overwrite"
    extracted_code: str = ""  # 番号，供移动页表格显示

@dataclass
class MoveOperation:
    original_path: str
    moved_to: str
    original_filename: str
    target_filename: str
    extracted_code: str
```

**注意点**：

- `plan()` 里用 `taken: set[str]` 跟踪本次已规划的目标路径，避免同批次内的冲突；
- 撤回记录只保留最近一次，`undo()` 成功后会删除记录文件。**如果撤回部分失败，记录文件保留**，用户可以再次重试；
- 移动操作前会 `mkdir -p` 目标目录；`overwrite` 会先 `unlink` 再 `move`。

**执行与预览一致性**：

移动页的做法是"预览时把 `PlannedOperation` 列表缓存下来，执行时**直接用它**"，不再重新 plan。这样保证"执行的" = "看到的"。用户如果改了输入 JSON 但没重新预览，执行时用的是旧计划。

---

### reports.py

**职责**：扫描结果导出。

**函数**：

- `make_run_directory(base) -> Path`：按 `YYYYMMDD_HHMMSS` 建时间戳目录；
- `save_json(results, path)`
- `save_text_report(results, path)`
- `save_csv(results, path)`

**输出格式**：

| 文件 | 内容 |
|---|---|
| `scraper_results.json` | 供 processor 读取的完整结果 |
| `extraction_report.txt` | 人可读，含统计与明细 |
| `extraction_table.csv` | 三列（文件名 / 提取码 / 来源）+ 统计信息 |

---

### config.py

**职责**：配置的数据模型与 JSON 读写。

**数据类结构**：

```
AppConfig
├── scraper: ScraperConfig
├── processor: ProcessorConfig
├── confirm_on_close: bool
├── max_recent_dirs: int
├── log_height: int
├── table_height: int
└── (插件配置由各插件自行管理)
```

**字段声明方式**：

```python
@dataclass
class ScraperConfig:
    output_directory: str = field(
        default="",
        metadata={"label": "输出目录", "kind": "dir"},
    )
    ...
```

`metadata` 字段约定：

| key | 取值 | 说明 |
|---|---|---|
| `label` | str | 界面显示的标签 |
| `kind` | `str` / `dir` / `file` / `save` / `bool` / `choice` / `int` / `float` / `list` | 控件类型 |
| `choices` | list | `kind="choice"` 时的选项 |
| `big` | bool | `kind="list"` 时是否用高文本框 |
| `hidden` | bool | 是否在配置页隐藏（如历史记录、嵌套 dataclass） |
| `row_group` | str | 相邻且同名的字段排在同一行 |
| `tooltip` | str | 悬停提示 |
| `ext` | str | `kind="file"` / `"save"` 时的扩展名过滤与自动补全 |
| `width` | int | 输入框宽度 |

**加字段后的自动行为**：

- 配置窗口自动出现对应控件（`ConfigForm._build` 遍历 dataclass fields）；
- `_collect` 用当前实例值填充 `hidden` 字段，避免被默认值覆盖；
- `AppConfig.load` 用 `dataclasses.fields()` 过滤未知键，向后兼容。

**列表解析规则**：每行按换行拆，再按逗号拆，去空白去引号。

---

### paths.py

**职责**：统一路径定位。

```python
def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

def config_path() -> Path: ...
def log_dir() -> Path: ...
```

**关键**：源码运行时返回**项目根目录**（`av_scraper/paths.py` 的上两级），与 cwd 无关。打包后返回 exe 同级。

---

## Widgets 组件库

`av_scraper/widgets/` 下的组件对核心 Tab 和插件 Tab 一视同仁。设计原则：**只依赖 tkinter，不 import `gui/` 或 `plugins/`，不感知具体配置类**。

### ConfigForm

**职责**：按 dataclass 定义自动渲染一组配置控件。

**接口**：

```python
form = ConfigForm(parent, config_cls, instance=None, *, label_width=None)
form.load_from(instance)      # 从实例填充控件
new_cfg = form.collect(base=None)  # 从控件读值，返回新实例
form.get_row_frame(field_name)     # 拿到某字段所在行，可往里追加控件
```

**行为**：

- 遍历 dataclass 字段，跳过 `hidden=True` 的；
- 按 `kind` 决定控件类型：`str/dir/file/save` → 输入框（`dir`/`file`/`save` 带「浏览…」）；`bool` → 勾选框；`choice` → 下拉框；`int` / `float` → Spinbox；`list` → Text；
- `row_group` 相同的相邻字段排在一行（同一行只能含相同 kind，或 `bool` 一组）；
- 每个字段左侧标签宽度由 `calc_label_width` 计算——**只按有独立左侧标签的字段计算**，跳过 `bool`（文字在按钮右侧）和 `list`（标签在顶部）以及 `row_group` 内的字段；
- `bool` 行有 2 字符缩进，短字段（`int` / `float` / `choice`）也有同样缩进，长条输入框贴左。

**扩展点**：

- 加新 kind：在 `_make_widget` 里加分支，并同步 `_read_widget` / `_write_widget`；
- 加"字段提示行"这类非配置元素：用 `get_row_frame` 往对应行追加控件，或改外层容器。

### SortableTreeview

**职责**：带排序、搜索、勾选框的 Treeview 封装。

**接口**：

```python
stv = SortableTreeview(
    parent,
    columns=[Column(...), ...],
    key=lambda d: ...,          # 稳定 id，默认 str(id(d))
    row_tags=lambda d: (...),   # 每行的 tag
    tag_configure={...},
    height=...,
    searchable=True,
    on_sort_changed=...,
    on_selection_changed=...,
)
stv.set_data(rows, select_all=True)
stv.append_row(row, scroll=True, select=None)
stv.update_row(row)
stv.see_row(row)
stv.clear()
stv.get_selected()        # 全部勾选的数据
stv.get_selected_visible() # 当前可见且勾选
stv.set_all_selected(True/False)
stv.tree                   # 底层 ttk.Treeview
```

**排序三态**：点表头 → 升序 → 降序 → 原始顺序 → 升序…

**搜索**：

- 搜索栏由 `searchable=True` 生成；
- 列选择下拉可切到"全部列"、"某个列名"、"正则"；
- 勾选框开启时，下拉里额外多"已勾选"、"未勾选"两个筛选选项（**通过 `filter_var` 下拉实现，不在列选择下拉里**——当前实现用独立的筛选下拉）。

**勾选框**：

- `Column(kind="checkbox")` 声明一个勾选列；
- 符号 `☑` / `☐` / `▣`，点击整行切换；
- 表头点击 = 对**当前可见行**全选/全不选；
- 已勾选行自动加 `__checked__` tag（浅蓝背景）；
- 勾选变化会触发 `on_selection_changed`。

**数据是黑盒**：组件不感知数据结构，`Column.display` / `Column.sort` 回调负责把数据翻译成显示值和排序键。

### HistoryPathInput

**职责**：带历史下拉的路径输入框。

**接口**：

```python
inp = HistoryPathInput(
    parent,
    label="扫描目录:",
    kind="dir",
    get_value=lambda: ...,
    set_value=lambda v: ...,
    get_history=lambda: [...],
    set_history=lambda h: ...,
    get_limit=lambda: 5,
    save=lambda: ...,
    label_width=0,       # 0 = 贴左；>0 = 固定宽度右对齐
    tooltip="...",
)
inp.get()       # 取值
inp.commit()    # 把当前值记入历史
inp.refresh()   # 从配置重读历史
```

**设计**：通过回调读写，不 import 任何具体配置类。核心和插件都能用。

**行为**：

- 点击输入框任意位置都展开下拉；
- `commit_on_browse=True` 时，用户点「浏览…」选出的目录会立刻进历史；
- `label_width=0` 时标签贴左；传了值则固定宽度、右对齐（多字段对齐时用）。

### ToolTip / QuestionMark / LabelWithTip

**职责**：悬停提示。

**用法**：

```python
from av_scraper.widgets import ToolTip, QuestionMark, LabelWithTip

ToolTip(某个控件, "悬停时显示的说明")
QuestionMark(parent, "这是一段说明")   # 一个蓝色 ?
LabelWithTip(parent, "输出目录:", tip="...")  # 标签 + ?
```

- `ToolTip` 挂在任意 widget 上，鼠标停留 400ms 后弹出浅黄色提示框；
- `QuestionMark` 是一个蓝色 `?` 标签，用于"字段旁放个提示入口"的常见场景；
- `LabelWithTip` 把标签和可选的 `?` 组合成一行，`ConfigForm` 内部用它渲染字段标签。

样式统一：改 `QuestionMark` 的字体、颜色、图标会全局生效（因为所有 `?` 都由它生成）。

---

## GUI 层

### 主窗口

`gui/app.py` 的 `App`：

- 两个核心 Tab：`ScanTab` / `MoveTab`；
- 状态栏：左侧显示状态文本，右侧有「配置」「关于」按钮；
- 加载插件：`_load_plugins(notebook)` 遍历 `discover_plugins()` 返回的 `PluginSpec` 列表；
- 每个插件失败都会显示一个**错误提示 Tab**，不阻塞核心；
- 顶部状态栏的 Tab 编号从核心 Tab 之后接续（插件从 3 开始编号）。

**打开配置窗口**：

```python
def _open_config_window(self) -> None:
    pages = self._core_config_pages()          # 至少一个核心配置页
    for plugin in self._loaded_plugins:
        getter = getattr(plugin, "config_pages", None)
        if callable(getter):
            pages.extend(getter())              # 有插件的加上
    ConfigWindow(self, pages)
```

**无插件**时 `pages` 只有一项，配置窗口仍能正常打开。

**关闭时确认**：

`App._on_close` 检查 `app_config.confirm_on_close`，弹窗询问后再 `destroy`。

### 配置窗口

`gui/config_window.py`：

**`ConfigPage` 数据类**（每个标签页一个）：

```python
@dataclass
class ConfigPage:
    title: str
    build: Callable[[ttk.Frame], ttk.Frame]
    load: Callable[[Any], None]
    collect: Callable[[], Any]
    save: Callable[[Any], None]
    current: Callable[[], Any]
    default: Callable[[], Any]
```

**`ConfigWindow`**：

- 顶部 `ttk.Notebook` 容纳多个标签页；
- 底部固定按钮栏：`[重置] [重新加载]` …… `[确定] [取消] [应用]`；
- 每个 Tab 内含滚动区（Canvas + Scrollbar），内容超出窗口高度时可滚轮滚动；
- **先 `withdraw` 再 `deiconify`**，避免打开时在左上角闪现；
- **不调 `transient`，不调 `grab_set`**，允许用户同时操作主窗口和配置窗口。

**按钮语义**：

| 按钮 | 行为 |
|---|---|
| 重置 | 对每个页调 `default()` 拿到默认对象，`load()` 到 UI，`save()` 落盘（弹二次确认） |
| 重新加载 | 对每个页调 `load(current())`，放弃未保存的改动 |
| 应用 | 对每个页调 `collect()` → `save()`；出错汇总报告 |
| 确定 | 同"应用"，但成功后关闭窗口；有失败时询问用户是否强制关闭 |
| 取消 | 直接关闭，不保存 |

**保存失败的处理**：每个页独立 try/except，最后把失败的页名和错误信息汇总到一个 messagebox 里。不做事务性回滚——对个人工具没必要。

### Tab 通用模式

所有 Tab（核心的和插件的）都遵循同一套并发模型：

```
用户点击 → 主线程禁用按钮 → 启动后台线程 →
  线程把消息 put 到 queue →
  主线程 after(80, poll) 消费 queue → 更新 UI
```

**铁律**：只有主线程能碰 tkinter 控件。后台线程不得直接调用 `.insert()` / `.configure()` 等。跨线程通信一律走 `queue.Queue`。

**日志输出**：后台线程用 `QueueLogHandler` 把 `logging` 记录推入 queue，主线程消费后写入 Text 控件。

**可选的 `apply_layout` 方法**：Tab 如果实现了这个方法，App 在配置保存后会调用一次，用来刷新"日志区高度""表格区高度"等运行时布局偏好。

---

## 插件系统

### 接口约定

定义在 `av_scraper/plugin_api.py`：

```python
@dataclass
class PluginContext:
    app: Any                          # 主窗口
    app_config: Any                   # AppConfig 实例
    config_path: Path                 # config.json
    log_dir: Path                     # log/
    plugin_dir: Path                  # 插件目录
    save_config: Callable[[], None]   # 触发核心保存配置


class Plugin(Protocol):
    name: str
    version: str

    def __init__(self, ctx: PluginContext) -> None: ...

    def create_tab(self, notebook) -> Any:
        """返回一个 tkinter Frame；返回 None 则不添加标签页。"""
        ...

    def config_pages(self) -> list[ConfigPage]:
        """返回本插件提供的配置页。可选实现。"""
        ...
```

### 加载流程

`plugin_loader.py` 里的逻辑：

1. **扫描候选目录**（按优先级）：
   - `user_plugins_root()`：exe 同级 / 项目根的 `plugins/`
   - `bundled_plugins_root()`：`_MEIPASS/plugins/`（完整版打包时用 `--add-data` 塞入）
   - 同名插件以用户目录为准。

2. **读 `plugin.json`**，拿到 `name` / `version` / `entry` / `dependencies`；

3. **依赖检查**（`_missing_dependencies`）：
   - 用 `importlib.import_module` 逐条尝试 import，不用 `importlib.metadata`；
   - **这是为了兼容 PyInstaller**：打包后 `pkg_resources` / `importlib.metadata` 可能失效，但 `import` 一定有效；
   - 包名到 import 名的映射表：`pillow → PIL`、`beautifulsoup4 → bs4` 等。

4. **动态加载**：
   - 把插件目录的父目录加入 `sys.path`；
   - 按 `<plugin_dir.name>.<entry_module>` 导入，取到类。

5. **实例化**：
   - 用 `PluginContext` 构造实例；
   - 调用 `plugin.create_tab(notebook)`，把返回的 Frame 加到 Notebook。

6. **配置页收集**：App 打开配置窗口时，遍历所有已加载插件，调用 `config_pages()` 收集它们的配置页。

### `plugin.json` 格式

```json
{
  "name": "插件显示名",
  "version": "1.0.0",
  "entry": "tab:PluginClass",
  "description": "简介",
  "dependencies": ["playwright", "openpyxl"]
}
```

### 源（Source）子结构

`web_scraper` 这类"包含多个功能单元"的插件，内部用"源"来组织。每个源实现一个 `Source` 协议：

```python
class Source(Protocol):
    name: str

    def __init__(self, plugin: Any, ctx: Any) -> None: ...

    def build_tab(self, parent) -> ttk.Frame: ...

    def config_page(self) -> ConfigPage | None: ...

    def is_busy(self) -> bool: ...

    def sync_from_config(self) -> None: ...  # 可选

    def apply_layout(self) -> None: ...      # 可选
```

**顶层插件 tab.py** 的职责：

```python
class WebScraperPlugin:
    def create_tab(self, notebook):
        root = ttk.Frame(notebook)
        inner = ttk.Notebook(root)
        inner.pack(fill="both", expand=True)
        self._sources = []
        for SourceCls in SOURCES:
            source = SourceCls(self, self.ctx)
            frame = source.build_tab(inner)
            inner.add(frame, text=source.name)
            self._sources.append(source)
        return root

    def config_pages(self):
        return [p for s in self._sources
                if (p := s.config_page()) is not None]

    def sync_from_config(self):
        for s in self._sources:
            s.sync_from_config()

    def apply_layout(self):
        for s in self._sources:
            s.apply_layout()
```

**好处**：加一个新源 = 加一个目录 + 在 `sources/__init__.py` 的 `SOURCES` 列表里加一行。主界面、配置窗口、`app.py`、`plugin_loader.py` 全都不用动。

**配置组织**：

- 顶层 `ScraperPluginConfig` 里聚合各源的子配置：
  ```python
  @dataclass
  class ScraperPluginConfig:
      javdb: JavdbSourceConfig = field(default_factory=JavdbSourceConfig)
      subtitlecat: SubtitleCatConfig = field(default_factory=SubtitleCatConfig)
  ```
- 存到 `scraper_config.json`；
- 每个源通过 `self.plugin.config.javdb` / `self.plugin.config.subtitlecat` 访问自己的那一段；
- 保存时顶层插件负责一次性写盘。

### 开发一个新插件

```
plugins/
└── my_plugin/
    ├── plugin.json
    ├── README.md       # 文档（必须）
    ├── __init__.py
    ├── tab.py          # GUI 入口，实现 Plugin 协议
    └── config.py       # 可选，插件自己的配置
```

**单功能插件**（不分子源）的 `tab.py` 模板：

```python
from av_scraper.gui.config_window import ConfigPage
from av_scraper.plugin_api import PluginContext
from av_scraper.widgets import ConfigForm


class MyPlugin:
    name = "我的插件"
    version = "1.0.0"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        self.config = MyConfig.load(...)

    def create_tab(self, notebook):
        import tkinter as tk
        from tkinter import ttk

        frame = ttk.Frame(notebook, padding=8)
        ttk.Label(frame, text=f"Hello from {self.name}").pack()
        return frame

    def config_pages(self):
        forms = {}

        def build(parent):
            frame = ttk.Frame(parent)
            form = ConfigForm(frame, MyConfig)
            form.pack(fill="both", expand=True)
            forms["main"] = form
            return frame

        return [ConfigPage(
            title=self.name,
            build=build,
            load=lambda c: forms["main"].load_from(c),
            collect=lambda: forms["main"].collect(),
            save=lambda c: (c.save(self.config_path), setattr(self, "config", c)),
            current=lambda: self.config,
            default=lambda: MyConfig(),
        )]
```

**多功能插件**：照 `web_scraper` 的结构，用 `Source` 协议分子源。

### 内置插件一览

| 插件 | 功能 | 实现文档 |
|---|---|---|
| **web_scraper / JavDB 信息** | Playwright 爬取 javdb.com，生成带封面嵌入的 Excel | [plugins/web_scraper/README.md](plugins/web_scraper/README.md) |
| **web_scraper / 字幕猫** | Playwright 爬取 subtitlecat.com，下载字幕，生成结果 Excel | 同上 |
| **jellyfin_nfo** | 从刮削表格提取封面，生成 Jellyfin 兼容的 `.nfo` | [plugins/jellyfin_nfo/README.md](plugins/jellyfin_nfo/README.md) |

---

## 构建与分发

### 前置：确保依赖装齐

打包前先在环境里装好：

```bash
conda activate <env>
pip install playwright xlsxwriter httpx Pillow openpyxl
playwright install msedge
python -c "import playwright, xlsxwriter, httpx, PIL, openpyxl; print('OK')"
```

`python -c` 那一步必须输出 `OK` 才能继续。**不要用 pandas/numpy**——它们会引入 MKL，让打包体积暴涨 400 MB。

### 打包前清理

```cmd
for /d /r plugins %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
```

或者直接双击根目录的 `清除缓存.bat`。

### 核心版

```cmd
pyinstaller -F -w -n JAVscraper_core_win64 ^
    --paths . ^
    --clean ^
    run.py
```

产物：`dist/JAVscraper_core_win64.exe`（约 15 MB）。

### 完整版

```cmd
pyinstaller -D -w -n JAVscraper_full_win64 ^
    --paths . ^
    --paths plugins ^
    --add-data "plugins;plugins" ^
    --collect-all playwright ^
    --collect-submodules web_scraper ^
    --collect-submodules jellyfin_nfo ^
    --clean ^
    run_full.py
```

产物：`dist/JAVscraper_full_win64/`（约 150 MB）。

| 参数 | 作用 |
|---|---|
| `-D` | 目录模式。单文件模式每次启动要解压 150 MB，很慢；完整版用 `-D` |
| `--paths plugins` | 让 PyInstaller 静态分析到插件包 |
| `--add-data "plugins;plugins"` | 把 `plugins/` 目录打进 exe 的 `_MEIPASS`（Windows 用 `;`，Linux/macOS 用 `:`） |
| `--collect-all playwright` | 收集 Playwright 的 Node 驱动和所有子模块 |
| `--collect-submodules web_scraper` | 递归收集 `web_scraper` 包下所有子模块，**加新源时不用改打包命令** |

### 分发

- 核心版：直接发 `JAVscraper_core_win64.exe`；
- 完整版：把 `dist/JAVscraper_full_win64/` 打包成 zip/7z，用户解压后运行里面的 exe。

```cmd
cd dist
tar -a -c -f JAVscraper_full_win64.zip JAVscraper_full_win64
```

### 打包前删除私人配置

完整版打包时，插件目录下的 `config.json` 会被一起复制到 exe 内部。虽然运行期插件读的是 exe 同级的配置文件，这些内部副本不会生效，但仍会**泄漏你本机的路径**。

在打包后手动删除：

```cmd
del /q dist\JAVscraper_full_win64\_internal\plugins\web_scraper\config.json
del /q dist\JAVscraper_full_win64\_internal\plugins\jellyfin_nfo\config.json
```

或者干脆在打包前把源码里的插件 `config.json` 临时移走。

### 调试打包问题

```cmd
:: 带控制台的调试版，能看到完整 traceback
pyinstaller -F -c --debug=imports -n JAVscraper_full_debug ^
    --paths . --paths plugins ^
    --add-data "plugins;plugins" ^
    --collect-all playwright ^
    --collect-submodules web_scraper ^
    --collect-submodules jellyfin_nfo ^
    run_full.py
```

常见问题：

| 现象 | 原因 |
|---|---|
| 完整版大小和核心版一样 | 打包环境没装插件依赖，`--collect-all playwright` 静默失败 |
| 有标签页但没有某个插件 | spec 里的 `datas` 丢了 `('plugins', 'plugins')`，或漏了 `--collect-submodules` |
| 启动慢 | 用了 `-F`，改成 `-D` |
| 杀软误报 | 常见，`-D` 模式误报率明显低于 `-F` |
| 插件报 `No module named 'xxx'` | 该依赖不在 `run_full.py` 的 import 列表里，也不在 `--collect-submodules` 覆盖范围内 |

---

## 测试

### 生成测试文件

`test/gen_test_files.py`：

```bash
# 10000 个空文件，默认输出到 test/files/
python test/gen_test_files.py

# 10 万个
python test/gen_test_files.py --count 100000

# 带 1KB 内容
python test/gen_test_files.py --count 10000 --size 1024

# 清理
python test/gen_test_files.py --clean
```

生成的文件名混合了字母前缀、FC2、数字前缀和噪声，用于验证扫描的识别率、进度显示和排序。

### 手工测试清单

改动核心逻辑后，建议验证：

- [ ] 扫描 1 万文件，进度条正常递增，成功率约 90%
- [ ] 排序：点表头，成功/失败分组正确
- [ ] 移动预览：绿色 / 灰色 / 红色分类正确
- [ ] 移动执行：冲突策略 `skip` / `overwrite` / `rename` 行为符合预期
- [ ] 撤回：移动后能完整还原
- [ ] 配置保存：改前缀 → 保存 → 重开 → 生效
- [ ] 配置窗口：「应用」立即生效，不需要重启
- [ ] 表格/日志高度：改完立即生效于所有 Tab
- [ ] 插件加载：完整版能看到插件标签页，核心版看不到
- [ ] 插件错误处理：手动删掉某个插件的依赖，应显示错误标签页而非崩溃

### 单元测试（待补充）

`scraper.py` 和 `processor.py` 适合写 pytest：

```python
import pytest
from av_scraper.config import ScraperConfig
from av_scraper.scraper import CodeExtractor

@pytest.mark.parametrize("filename,expected", [
    ("ABC-123.mp4", "ABC-123"),
    ("ABC_123.mp4", "ABC-123"),
    ("FC2PPV1234567.mp4", "FC2-1234567"),
    ("ABCD-123.mp4", "ABCD-123"),   # 最长优先
    ("random.mp4", None),
])
def test_extract(filename, expected):
    ext = CodeExtractor(ScraperConfig())
    assert ext.extract(filename) == expected
```

---

## 扩展点

### 加一个核心配置项

在 `av_scraper/config.py` 对应 dataclass 加一行 field，带 `metadata`。配置窗口自动渲染，无需改 `config_window.py`。

如果新字段是**全局行为**（如日志高度），加在 `AppConfig` 顶层；如果是**模块专属**（如扫描参数），加到对应的子 dataclass。

### 加一种识别模式

在 `scraper.CodeExtractor._compile_patterns` 里加预编译正则集合，在 `extract()` 里按优先级调用。

### 加一种导出格式

在 `reports.py` 加一个函数，然后 `scan_tab._worker` 里调用。

### 加一个爬取源

1. 在 `plugins/web_scraper/sources/` 下新建目录 `my_source/`；
2. 写 `config.py`（dataclass）和 `source.py`（实现 `Source` 协议）；
3. 在 `sources/__init__.py` 的 `SOURCES` 列表里加一行 `MySource`；
4. 在 `web_scraper/config.py` 的 `ScraperPluginConfig` 里加对应子配置字段；
5. 在 `_sub_config_class` 里加一行分支；
6. 在 `run_full.py` 里加对应的 `import`（保证打包时不漏）。

**主界面、配置窗口、`app.py` 都不用改**。

### 加一个独立插件

见 [开发一个新插件](#开发一个新插件)。

### 换 GUI

`scraper.py` / `processor.py` / `reports.py` 都不依赖 tkinter。可以：

```python
from av_scraper.config import AppConfig
from av_scraper.paths import config_path
from av_scraper.scraper import CodeExtractor

cfg = AppConfig.load(config_path())
ext = CodeExtractor(cfg.scraper)
results = ext.scan_directory("D:/videos")
```

接入 FastAPI、PySide6、Rich CLI 都可以。

---

## 已知限制

- **插件不能热重载**。改插件代码需要重启程序；如果行为不符预期，先清 `__pycache__`。
- **撤回仅保留最近一次**。连续执行多次移动只撤回最后一次。
- **扫描的目录遍历为单线程**。1 万个文件通常在 1 秒内完成；网络盘可能需要数秒。已考虑过 `os.scandir` 优化，但收益不明显，暂未采用。
- **完整版体积大**。约 150 MB，绝大部分是 Playwright 的 Node 驱动（93 MB `node.exe`）。
- **Playwright 打包兼容性**。PyInstaller 打 Playwright 需要 `--collect-all`，不同版本之间略有差异，升级前先在测试目录试打。
- **`plugin_loader` 依赖 `importlib.import_module` 检查依赖**，不读 `importlib.metadata`。这意味着无法做版本约束（如 `openpyxl>=3.1`），只能判断"有没有"。
- **`web_scraper` 的启动慢是正常的**。它加载了 Playwright，首屏会慢 1~2 秒。这是完整版体积和功能换来的。

---

## 版本与兼容

- **核心 API（`plugin_api.py`）以 `PluginContext` 字段为准**。如果插件要读 `app_config` 的字段，需要注意核心升级可能带来的字段变动；
- **`plugin.json` 的 `min_core_version`** 字段已预留但未启用，将来若做兼容性检查会在此实现；
- **插件配置文件分散**：每个插件一个 `.json`，独立读写。用户升级程序版本时，旧配置文件不会自动删除；
- **主版本号变更**时（如 `0.x → 1.x`），核心 API 可能不兼容，届时插件需要同步更新；
- **`config.json` 结构向后兼容**：新增字段用 dataclass 默认值填充；删除字段被 `dataclasses.fields()` 过滤。