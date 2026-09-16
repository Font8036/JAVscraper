# AV 文件名刮削与整理工具

一个基于 tkinter 的本地桌面小工具：**从文件名中提取标准化代码，并按代码批量移动 / 重命名文件**。整个工作流——扫描、预览、执行、撤回、改配置——都在图形界面完成，不需要碰命令行，也不需要手动编辑配置文件。

- 代码开发遵循宁可遗漏，不可误报的原则
- 支持从视频 / ISO / 字幕文件名中识别 `ABC-123`、`FC2-1234567` 等标准代码
- 支持递归扫描、结果可视化、一键导出 JSON / TXT / CSV 报告
- 执行移动前可先预览，执行后仍可一键撤回
- 配置以 JSON 保存，界面里可视化编辑，保存即生效
- 全程纯标准库，无第三方运行时依赖

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
- [配置文件详解](#配置文件详解)
- [历史目录机制](#历史目录机制)
- [输出文件说明](#输出文件说明)
- [工作流程](#工作流程)
- [打包为 exe](#打包为-exe)
- [二次开发](#二次开发)
- [常见问题](#常见问题)
- [注意事项](#注意事项)
- [许可](#许可)

---

## 功能特性

| 分类 | 能力 |
|---|---|
| 扫描 | 从文件名提取产品代码，支持 `FC2` / 字母前缀 / 6 位数字前缀三种模式；前缀可按最长优先匹配；递归扫描子目录；自动跳过无关文件 |
| 可视化 | 扫描结果实时显示在表格中，成功提取标绿色 `✓`，保持原样标灰色 `✗`；底部显示总数、成功数、提取率 |
| 导出 | 每次扫描自动在 `log/<时间戳>/` 下生成 `scraper_results.json`、`extraction_report.txt`、`extraction_table.csv` |
| 移动 | 支持按代码分组移动到子文件夹、按代码重命名、冲突跳过 / 覆盖 / 自动追加序号 |
| 预览 | 真正动文件前先列出每个源文件的目标路径与冲突处理结果，绿色 / 灰色 / 红色区分 |
| 撤回 | 每次移动自动记录到 `log/move_operations.json`，可一键还原 |
| 配置 | 所有配置项集中在一个 `config.json`，界面里可视化编辑，加字段只需改一处代码 |
| 历史 | 扫描目录和目标目录各保留最近若干条，输入框点击即展开下拉选择，默认保留 5 条，上限可调 |

---

## 目录结构

```
av_scraper/
├── pyproject.toml               # 项目元信息与依赖声明
├── README.md
├── .gitignore
├── run.py                       # ★ 顶层入口，源码运行和打包共用
├── config.json                  # 首次运行自动生成
├── log/                         # 日志 / 结果输出，首次运行自动生成
│   ├── 20260914_153000/         # 每次扫描一个时间戳目录
│   │   ├── scraper_results.json
│   │   ├── extraction_report.txt
│   │   └── extraction_table.csv
│   └── move_operations.json     # 最近一次移动操作的记录（供撤回）
└── av_scraper/
    ├── __init__.py              # 版本号
    ├── __main__.py              # python -m av_scraper 入口
    ├── paths.py                 # 定位项目根 / 配置 / 日志目录
    ├── defaults.py              # 默认前缀与扩展名
    ├── config.py                # 配置数据类 + JSON 读写
    ├── scraper.py               # 核心：文件名解析
    ├── processor.py             # 核心：文件移动 / 撤回
    ├── reports.py               # 核心：JSON / TXT / CSV 导出
    └── gui/
        ├── __init__.py
        ├── common.py            # 日志队列 / 尺寸格式化 / 历史目录工具
        ├── app.py               # 主窗口
        ├── scan_tab.py          # ① 扫描页
        ├── move_tab.py          # ② 移动页
        └── config_tab.py        # ③ 配置页
```

设计原则：

- **核心与界面分离**。`scraper.py` / `processor.py` / `reports.py` 是纯 Python，不 import 任何 GUI 模块，可以单测、写 CLI、换 GUI。
- **配置是数据不是代码**。所有可调项都是 `config.json` 里的字段，界面用 dataclass 元数据自动生成控件，加字段只改一处。
- **运行期文件只落 `log/` 和 exe 同级**。源码运行时以项目根为基准，打包后以 exe 所在目录为基准。

---

## 环境要求

- **Python 3.9+**（使用了 `dataclass`、`Path`、`dict` 保序等特性）
- **tkinter**：官方 Python 安装包默认自带；Linux 用户若缺失可 `sudo apt install python3-tk`
- **运行时无第三方依赖**，全部使用标准库
- 打包需要 `pyinstaller`（可选）

---

## 安装与运行

### 方式一：从项目根目录运行（推荐）

```bash
cd av_scraper          # 进入包含 run.py 和 av_scraper/ 的目录
python run.py
```

`run.py` 会把当前目录加入 `sys.path` 并调用 `av_scraper.__main__.main`，无论从哪里双击运行都能找到包。

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

---

## 界面说明

主窗口由三个标签页组成，底部是状态栏。

### ① 扫描

| 控件 | 说明 |
|---|---|
| 扫描目录 | 待扫描目录。首次为空，之后默认填入**上一次使用过的目录**；点击输入框任意位置可展开历史下拉 |
| 开始扫描 | 在后台线程中执行，界面保持响应；扫描期间按钮自动禁用 |
| 结果表格 | 每行含 `✓/✗`、文件名、提取码、状态、文件大小 |
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
```

---

## 打包为 exe

### 前置：确认有 `run.py`

`run.py` 放在项目根目录、和 `pyproject.toml` 同级：

```python
"""顶层入口：源码运行和 PyInstaller 打包共用。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from av_scraper.__main__ import main

if __name__ == "__main__":
    main()
```

### 安装 PyInstaller

```bash
pip install pyinstaller
# 或使用项目声明的 dev 依赖
pip install -e ".[dev]"
```

### 打包

在项目根目录执行：

```bash
pyinstaller -F -w -n av-scraper --paths . run.py
```

| 参数 | 含义 |
|---|---|
| `-F` | 单文件模式 |
| `-w` | 不显示控制台窗口（GUI 程序必需） |
| `-n av-scraper` | 生成的 exe 名字 |
| `--paths .` | 显式把项目根加入搜索路径，避免 `av_scraper` 包找不到 |

产物：

```
dist/
└── av-scraper.exe
```

### 部署

把 `dist/av-scraper.exe` 拷到任意目录，例如 `D:\Tools\AVScraper\`：

```
D:\Tools\AVScraper\
├── av-scraper.exe      ← 双击运行
├── config.json         ← 首次运行自动生成
└── log/                ← 首次扫描/移动后自动生成
```

**注意**：`config.json` 和 `log/` 都落在 exe 同级目录。不要把它们打进 exe 内部——那样 UI 里改配置就保存不了。

### 调试打包问题

单文件 exe 没控制台，报错可能直接闪退。排查时改用带控制台的模式：

```bash
pyinstaller -F -c -n av-scraper-debug --paths . run.py
```

双击后在黑窗里能看到完整 traceback。

### 目录模式（可选）

如果嫌单文件 exe 每次启动都要解压到临时目录（首次启动 1~3 秒），或遇到杀软误报，改用目录模式：

```bash
pyinstaller -D -w -n av-scraper --paths . run.py
```

产出 `dist/av-scraper/` 文件夹，exe 启动几乎瞬间完成。分发时把它打成 zip 即可。

### 附带图标（可选）

准备一个 `.ico` 文件，打包时加上：

```bash
pyinstaller -F -w -n av-scraper --paths . --icon icon.ico run.py
```

或者编辑自动生成的 `av-scraper.spec`，在 `EXE(...)` 里加上 `icon='icon.ico'`，之后直接：

```bash
pyinstaller av-scraper.spec
```

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

---

## 常见问题

**Q：运行时提示 `attempted relative import with no known parent package`**

A：别直接运行 `av_scraper/__main__.py`。用 `python run.py`（推荐），或 `cd` 到包上一层执行 `python -m av_scraper`。

**Q：`config.json` 和 `log/` 生成在了奇怪的地方**

A：源码运行时它们以项目根为基准（`paths.app_dir()` 用 `__file__` 定位），跟当前 shell 的工作目录无关。打包后以 exe 所在目录为基准。检查 `av_scraper/paths.py` 的 `app_dir()` 是否符合预期。

**Q：扫描结果里 `✗` 很多，识别不到**

A：看 `✗` 行的文件名，确认前缀是否在「已知字母前缀」列表里。不在就加进去，保存后重新扫描。或者，检查文件名是否有连接符，如果没有合法的连接符，识别自然会失败。

**Q：扫描结果里误识别很多**

A：看误报的 `✓` 行的文件名，确认匹配过程是否正确。如果匹配过程正确，那么请参照注意事项第5，6条检查，也可能是原始文件名过于奇怪，这种情况难以避免。

**Q：配置页保存前缀后报 `'scraper', 'recent_scan_dirs'`**

A：这是老版本的 bug。当前版本 `_collect` 会跳过 `hidden=True` 的字段并从现有配置里保留原值，升级到最新代码即可。

**Q：移动后想反悔**

A：点「撤回移动」。前提是 `log/move_operations.json` 还在、且没有被后续移动覆盖。

**Q：扫描时界面卡住**

A：不应发生。扫描跑在后台线程，只有主线程碰 tkinter。如果卡住多半是磁盘 IO，等日志滚完即可。

**Q：支持 macOS / Linux 吗？**

A：支持。`ttk.Style().theme_use("vista")` 在非 Windows 上会抛异常，但代码里已用 `try/except` 兜住，会自动回退到默认主题。

**Q：单文件 exe 被杀毒软件误报**

A：PyInstaller 单文件模式的常见问题。改用 `-D` 目录模式打包通常能显著降低误报率。

---

## 注意事项

- **执行移动前务必先「预览更改」**，尤其注意红色的「覆盖」行。
- 移动是**物理操作**，撤回依赖记录文件；记录文件被删或被下一次移动覆盖后，无法自动恢复。
- 扫描不修改任何源文件，可以放心重复运行。
- 输出文件（scraper_results.json，extraction_report.txt，extraction_table.csv）默认会永久保留，如需删除，请手动删除。
- 首次使用建议先在一个测试目录里跑一遍流程，确认目录、命名规则、冲突策略都符合预期。
- 不建议在已知字母前缀列表中增加两个字母的前缀，可能会导致误识别增加
- 不建议修改配置中的连接符列表。特别的， **不要修改代码以增加连接符为空的情况！** 会导致大量误识别！如有需要，对于少量文件建议使用everything之类的搜索软件，使用文件名或正则表达式匹配来搜索文件，按照文件大小排序后手动修改文件名以增加连接符，建议用-。常用正则表达式有 **[A-Za-z]{3,5}[0-9]{3,4}** ， **^(?!hhd800$)[A-Za-z]{3,5}[0-9]{3,4}$** 等，别的需求可以直接告诉AI，让AI帮忙写正则表达式。
- 本项目仅用于本地文件管理，请确保所处理的文件来源合法，并遵守当地法律法规。

---

## 许可

MIT
