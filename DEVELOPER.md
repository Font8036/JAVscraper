# AV 文件名刮削与整理工具 — 开发者文档

面向想阅读源码、扩展功能或自行构建的开发者。普通使用请看 **[README.md](README.md)**。

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
- [GUI 层](#gui-层)
- [插件系统](#插件系统)
  - [接口约定](#接口约定)
  - [加载流程](#加载流程)
  - [开发一个新插件](#开发一个新插件)
- [内置插件：JavDB 刮削](#内置插件javdb-刮削)
- [构建与分发](#构建与分发)
  - [核心版](#核心版)
  - [完整版](#完整版)
- [测试](#测试)
- [扩展点](#扩展点)
- [已知限制](#已知限制)

---

## 项目定位与设计原则

- **核心零依赖**。除标准库外不引入任何第三方包，让核心版保持在 15 MB 量级、能被单文件 exe 打包。
- **核心与 GUI 解耦**。`scraper` / `processor` / `reports` 是纯逻辑，可单测、可 CLI、可接任意 GUI。
- **配置即数据**。所有可调项都是 dataclass 字段，GUI 依据 metadata 自动生成控件，加字段不改 UI 代码。
- **插件与核心彻底分离**。核心不 import 插件的任何业务模块，只通过 `plugin_api.py` 约定接口。
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
│   └── settings.json               # 建议将 "plugins" 加入 analysis.extraPaths
│
├── run.py                          # 核心版入口
├── run_full.py                     # 完整版入口（内置 javdb 插件）
│
├── config.json                     # 首次运行自动生成
├── log/                            # 输出与日志，自动生成
│
├── plugins/                        # 插件目录（用户可写）
│   └── javdb/
│       ├── plugin.json
│       ├── javdb_config.json       # 完整版插件的配置，自动生成
│       ├── __init__.py
│       ├── config.py
│       ├── fetch.py
│       ├── parse.py
│       ├── report.py
│       └── tab.py
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
    ├── plugin_api.py               # 插件接口定义
    ├── plugin_loader.py            # 插件发现与加载
    └── gui/
        ├── common.py               # QueueLogHandler / human_size / remember_dir
        ├── app.py                  # 主窗口，含插件加载
        ├── scan_tab.py
        ├── move_tab.py
        └── config_tab.py
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

### 完整版额外依赖

```bash
pip install playwright pandas xlsxwriter httpx Pillow
playwright install msedge
```

### VSCode 建议配置

`.vscode/settings.json`：

```json
{
  "python.analysis.extraPaths": [".", "plugins"],
  "python.defaultInterpreterPath": "<你的解释器路径>"
}
```

---

## 核心模块

### scraper.py

**职责**：从文件名提取标准代码。

**关键类**：`CodeExtractor(config: ScraperConfig)`

- 构造时预编译所有正则，`scan_directory` 里不再做编译；
- 三种识别策略，按优先级：FC2 → 字母前缀 → 6 位数字前缀；
- 前缀按**长度降序**匹配，保证最长优先；
- 前缀列表在构造时**去重**（`sorted(set(...))`），配置里可以有重复项。

**数据类**：`ScrapeResult`

```python
@dataclass
class ScrapeResult:
    file_path: str
    filename: str
    extracted_code: str
    status: str         # "extracted" | "original"
    file_size: int
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

- `rglob` / `glob` 返回的迭代器**只能消费一次**。当前实现先收集候选列表再逐个处理，两轮循环分别用 `iterator` 和 `candidates`；
- 总数回调 `on_total` 在收集完成后触发一次；单文件回调 `progress` 在每个文件处理完后触发。

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
    status: str    # "move" | "rename" | "skip" | "overwrite"

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
- 撤回记录只保留最近一次，`undo()` 成功后会删除记录文件；
- 移动操作前会 `mkdir -p` 目标目录；`overwrite` 会先 `unlink` 再 `move`。

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
| `extraction_table.csv` | 两列 + 统计信息 |

---

### config.py

**职责**：配置的数据模型与 JSON 读写。

**数据类结构**：

```
AppConfig
├── scraper: ScraperConfig
├── processor: ProcessorConfig
└── (插件配置由各插件自行管理)
```

**字段声明方式**：

```python
@dataclass
class ScraperConfig:
    default_directory: str = field(
        default="",
        metadata={"label": "默认扫描目录", "kind": "dir"},
    )
    ...
```

`metadata` 字段约定：

| key | 取值 | 说明 |
|---|---|---|
| `label` | str | 界面显示的标签 |
| `kind` | `str` / `dir` / `file` / `bool` / `choice` / `int` / `list` | 控件类型 |
| `choices` | list | `kind="choice"` 时的选项 |
| `big` | bool | `kind="list"` 时是否用高文本框 |
| `hidden` | bool | 是否在配置页隐藏（如历史记录） |

**加字段后的自动行为**：

- GUI 自动出现对应控件（`config_tab._render_section` 遍历 dataclass fields）；
- 保存时 `_collect` 用当前实例值填充 `hidden` 字段，避免被默认值覆盖；
- `AppConfig.load` 用 `{k: v for k, v in data.items() if k in known}` 过滤旧字段，向后兼容。

**列表解析规则**（`_parse_list`）：每行按换行拆，再按逗号拆，去空白去引号。

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

## GUI 层

### 主窗口：`gui/app.py`

- 三个核心 Tab：`ScanTab` / `MoveTab` / `ConfigTab`；
- 加载插件：`_load_plugins(notebook)` 遍历 `discover_plugins()` 返回的 `PluginSpec` 列表；
- 每个插件失败都会显示一个**错误提示 Tab**，不阻塞核心。

### 通用工具：`gui/common.py`

- `QueueLogHandler`：把 `logging` 记录推入 `queue.Queue`，由主线程消费；
- `human_size(n)`：格式化文件大小；
- `remember_dir(history, new_dir, limit)`：维护历史目录列表；
- `make_dir_combobox(parent, textvariable)`：点击整行可展开的 Combobox。

### Tab 通用模式

所有 Tab 都遵循同一套并发模型：

```
用户点击 → 主线程禁用按钮 → 启动后台线程 →
  线程把消息 put 到 queue →
  主线程 after(80, poll) 消费 queue → 更新 UI
```

**铁律**：只有主线程能碰 tkinter 控件。后台线程不得直接调用 `.insert()` / `.configure()` 等。

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
   - **这是为了兼容 PyInstaller**：打包后 `pkg_resources` / `importlib.metadata` 可能失效，但 `import` 一定有效。
   - 包名到 import 名的映射表：`pillow → PIL`、`beautifulsoup4 → bs4` 等。

4. **动态加载**：
   - 把插件目录的父目录加入 `sys.path`；
   - 按 `<plugin_dir.name>.<entry_module>` 导入，取到类。

5. **实例化**：
   - 用 `PluginContext` 构造实例；
   - 调用 `plugin.create_tab(notebook)`，把返回的 Frame 加到 Notebook。

### `plugin.json` 格式

```json
{
  "name": "插件显示名",
  "version": "1.0.0",
  "entry": "tab:PluginClass",
  "description": "简介",
  "dependencies": ["playwright", "pandas"]
}
```

### 开发一个新插件

```
plugins/
└── my_plugin/
    ├── plugin.json
    ├── __init__.py
    ├── tab.py
    └── config.py       # 可选，插件自己的配置
```

`tab.py` 模板：

```python
from av_scraper.plugin_api import PluginContext


class MyPlugin:
    name = "我的插件"
    version = "1.0.0"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    def create_tab(self, notebook):
        import tkinter as tk
        from tkinter import ttk

        frame = ttk.Frame(notebook, padding=8)
        ttk.Label(frame, text=f"Hello from {self.name}").pack()
        return frame
```

**插件可以**：

- `import av_scraper.*`（核心 API 是稳定的）
- 通过 `ctx.log_dir` 写日志
- 通过 `ctx.app_config` 读写核心配置
- 通过 `ctx.save_config()` 触发核心落盘
- 在插件目录里保存自己的配置/资源

**插件不能**：

- 热重载（改代码要重启程序）
- 修改核心界面（只能新增标签页）
- 与其它插件通信（无约定机制）

---

## 内置插件：JavDB 刮削

### 文件职责

| 文件 | 职责 |
|---|---|
| `config.py` | `JavdbConfig` 数据类，读写 `config.json` |
| `fetch.py` | Playwright 爬取 + CSV 导出 |
| `parse.py` | 演员 / 评分 / 类别 / 评论的字段提取（纯函数） |
| `report.py` | 生成带封面嵌入的 Excel |
| `tab.py` | GUI，实现 Plugin 协议 |

### 数据流

```
input_excel(单列番号)
    │ load_targets()
    ▼
targets: list[str]
    │ scrape_javdb()  ──► csv ──► parse_row()  ──► report.build_excel()
    ▼
records: list[dict]
    │ save_csv()
    ▼
output_csv
    │ build_excel()
    ▼
output_excel
```

### 字段约定

爬取阶段的 `record` 字段（**供 report 使用**）：

| 字段 | 说明 |
|---|---|
| `目标番号` | 用户输入的 |
| `链接` | 详情页 URL |
| `番号` | 页面抓到的 |
| `名称` | 标题 |
| `信息` | 详情页文本（供 parse 二次提取） |
| `评论` | 评论文本 |

### 关键实现细节

1. **进度回调** 传的是 `{"fanhao": ..., "name": ...}` 的字典，不是裸字符串——这样 GUI 能拿到实际番号做比对；
2. **`is_matched(target, scraped)`** 做归一化比较：去掉 `-` `_`、大写、`FC2PPV` → `FC2`；
3. **配置路径解析** `_resolve_config_path`：
   - 源码运行：`plugins/javdb/config.json`
   - 完整版 exe：`_MEIPASS` 只读，改写到 exe 同级 `javdb_config.json`；
4. **Playwright headless 由 `show_browser` 控制**，默认 `True`，因为 javdb 有 Cloudflare，无头模式容易卡验证。

---

## 构建与分发

### 前置：确保依赖装齐

```bash
conda activate <env>
pip install playwright pandas xlsxwriter httpx Pillow
playwright install msedge
python -c "import playwright, pandas, xlsxwriter, httpx, PIL; print('OK')"
```

### 核心版

```bash
pyinstaller -F -w -n av-scraper-core --paths . --clean run.py
```

产物：`dist/av-scraper-core.exe`（约 15 MB）。

### 完整版

```bash
pyinstaller -D -w -n av-scraper-full ^
    --paths . ^
    --paths plugins ^
    --add-data "plugins;plugins" ^
    --collect-all playwright ^
    --clean run_full.py
```

产物：`dist/av-scraper-full/`（约 200 MB）。

| 参数 | 作用 |
|---|---|
| `-D` | 目录模式。单文件模式每次启动要解压 200 MB，很慢；完整版用 `-D` |
| `--paths plugins` | 让 PyInstaller 静态分析到 `javdb` 包 |
| `--add-data "plugins;plugins"` | 把 `plugins/` 目录打进 exe 的 `_MEIPASS`（Windows 用 `;`，Linux/macOS 用 `:`） |
| `--collect-all playwright` | 收集 Playwright 的 Node 驱动和所有子模块 |

### 分发

- 核心版：直接发 `av-scraper-core.exe`；
- 完整版：把 `dist/av-scraper-full/` 打包成 zip，用户解压后运行里面的 exe。

```bash
cd dist
tar -a -c -f av-scraper-full.zip av-scraper-full
```

### 调试打包问题

```bash
# 带控制台的调试版，能看到完整 traceback
pyinstaller -F -c --debug=imports -n av-scraper-full-debug ^
    --paths . --paths plugins ^
    --add-data "plugins;plugins" ^
    --collect-all playwright ^
    run_full.py
```

常见问题：

| 现象 | 原因 |
|---|---|
| 完整版大小和核心版一样 | 打包环境没装插件依赖，`--collect-all playwright` 静默失败 |
| 有标签页但没有 javdb | spec 里的 `datas` 丢了 `('plugins', 'plugins')` |
| 启动慢 | 用了 `-F`，改成 `-D` |
| 杀软误报 | 常见，`-D` 模式误报率明显低于 `-F` |

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
- [ ] 插件加载：完整版能看到 ④ 标签页，核心版看不到

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

在 `av_scraper/config.py` 对应 dataclass 加一行 field，带 `metadata`。GUI 自动渲染，无需改 `config_tab.py`。

### 加一种识别模式

在 `scraper.CodeExtractor._compile_patterns` 里加预编译正则集合，在 `extract()` 里按优先级调用。

### 加一种导出格式

在 `reports.py` 加一个函数，然后 `scan_tab._worker` 里调用。

### 加一个插件

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

- **插件不能热重载**。改插件代码需要重启程序。
- **撤回仅保留最近一次**。连续执行多次移动只撤回最后一次。
- **扫描的目录遍历为单线程**。1 万个文件通常在 1 秒内完成；网络盘可能需要数秒。已考虑过 `os.scandir` 优化，但收益不明显，暂未采用。
- **完整版体积大**。主要来自 Playwright 的 Node 驱动。可以用 `--exclude-module` 裁剪未用到的子模块，但风险大于收益。
- **Playwright 打包兼容性**。PyInstaller 打 Playwright 需要 `--collect-all`，不同版本之间略有差异，升级前先在测试目录试打。
- **`plugin_loader` 依赖 `importlib.import_module` 检查依赖**，不读 `importlib.metadata`。这意味着无法做版本约束（如 `pandas>=2.0`），只能判断"有没有"。

---

## 版本与兼容

- **核心 API（`plugin_api.py`）以 `PluginContext` 字段为准**。如果插件要读 `app_config` 的字段，需要注意核心升级可能带来的字段变动；
- **`plugin.json` 的 `min_core_version`** 字段已预留但未启用，将来若做兼容性检查会在此实现；
- 主版本号变更时（如 `2.x → 3.x`），核心 API 可能不兼容，届时插件需要同步更新。