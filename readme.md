
---

# AV 文件名刮削与整理工具

一个基于 tkinter 的本地桌面小工具：**从文件名中提取标准化代码，并按代码批量移动 / 重命名文件**。整个工作流——扫描、预览、执行、撤回、改配置——都在图形界面完成，不需要碰命令行，也不需要手动编辑配置文件。

- 代码开发遵循**宁可遗漏，不可误报**的原则
- 支持从视频 / ISO / 字幕文件名中识别 `ABC-123`、`FC2-1234567` 等标准代码
- 支持递归扫描、结果可视化、一键导出 JSON / TXT / CSV 报告
- 执行移动前可先预览，执行后仍可一键撤回
- 配置以 JSON 保存，界面里可视化编辑，保存即生效
- **核心全程纯标准库，无第三方运行时依赖**
- **支持插件系统**，可按需扩展功能（如 JavDB 元数据刮削）

---

## 目录

- [功能特性](#功能特性)
- [目录结构](#目录结构)
- [环境要求](#环境要求)
- [安装与运行](#安装与运行)
- [快速开始](#快速开始)
- [界面说明](#界面说明)
  - [① 扫描](#-扫描)
  - [② 移动](#-移动)
  - [③ 配置](#-配置)
  - [④ 插件标签页](#-插件标签页)
- [配置文件详解](#配置文件详解)
- [历史目录机制](#历史目录机制)
- [输出文件说明](#输出文件说明)
- [插件系统](#插件系统)
  - [JavDB 刮削插件](#javdb-刮削插件)
- [工作流程](#工作流程)
- [打包为 exe](#打包为-exe)
  - [核心版](#核心版)
  - [完整版](#完整版)
- [二次开发](#二次开发)
- [常见问题](#常见问题)
- [注意事项](#注意事项)
- [许可](#许可)

---

## 功能特性

| 分类 | 能力 |
|---|---|
| 扫描 | 从文件名提取产品代码，支持 `FC2` / 字母前缀 / 6 位数字前缀三种模式；前缀按最长优先匹配；递归扫描子目录；自动跳过无关文件 |
| 可视化 | 扫描结果实时显示在表格中，成功提取标绿色 `✓`，保持原样标灰色 `✗`；支持按任意列排序 |
| 导出 | 每次扫描自动在 `log/<时间戳>/` 下生成 `scraper_results.json`、`extraction_report.txt`、`extraction_table.csv` |
| 移动 | 支持按代码分组移动到子文件夹、按代码重命名、冲突跳过 / 覆盖 / 自动追加序号 |
| 预览 | 真正动文件前先列出每个源文件的目标路径与冲突处理结果，绿色 / 灰色 / 红色区分 |
| 撤回 | 每次移动自动记录到 `log/move_operations.json`，可一键还原 |
| 配置 | 所有配置项集中在一个 `config.json`，界面里可视化编辑，加字段只需改一处代码 |
| 历史 | 扫描目录和目标目录各保留最近若干条，输入框点击即展开下拉选择，默认保留 5 条，上限可调 |
| 插件 | 支持动态加载插件，插件可独立分发、独立配置，加载失败不影响核心运行 |

---

## 目录结构

```
av_scraper/
├── pyproject.toml               # 项目元信息与依赖声明
├── readme.md
├── .gitignore
├── run.py                       # ★ 核心版入口，源码运行和打包共用
├── run_full.py                  # ★ 完整版入口，内置 JavDB 插件一起打包
├── config.json                  # 首次运行自动生成
├── log/                         # 日志 / 结果输出，首次运行自动生成
│   ├── 20260914_153000/         # 每次扫描一个时间戳目录
│   │   ├── scraper_results.json
│   │   ├── extraction_report.txt
│   │   └── extraction_table.csv
│   └── move_operations.json     # 最近一次移动操作的记录（供撤回）
├── plugins/                     # ★ 插件目录
│   └── javdb/                   # JavDB 刮削插件
│       ├── plugin.json          # 插件清单
│       ├── __init__.py
│       ├── config.py            # 插件自己的配置
│       ├── fetch.py             # Playwright 爬取
│       ├── parse.py             # 字段解析
│       ├── report.py            # 生成 Excel 报告
│       ├── tab.py               # GUI 入口，实现 Plugin 协议
│       └── config.json          # 插件配置，首次运行自动生成
└── av_scraper/
    ├── __init__.py              # 版本号
    ├── __main__.py              # python -m av_scraper 入口
    ├── paths.py                 # 定位项目根 / 配置 / 日志目录
    ├── defaults.py              # 默认前缀与扩展名
    ├── config.py                # 配置数据类 + JSON 读写
    ├── plugin_api.py            # 插件接口定义（核心的一部分）
    ├── plugin_loader.py         # 插件发现与加载
    ├── scraper.py               # 核心：文件名解析
    ├── processor.py             # 核心：文件移动 / 撤回
    ├── reports.py               # 核心：JSON / TXT / CSV 导出
    └── gui/
        ├── __init__.py
        ├── common.py            # 日志队列 / 尺寸格式化 / 历史目录工具
        ├── app.py               # 主窗口（含插件加载逻辑）
        ├── scan_tab.py          # ① 扫描页
        ├── move_tab.py          # ② 移动页
        └── config_tab.py        # ③ 配置页
```

设计原则：

- **核心与界面分离**。`scraper.py` / `processor.py` / `reports.py` 是纯 Python，不 import 任何 GUI 模块，可以单测、写 CLI、换 GUI。
- **配置是数据不是代码**。所有可调项都是 `config.json` 里的字段，界面用 dataclass 元数据自动生成控件，加字段只改一处。
- **运行期文件只落 `log/` 和 exe 同级**。源码运行时以项目根为基准，打包后以 exe 所在目录为基准。
- **插件与核心解耦**。核心不 import 插件的任何业务模块，只通过 `plugin_api.py` 约定接口。

---

## 环境要求

- **Python 3.9+**（使用了 `dataclass`、`Path`、`dict` 保序等特性）
- **tkinter**：官方 Python 安装包默认自带；Linux 用户若缺失可 `sudo apt install python3-tk`
- **核心运行时无第三方依赖**，全部使用标准库
- **JavDB 插件额外需要**：`playwright`、`pandas`、`xlsxwriter`、`httpx`、`Pillow`

---

## 安装与运行

### 方式一：源码运行（推荐）

```bash
# 核心版（不含 JavDB 插件）
cd av_scraper
python run.py

# 完整版（含 JavDB 插件，需先装插件依赖）
python run_full.py
```

### 方式二：作为模块运行

```bash
cd av_scraper
python -m av_scraper
```

**注意**：必须在 `av_scraper` 包的**上一层**运行，且只能用 `python -m av_scraper`。直接 `python av_scraper/__main__.py` 会因为相对导入找不到父包而报错。

### 方式三：安装后运行

```bash
pip install -e .
av-scraper             # 或 python -m av_scraper
```

### 方式四：双击运行（Windows）

在项目根目录新建 `启动.bat`：

```bat
@echo off
cd /d "%~dp0"
python run.py
```

以后双击这个 `.bat` 即可启动。

---

## 快速开始

首次打开后按顺序做三件事：

1. **切到「③ 配置」**，把「默认扫描目录」、「输出目录」（可选，留空即用 `log/`）、「目标目录」改成你机器上的实际路径，点「保存配置」。
2. **切到「① 扫描」**，确认扫描目录，点「开始扫描」。结果会以 `✓/✗` 实时显示，扫描结束后本次的 JSON 路径自动同步到「② 移动」页。
3. **切到「② 移动」**，先点「预览更改」核对每一项的去向与冲突处理结果，确认无误后「执行移动」。想反悔就点「撤回移动」。

如果使用完整版（含 JavDB 插件），还会看到第 ④ 个标签页。详见 [插件系统](#插件系统)。

---

## 界面说明

主窗口由多个标签页组成，底部是状态栏。核心版有三个标签页，完整版额外包含插件标签页。

### ① 扫描

| 控件 | 说明 |
|---|---|
| 扫描目录 | 待扫描目录。首次为空，之后默认填入**上一次使用过的目录**；点击输入框任意位置可展开历史下拉 |
| 开始扫描 | 在后台线程中执行，界面保持响应；扫描期间按钮自动禁用 |
| 结果表格 | 每行含 `✓/✗`、文件名、提取码、状态、文件大小；**点击表头可排序**（再点切换升/降序） |
| 统计 | 显示总数、成功提取数、提取率 |
| 日志 | 实时滚动，与核心逻辑的 logging 输出一致 |

**颜色约定：**

| 样式 | 含义 |
|---|---|
| `✓` 绿色 | 成功提取到代码 |
| `✗` 灰色 | 未能识别，保持原文件名 |

扫描成功后会自动记录本次使用的目录到「历史目录」，并把本次结果的 JSON 路径填入「② 移动」页。

### ② 移动

| 控件 | 说明 |
|---|---|
| 输入 JSON | 刮削结果 JSON，通常由扫描页自动填入 |
| 目标目录 | 文件最终移动到的目录；同样支持历史下拉 |
| 预览更改 | **不实际动文件**，只列出每个操作的去向和冲突处理结果 |
| 执行移动 | 真正执行，会弹出确认框；完成后统计"成功 / 跳过 / 失败" |
| 撤回移动 | 按 `log/move_operations.json` 把文件搬回原位，完成后删除记录文件 |
| 日志 | 实时输出每个文件的处理结果 |

**预览表格颜色：**

| 颜色 | 含义 |
|---|---|
| 绿色 | 正常移动 / 重命名后移动 |
| 灰色 | 跳过（目标已存在且策略为 `skip`） |
| 红色 | 覆盖（目标已存在且策略为 `overwrite`） |

### ③ 配置

按模块分组展示全部可配置项，控件类型由 dataclass 元数据自动匹配：

| 类型 | 控件 |
|---|---|
| `str` | 单行输入框 |
| `dir` | 单行输入框 + 「浏览…」按钮（选目录） |
| `file` | 单行输入框 + 「浏览…」按钮（选文件） |
| `bool` | 勾选框 |
| `choice` | 下拉框 |
| `int` | 数字微调框 |
| `list` | 多行文本框（每行一项，或同一行用逗号分隔） |

底部按钮：

- **重新加载**：放弃未保存的改动，从磁盘重新读取 `config.json`；
- **保存配置**：校验控件内容 → 写回 `config.json` → 同步到其他标签页，立即生效，无需重启。

「最近扫描目录」「最近目标目录」等历史字段标记为 `hidden`，不会出现在配置页，但保存在 `config.json` 里，可手动清空或编辑。

### ④ 插件标签页

完整版启动时会自动扫描 `plugins/` 目录，加载其中的插件。每个插件在 Notebook 中占据一个标签页，标签页名称由插件自己声明。

插件加载失败时（依赖缺失、代码异常等），会显示一个**错误提示标签页**，说明原因并给出安装依赖的命令，不会影响核心功能的使用。

详见 [插件系统](#插件系统)。

---

## 配置文件详解

`config.json` 首次运行时自动生成，位于项目根目录（源码运行）或 exe 同级（打包后）。

```json
{
  "scraper": {
    "default_directory": "",
    "output_directory": "",
    "output_filename": "scraper_results.json",
    "recursive_processing": true,
    "separators": ["-", "_"],
    "supported_extensions": [".mp4", ".mkv", "..."],
    "known_alpha_prefixes": ["ABP", "ADN", "..."],
    "max_recent_dirs": 5,
    "recent_scan_dirs": []
  },
  "processor": {
    "input_json": "",
    "target_directory": "",
    "move_to_extracted_folder": true,
    "enable_rename": false,
    "existing_file_handling": "rename",
    "recent_target_dirs": []
  }
}
```

### `scraper` 段

| 字段 | 类型 | 说明 |
|---|---|---|
| `default_directory` | str | 默认扫描目录；如果历史目录里已有记录，会优先用最近一次使用的目录 |
| `output_directory` | str | 结果输出根目录；**留空则输出到 `log/`** |
| `output_filename` | str | 结果 JSON 的文件名，默认 `scraper_results.json` |
| `recursive_processing` | bool | 是否递归处理子文件夹 |
| `separators` | list | 前缀与数字之间的连接符，默认 `["-", "_"]` |
| `supported_extensions` | list | 参与扫描的扩展名（含视频 / ISO / 字幕） |
| `known_alpha_prefixes` | list | 已知字母前缀白名单。可以包含重复项，构造提取器时会自动去重 |
| `max_recent_dirs` | int | 历史目录最多保留条数，默认 5，界面可改 |
| `recent_scan_dirs` | list | 最近使用的扫描目录，程序自动维护，**不建议手动改** |

### `processor` 段

| 字段 | 类型 | 说明 |
|---|---|---|
| `input_json` | str | 刮削结果 JSON 路径（移动页输入框可临时覆盖，不写回配置） |
| `target_directory` | str | 文件移动目标目录 |
| `move_to_extracted_folder` | bool | 是否移动到以提取码命名的子文件夹 |
| `enable_rename` | bool | 是否用提取码重命名文件（保留原扩展名） |
| `existing_file_handling` | str | 冲突策略：`skip` / `overwrite` / `rename` |
| `recent_target_dirs` | list | 最近使用的目标目录，程序自动维护 |

**冲突策略：**

| 值 | 行为 |
|---|---|
| `skip` | 目标已存在则跳过该文件 |
| `overwrite` | 直接覆盖目标文件 |
| `rename` | 自动追加 `_01`、`_02` … 直到不冲突 |

---

## 历史目录机制

- 「扫描目录」和「目标目录」两处输入框都会自动记录最近使用过的目录；
- 打开程序时，输入框默认填入**上一次使用过的目录**；
- 点击输入框任意位置可展开下拉，列出所有历史目录；
- 上限由 `scraper.max_recent_dirs` 控制，默认 5，可在「③ 配置」页里修改；
- 每次扫描成功或移动成功后自动保存，**写入 `config.json`，不需要手动点保存**。

清空历史：关闭程序后直接编辑 `config.json`，把 `recent_scan_dirs` / `recent_target_dirs` 置为 `[]` 即可。

---

## 输出文件说明

每次扫描在 `log/<时间戳>/` 下生成三个文件：

| 文件 | 内容 |
|---|---|
| `scraper_results.json` | 机器可读的完整结果，供「② 移动」页读取 |
| `extraction_report.txt` | 人类可读的文本报告，含统计与逐条明细 |
| `extraction_table.csv` | 表格，含"原始文件名 / 提取出的信息"两列 + 统计信息 |

移动操作完成后，额外生成 `log/move_operations.json`，记录最近一次移动的全部源 / 目标路径，供「撤回移动」使用。**同一时间只保留最近一次**，执行新的移动会覆盖上一条记录。

所有输出文件默认**永久保留**，如需清理请手动删除。

---

## 插件系统

### 设计原则

- **核心不 import 插件**。核心只通过 `plugin_api.py` 约定接口，插件对核心是黑盒。
- **插件可独立分发**。每个插件是一个目录，放在 `plugins/` 下即可被自动发现。
- **加载失败不影响核心**。依赖缺失、代码异常时显示错误标签页，核心功能不受影响。
- **插件配置独立**。插件有自己的配置文件，放在插件目录内，与核心 `config.json` 隔离。

### 插件目录结构

```
plugins/
└── <插件名>/
    ├── plugin.json          # 插件清单（必需）
    ├── __init__.py
    ├── tab.py               # GUI 入口，实现 Plugin 协议
    ├── ...                  # 插件自己的模块
    └── config.json          # 插件配置（可选，首次运行自动生成）
```

### `plugin.json` 格式

```json
{
  "name": "插件显示名",
  "version": "1.0.0",
  "entry": "tab:PluginClass",
  "description": "插件简介",
  "dependencies": ["playwright", "pandas"]
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 显示在标签页上的名字 |
| `version` | 是 | 插件版本 |
| `entry` | 是 | `模块名:类名`，模块名相对于插件目录 |
| `description` | 否 | 简介 |
| `dependencies` | 否 | pip 包列表，核心会检查是否已安装 |

### 插件接口

插件类需实现 `Plugin` 协议（定义在 `av_scraper/plugin_api.py`）：

```python
from av_scraper.plugin_api import PluginContext

class MyPlugin:
    name = "我的插件"
    version = "1.0.0"

    def __init__(self, ctx: PluginContext) -> None:
        self.ctx = ctx

    def create_tab(self, notebook) -> ttk.Frame:
        frame = ttk.Frame(notebook, padding=8)
        # ... 构建 UI ...
        return frame
```

`PluginContext` 提供以下运行时信息：

| 字段 | 说明 |
|---|---|
| `app` | 主窗口实例 |
| `app_config` | 核心配置对象 |
| `config_path` | 核心配置文件路径 |
| `log_dir` | 日志目录路径 |
| `plugin_dir` | 插件所在目录 |
| `save_config` | 触发核心保存配置的回调 |

---

### JavDB 刮削插件

完整版内置的 JavDB 刮削插件，可从 javdb.com 抓取影片元数据并生成带封面的 Excel 报告。

#### 功能

- 从 Excel 读取番号清单
- 用 Playwright 打开浏览器，自动搜索并抓取影片信息
- 下载封面图片到本地
- 导出爬取结果 CSV
- 解析演员、评分、类别、评论，生成嵌入封面的 Excel 表格

#### 依赖

```bash
pip install playwright pandas xlsxwriter httpx Pillow
playwright install msedge
```

第二条命令会下载浏览器驱动，只需执行一次。

#### 插件配置

插件有自己的配置文件 `plugins/javdb/config.json`，首次运行时自动生成。字段如下：

| 字段 | 类型 | 说明 |
|---|---|---|
| `input_excel` | str | 番号清单 Excel 路径（单列，无表头） |
| `cover_dir` | str | 封面图片保存目录 |
| `output_csv` | str | 爬取结果 CSV 输出路径 |
| `output_excel` | str | 最终 Excel 报告输出路径 |
| `state_file` | str | 登录状态文件路径（可选） |
| `show_browser` | bool | 是否显示浏览器窗口 |
| `browser_channel` | str | 浏览器通道：`msedge` / `chrome` / `chromium` |
| `request_delay` | float | 请求间隔（秒），默认 1.0 |
| `page_timeout` | int | 页面超时（秒），默认 60 |
| `max_retries` | int | 失败重试次数，默认 2 |
| `skip_existing_covers` | bool | 跳过已存在的封面下载 |
| `save_state_on_exit` | bool | 退出时保存登录状态 |

#### 使用方式

1. 切到「④ JavDB 刮削」标签页
2. 填入输入 Excel、封面目录、输出 CSV、输出 Excel 路径
3. 按需调整参数（是否显示浏览器、请求间隔等）
4. 点「保存配置」
5. 点「开始爬取」，进度表格会逐行显示状态
6. 爬取完成后点「生成 Excel」，几秒后弹出完成提示

#### 注意事项

- javdb 有 Cloudflare 反爬机制，建议**开启浏览器窗口**手动完成验证
- 登录状态可保存复用，减少重复验证
- 爬取速度受网络和网站限流影响，建议保持 `request_delay ≥ 1.0`
- Playwright 打包后体积较大，完整版 exe 约 200~250 MB

---

## 工作流程

```
┌──────────────┐  选择目录   ┌──────────────────┐
│  ① 扫描页    │ ─────────► │  CodeExtractor   │
│              │             │  （后台线程）      │
└──────────────┘             └────────┬─────────┘
                                      │
            ┌───────────────┬─────────┴──────────┐
            ▼               ▼                    ▼
      UI 表格（✓/✗）  scraper_results.json   TXT / CSV 报告
                                      │
                                      │ 路径自动填入
                                      ▼
┌──────────────┐  预览 / 执行  ┌──────────────────┐
│  ② 移动页    │ ───────────► │  FileProcessor   │──► 移动文件
│              │ ◄─────────── │                  │──► move_operations.json
└──────────────┘     撤回      └──────────────────┘
                                      ▲
                                      │ 读写
                                      ▼
                              config.json（+ 历史目录）
                                      ▲
                                      │
                               ┌──────────────┐
                               │  ③ 配置页    │
                               └──────────────┘

┌──────────────┐  自动加载   ┌──────────────────┐
│  ④ 插件页    │ ◄────────── │  plugins/ 目录   │
│              │             │  （动态发现）      │
└──────────────┘             └──────────────────┘
```

---

## 打包为 exe

项目支持打包为两种形态：**核心版**（零依赖，体积小）和**完整版**（内置 JavDB 插件，体积大但开箱即用）。

### 前置：确认入口文件

- `run.py` — 核心版入口
- `run_full.py` — 完整版入口

两个文件都在项目根目录，和 `pyproject.toml` 同级。

### 核心版

```bash
pyinstaller -F -w -n av-scraper-core --paths . run.py
```

| 参数 | 含义 |
|---|---|
| `-F` | 单文件模式 |
| `-w` | 不显示控制台窗口 |
| `-n av-scraper-core` | 生成的 exe 名字 |
| `--paths .` | 显式把项目根加入搜索路径 |

产物：`dist/av-scraper-core.exe`，约 15 MB。

部署结构：

```
任意目录/
├── av-scraper-core.exe
├── config.json            ← 首次运行自动生成
├── log/                   ← 首次运行自动生成
└── plugins/               ← 可选，用户自行建
```

### 完整版

```bash
pyinstaller -F -w -n av-scraper-full ^
    --paths . ^
    --paths plugins ^
    --add-data "plugins;plugins" ^
    --collect-all playwright ^
    run_full.py
```

| 参数 | 含义 |
|---|---|
| `--paths plugins` | 让 PyInstaller 能静态分析到 `javdb` 包 |
| `--add-data "plugins;plugins"` | 把整个 `plugins/` 目录复制进 exe |
| `--collect-all playwright` | 收集 Playwright 的完整依赖 |

产物：`dist/av-scraper-full.exe`，约 200~250 MB。

部署结构：

```
任意目录/
├── av-scraper-full.exe
├── config.json            ← 首次运行自动生成
├── log/                   ← 首次运行自动生成
├── plugins/               ← 可选，覆盖内置插件用
└── javdb_config.json      ← 插件配置，首次运行自动生成
```

> **注意**：Windows 上 `--add-data` 用 `;` 分隔源和目标，Linux/macOS 用 `:`。

### 调试打包问题

单文件 exe 没控制台，报错可能直接闪退。排查时改用带控制台的模式：

```bash
pyinstaller -F -c -n av-scraper-debug --paths . run.py
```

双击后在黑窗里能看到完整 traceback。

### 目录模式（可选）

如果嫌单文件 exe 启动慢或被杀软误报，改用目录模式：

```bash
pyinstaller -D -w -n av-scraper-core --paths . run.py
```

产出 `dist/av-scraper-core/` 文件夹，exe 启动几乎瞬间完成。分发时打成 zip 即可。

---

## 二次开发

### 增加一个配置项

在 `av_scraper/config.py` 对应 dataclass 里加一行：

```python
my_option: str = field(
    default="",
    metadata={"label": "我的新选项", "kind": "str"},
)
```

`kind` 取值：`str` / `dir` / `file` / `bool` / `choice` / `int` / `list`。

- `choice` 需额外提供 `"choices": [...]`；
- `list` 可选 `"big": True` 让文本框更高；
- 加 `"hidden": True` 则不出现在配置页（适合程序自动维护的字段）。

界面会自动出现对应控件，无需改 GUI 代码。

### 增加已知前缀

在 `av_scraper/defaults.py` 的 `DEFAULT_PREFIXES` 里加词（用空格分隔即可）；或者直接在「③ 配置」页的「已知字母前缀」文本框里加一行保存。列表允许重复，提取器会自动去重。

### 扩展识别规则

`av_scraper/scraper.py` 的 `CodeExtractor._compile_patterns` 里按现有三种模式（FC2 / 字母前缀 / 数字前缀）添加正则，然后在 `extract()` 里按优先级调用。

### 更换 GUI

`scraper.py`、`processor.py`、`reports.py` 不依赖 tkinter，可以：

```python
from av_scraper.config import AppConfig
from av_scraper.paths import config_path
from av_scraper.scraper import CodeExtractor

cfg = AppConfig.load(config_path())
results = CodeExtractor(cfg.scraper).scan_directory("D:/some/dir")
```

直接接入命令行、PySide6、Web 界面等。

### 开发新插件

1. 在 `plugins/` 下新建插件目录
2. 创建 `plugin.json`，声明 `name`、`version`、`entry`
3. 创建 `tab.py`，实现 `Plugin` 协议
4. 重启程序，核心会自动发现并加载

详见 [插件系统](#插件系统) 章节。

---

## 常见问题

**Q：运行时提示 `attempted relative import with no known parent package`？**

A：别直接运行 `av_scraper/__main__.py`。用 `python run.py`（推荐），或 `cd` 到包上一层执行 `python -m av_scraper`。

**Q：`config.json` 和 `log/` 生成在了奇怪的地方？**

A：源码运行时它们以项目根为基准（`paths.app_dir()` 用 `__file__` 定位），跟当前 shell 的工作目录无关。打包后以 exe 所在目录为基准。检查 `av_scraper/paths.py` 的 `app_dir()` 是否符合预期。

**Q：扫描结果里 `✗` 很多，识别不到？**

A：看 `✗` 行的文件名，确认前缀是否在「已知字母前缀」列表里。不在就加进去，保存后重新扫描。或者检查文件名是否有合法的连接符（默认 `-` 和 `_`）。

**Q：扫描结果里误识别很多？**

A：看误报的 `✓` 行的文件名，确认匹配过程是否正确。如果匹配过程正确但结果不合预期，可能是原始文件名过于奇怪，这种情况难以完全避免。

**Q：配置页保存前缀后报 `'scraper', 'recent_scan_dirs'`？**

A：这是老版本的 bug。当前版本 `_collect` 会跳过 `hidden=True` 的字段并从现有配置里保留原值，升级到最新代码即可。

**Q：移动后想反悔？**

A：点「撤回移动」。前提是 `log/move_operations.json` 还在、且没有被后续移动覆盖。

**Q：扫描时界面卡住？**

A：不应发生。扫描跑在后台线程，只有主线程碰 tkinter。如果卡住多半是磁盘 IO，等日志滚完即可。

**Q：支持 macOS / Linux 吗？**

A：支持。`ttk.Style().theme_use("vista")` 在非 Windows 上会抛异常，但代码里已用 `try/except` 兜住，会自动回退到默认主题。

**Q：单文件 exe 被杀毒软件误报？**

A：PyInstaller 单文件模式的常见问题。改用 `-D` 目录模式打包通常能显著降低误报率。

**Q：JavDB 插件提示缺少依赖？**

A：按错误标签页里的提示执行：

```bash
pip install playwright pandas xlsxwriter httpx Pillow
playwright install msedge
```

**Q：JavDB 插件爬取时被 Cloudflare 拦截？**

A：在插件配置里开启「显示浏览器窗口」，手动完成验证。也可以保存登录状态（设置 `state_file` 并勾选「退出时保存登录状态」），下次复用。

**Q：插件加载失败会影响核心功能吗？**

A：不会。核心会显示一个错误提示标签页，说明失败原因，其余功能完全正常。

---

## 注意事项

- **执行移动前务必先「预览更改」**，尤其注意红色的「覆盖」行。
- 移动是**物理操作**，撤回依赖记录文件；记录文件被删或被下一次移动覆盖后，无法自动恢复。
- 扫描不修改任何源文件，可以放心重复运行。
- 输出文件（`scraper_results.json`、`extraction_report.txt`、`extraction_table.csv`）默认永久保留，如需删除请手动操作。
- 如果扫描结果里 `✗` 很多，建议先用文件名搜索工具（如 Everything）按文件大小排序，手动给文件名加上连接符（推荐用 `-`），再重新扫描。常用正则：`[A-Za-z]{3,5}[0-9]{3,4}`。
- JavDB 插件依赖 Playwright，完整版 exe 体积较大（200~250 MB），这是正常的。
- 本项目仅用于本地文件管理，请确保所处理的文件来源合法，并遵守当地法律法规。

---

## 许可

MIT

---
