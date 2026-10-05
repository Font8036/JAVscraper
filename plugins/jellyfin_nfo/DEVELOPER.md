# Jellyfin NFO 插件 — 开发者文档

面向想阅读源码、修改逻辑或扩展功能的开发者。用户使用请看 **[README.md](README.md)**。

---

## 目录

- [文件职责](#文件职责)
- [数据流](#数据流)
- [关键实现细节](#关键实现细节)
  - [从 Excel 提取图片](#从-excel-提取图片)
  - [按列名读 Excel](#按列名读-excel)
  - [多列合并规则](#多列合并规则)
  - [NFO 生成](#nfo-生成)
  - [封面复制](#封面复制)
  - [插件结构](#插件结构)
  - [配置路径解析](#配置路径解析)
  - [UI 构建](#ui-构建)
- [扩展点](#扩展点)
- [已知限制](#已知限制)

---

## 文件职责

| 文件 | 职责 |
|---|---|
| `plugin.json` | 插件清单：name、version、entry、dependencies |
| `config.py` | `JellyfinNfoConfig` 数据类 + JSON 读写 |
| `parser.py` | 演员 / 评分 / 类别解析（纯函数） |
| `excel_reader.py` | 按列名读取 Excel，返回结构化电影数据 |
| `image_extractor.py` | 从 xlsx 内部提取封面，输出到本地目录 |
| `nfo_builder.py` | 生成 NFO XML |
| `organizer.py` | 遍历电影文件夹，复制封面，写 NFO |
| `tab.py` | GUI，实现 `Plugin` 协议 + `config_page()` |

---

## 数据流

### 提取图片流程

```
input_excel
    │ openpyxl 打开，找 DISPIMG 公式 → {番号: DISPIMG_ID}
    ▼
code_to_dispimg
    │ zipfile 读 cellimages.xml → {DISPIMG_ID: rId}
    │ zipfile 读 cellimages.xml.rels → {rId: 图片文件名}
    ▼
dispimg_to_file
    │ zipfile 读 xl/media/<图片文件名>
    ▼
cover_source_dir/<番号>.<ext>
```

### 生成 NFO 流程

```
input_excel
    │ excel_reader.read_excel_data()  按列名读
    ▼
{番号: movie_data}
    │
    │ 遍历 movie_root/<番号>/ 每个子文件夹
    ▼
organizer.organize_movie_folder()
    │
    ├── _find_videos()  找视频文件
    ├── _find_cover()   从 cover_source_dir 找封面
    ├── _copy_covers()  按命名模式复制到电影文件夹
    └── _write_nfo_files()  按 NFO 模式写 .nfo
```

### movie_data 结构

```python
{
    "code": "ABC-123",
    "title": "某某标题",
    "actors": [
        {"name": "张三", "aka": "张三丰", "gender": "Male"},
        ...
    ],
    "ratings": [(4.0, 18), (8.5, 320)],      # 每个 rating 列一个元组
    "genres": ["类别1", "类别2"],             # 去重后的列表
    "personal_comment": "个人评价文本",
    "user_comment": "网友评论文本",
}
```

---

## 关键实现细节

### 从 Excel 提取图片

xlsx 本质是 ZIP 包，封面图片以二进制形式保存在 `xl/media/` 下。WPS 的「嵌入单元格图片」使用 `DISPIMG` 公式引用，映射关系保存在：

- `xl/cellimages.xml`：`<xdr:pic>` 里 `cNvPr name="ID_xxx"` 对应 `blip` 上的 rId；
- `xl/_rels/cellimages.xml.rels`：rId 对应 `xl/media/` 里的实际文件名。

提取流程：

```python
# 1. 从单元格读 DISPIMG 公式，取 ID
cell.data_type == "f" and "DISPIMG" in cell.value
m = re.search(r'DISPIMG\("([^"]+)"', cell.value)

# 2. 从 cellimages.xml 建立 ID → rId
# 3. 从 cellimages.xml.rels 建立 rId → 文件名
# 4. 从 zip 里读 xl/media/<文件名> 写出
```

**为什么只扫封面列**：早期实现扫整行找 DISPIMG，如果表格里除了封面还有其它嵌入图片会误命中。现在按列名精确定位封面列，找不到会明确报错。

**不开临时文件**：`zipfile.read(name)` 返回该文件的 `bytes`，直接 `write_bytes` 到目标，全程在内存里处理单张图片。峰值内存等于最大的那张图片。

### 按列名读 Excel

`excel_reader._build_header_map`：

```python
headers = {}
for col in range(1, ws.max_column + 1):
    v = ws.cell(row=header_row, column=col).value
    if v is not None:
        headers[str(v).strip()] = col
```

`_resolve_cols` 把配置里的多列名字（逗号分隔）解析成列号列表：

```python
def _resolve_cols(headers, name_str):
    return [
        headers[n] for n in split_columns(name_str) if n in headers
    ]
```

`split_columns` 支持 `,，;；` 四种分隔符。

### 多列合并规则

| 字段 | 合并方式 | 代码 |
|---|---|---|
| 演员 | 按 `name` 去重取并集 | `seen_names: set` |
| 类别 | `set` 去重后排序 | `genre_set.update(...)` |
| 评分 | 按列顺序保留每个评分 | `ratings.append((score, count))` |
| 个人评论 | 按列顺序拼接，`\n` 分隔 | `"\n".join(parts)` |
| 网友评论 | 同上 | 同上 |

### NFO 生成

`nfo_builder.build_nfo(movie_data, poster_filename)`：

- 用 `xml.etree.ElementTree` 构造元素树；
- 用 `minidom.parseString().toprettyxml()` 做格式化输出（缩进 2 空格）；
- `<thumb>` 只写文件名（如 `ABC-123.jpg`），Jellyfin 会在视频同级目录查找；
- 多个评分时，第一个 `<rating name="database">`，后续 `<rating name="database2">` 等。

**关于 `<role>`**：别名放在 `<role>` 而不是 `<aka>`，因为 `<aka>` 不是 Jellyfin/Kodi 的标准标签。`<role>` 语义上表示"扮演的角色名"，把别名放进去是一种折中，也避免了信息丢失。

### 封面复制

`organizer._copy_covers` 按 `cover_naming` 配置决定复制哪些文件：

```python
if cover_naming in ("与视频同名", "两种都复制"):
    for v in videos:
        dst = folder / f"{v.stem}{ext}"
        shutil.copy2(cover_src, dst)

if cover_naming in ("用番号命名", "两种都复制"):
    dst = folder / f"{code}{ext}"
    if dst.name not in copied:
        shutil.copy2(cover_src, dst)
```

用 `copied` 集合避免同一份文件被复制两次（视频名和番号相同时）。

### 插件结构

`tab.py` 里的 `JellyfinNfoPlugin` 同时承担三个角色：

**1. 实现 `Plugin` 协议**

```python
class JellyfinNfoPlugin:
    name = "Jellyfin NFO"
    version = "1.0.0"

    def __init__(self, ctx: PluginContext) -> None: ...
    def create_tab(self, notebook) -> ttk.Frame: ...
    def config_pages(self) -> list[ConfigPage]: ...
```

**2. 主界面 Tab** —— 只保留操作

```
⚙ 所有配置项已移至右下角「配置」窗口。
[提取图片] [生成 NFO]  状态: 就绪
搜索: [____] [全部列▾] ☐正则   N
┌─────────────────────────────┐
│ 番号  视频数  封面  NFO  状态 │
└─────────────────────────────┘
日志:
┌─────────────────────────────┐
└─────────────────────────────┘
```

配置项**不再出现在主界面**，全部移到配置窗口。

**3. 配置窗口页** —— `config_pages()` 返回一个 `ConfigPage`

```python
def config_pages(self):
    forms = {}

    def build(parent):
        frame = ttk.Frame(parent)
        form = ConfigForm(frame, JellyfinNfoConfig)
        form.pack(fill="both", expand=True)
        forms["main"] = form
        return frame

    def load(cfg):
        forms["main"].load_from(cfg)

    def collect():
        return forms["main"].collect()

    def save(cfg):
        cfg.save(self._config_path)
        self._config = cfg

    def current():
        return self._config

    def default():
        return JellyfinNfoConfig()

    return [ConfigPage(
        title=self.name,
        build=build, load=load, collect=collect,
        save=save, current=current, default=default,
    )]
```

配置表单由 `ConfigForm` 组件根据 `JellyfinNfoConfig` 的 dataclass metadata 自动生成。

### 配置路径解析

`JellyfinNfoPlugin._resolve_config_path`：

```python
def _resolve_config_path(self) -> Path:
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass and str(self.ctx.plugin_dir).startswith(str(meipass)):
        return Path(sys.executable).resolve().parent / "jellyfin_nfo_config.json"
    return self.ctx.plugin_dir / "config.json"
```

- **源码运行**：`plugins/jellyfin_nfo/config.json`
- **完整版 exe**：写到 exe 同级的 `jellyfin_nfo_config.json`（因为 `plugin_dir` 指向 `_MEIPASS` 中的只读副本，写不进去）

### UI 构建

`_build` 的结构：

```python
def _build(self, root: ttk.Frame) -> None:
    # ---- 提示行 ----
    hint = ttk.Frame(root)
    hint.pack(fill="x", pady=(0, 4))
    ttk.Label(
        hint,
        text="⚙ 所有配置项已移至右下角「配置」窗口。",
        foreground="#888",
    ).pack(side="left")

    # ---- 按钮区 ----
    actions = ttk.Frame(root, padding=(0, 4))
    actions.pack(fill="x")
    self._btn_extract = ttk.Button(
        actions, text="提取图片", command=self._on_extract)
    self._btn_extract.pack(side="left")
    self._btn_generate = ttk.Button(
        actions, text="生成 NFO", command=self._on_generate)
    self._btn_generate.pack(side="left", padx=4)
    # ...状态标签...

    # ---- 表格 ----
    self.result_tree = SortableTreeview(
        root,
        height=self.ctx.app_config.table_height,
        columns=[
            Column("code", "番号", ...),
            Column("videos", "视频数", ...),
            Column("cover", "封面", ...),
            Column("nfo", "NFO", ...),
            Column("status", "状态", ...),
        ],
        ...
    )
    self.result_tree.pack(fill="both", expand=True, pady=(8, 0))

    # ---- 日志 ----
    ttk.Label(root, text="日志:").pack(anchor="w", pady=(6, 0))
    self._log = tk.Text(
        root, height=self.ctx.app_config.log_height,
        wrap="none", state="disabled",
    )
    self._log.pack(fill="both", expand=True)
```

**支持的运行时布局刷新**：

```python
def apply_layout(self) -> None:
    cfg = self.ctx.app_config
    if self.result_tree is not None:
        self.result_tree.tree.configure(height=cfg.table_height)
    if self._log is not None:
        self._log.configure(height=cfg.log_height)
```

核心的 `App.refresh_from_config` 会在配置保存后调用它，实现"改完表格/日志高度立即生效"。

---

## 扩展点

### 支持更多 NFO 字段

`nfo_builder.build_nfo` 里可以加：

- `<studio>` 制作商
- `<premiered>` 发行日期
- `<runtime>` 时长
- `<director>` 导演
- `<art>` 背景图
- `<mpaa>` 分级

这些字段的数据来源可以在 `excel_reader` 里从表格列读出来，加到 `movie_data` 里。对应的配置项（列名映射）在 `config.py` 的 `JellyfinNfoConfig` 里加字段即可，GUI 会自动渲染。

### 支持其它图片格式的提取

当前只处理 DISPIMG 格式。如果表格使用 xlsxwriter 的 `embed_image()` 或 Excel 365 的「置于单元格中」格式，映射方式不同：

- **xlsxwriter 的 `embed_image`**：图片作为 `richValue` 存储，需要通过 OOXML 的 `xl/richData/` 解析；
- **Excel 365 的「置于单元格中」**：与 WPS DISPIMG 格式类似，但 `cellimages.xml` 的命名空间可能不同。

如果要做通用兼容，需要在 `image_extractor` 里增加解析分支。

### 从 CSV 读数据

除了 Excel，也可以从爬虫插件生成的 CSV 读数据。CSV 格式稳定（字段是 JSON 里明确定义的），但**没有经过解析**——演员、评分、类别还需要调 `parser.py` 里的函数再解析一次。

### 自动转换图片格式

如果 Jellyfin 对 `.webp` 支持不好，可以在 `organizer._copy_covers` 里用 Pillow 转成 `.jpg`：

```python
from PIL import Image
with Image.open(cover_src) as im:
    im.convert("RGB").save(dst, "JPEG", quality=90)
```

需要引入 `Pillow` 依赖。

### NFO 模板化

如果想让用户自定义 NFO 结构，可以把 `build_nfo` 改成读一个模板文件（如 Jinja2 模板），替换变量后输出。这会让配置多一个「NFO 模板」字段，灵活度提高但复杂度上升。

### 加配置项

在 `config.py` 的 `JellyfinNfoConfig` 里加一行 `field(metadata={"label": ..., "kind": ...})`：

- 配置窗口的 `ConfigForm` 会自动渲染新控件；
- 源码里读 `self._config.xxx` 即可；
- **不需要改 `tab.py`**（除非需要在 UI 上做特殊处理）。

---

## 已知限制

### 只处理 DISPIMG 格式

本插件目前只支持 WPS 的「嵌入单元格图片」（DISPIMG 格式）。其他嵌入方式（xlsxwriter、Excel 365 原生）需要额外解析逻辑。**目前如果表格是这些格式，提取会失败并明确报错**。

### 封面命名匹配是精确匹配

`organizer._find_cover` 用 `f"{code}{ext}"` 精确匹配。如果封面文件名带有额外后缀（如 `ABC-123_cover.jpg`），找不到。扩展方向：模糊匹配或给配置加前缀/后缀模式。

### 视频文件名不做处理

生成 NFO 时直接用视频文件的 stem 作为 NFO 名，不做任何清洗。如果视频名里含特殊字符（如 `:`、`/`），生成的 NFO 文件名在 Windows 上会失败。可以加一个 `sanitize()` 函数处理。

### 多分段视频的处理是启发式的

`per_video` 模式下，每个分段都会生成独立的同名 NFO，内容完全一样。Jellyfin 会把这个文件夹识别为一部电影。如果分段视频的命名不规范（比如 `ABC-123.part1.mp4` 和 `ABC-123.part2.mkv` 混用），可能识别不准确。

### 没有回滚机制

生成 NFO 的过程中如果失败，已经写出的文件不会回滚。设计上 NFO 是幂等的（内容相同），重跑一次即可。

### 评论字段没有长度限制

如果 `personal_comment` 或 `user_comment` 内容极长（几万字），NFO 文件会很大。Jellyfin 对 NFO 大小没有硬限制，但过大的文件加载慢。可以考虑加一个截断配置。

### 字段名与其它插件不统一

`web_scraper` 的两个源用 `self._result_tree` 命名表格字段，本插件用 `self.result_tree`。**功能上无影响**，但代码走读时容易搞混。将来可以统一。

### 表格与日志的初始高度

表格和日志的初始高度读的是 `AppConfig.table_height` / `AppConfig.log_height`。如果这两个配置项缺失（比如老版本升级上来），会走 dataclass 的默认值（6 / 8）。**首次运行时如果界面高度不对**，检查 `config.json` 里是否有这两个字段。