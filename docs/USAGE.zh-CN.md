# L4D2 模组冲突检查器 · 桌面版 / 网页版

为《求生之路2》(Left 4 Dead 2) 做的模组冲突检测与管理工具。

- **自动扫描**你订阅/安装的所有模组,找出「多个模组提供同一个内部文件」的冲突;
- 详细列出**内部路径、文件类型、模组名称、创意工坊 ID、磁盘位置**;
- 提供两种界面:**原生桌面窗口(exe,推荐)** 和 **本地网页管理器**;
- 可直接查看冲突、启用/关闭/删除模组。

> ✅ **无需安装 Python**:桌面版是自带运行环境的单文件 exe;网页版也自带便携 Python。

---

## 快速开始(双击即可)

### 方式一:桌面版 exe(推荐,原生窗口,不开网页)

双击 **`L4D2模组管理器.exe`**。

- 单文件程序,自带运行环境,**无需安装 Python**;
- 打开的是原生窗口,不依赖浏览器;
- **界面设计(Tailwind + Bento)**:本地编译的 Tailwind CSS、Bento / 仪表盘布局;
  细边框、轻阴影、backdrop-blur 玻璃质感、极淡渐变网格与颗粒噪点;
  克制的微交互、滚动渐入、错峰动画与平滑过渡;
- **氛围 3D**:Three.js 极淡粒子场 + 线框球体作为背景,只作氛围、不抢主体信息;
- **组件风格**:shadcn 风格 Button / Card / Input / Modal / Toast / Empty State / Switch,风格统一;
- **浅色 / 深色切换**:右上角太阳/月亮按钮一键切换,偏好记录在本地;
- exe 使用系统内置 **WebView2** 渲染(原生窗口,非浏览器标签);若缺少 WebView2 会自动用浏览器打开同一界面;
- 界面文件为 `page.html`(在 exe 同目录),直接编辑即可调整样式/文案,无需重新打包;
- 首次运行 Windows 可能弹出 SmartScreen 提示(因为未做代码签名),
  点「更多信息 → 仍要运行」即可。

**桌面版功能:**

- **统计**:已启用模组 / 冲突文件 / 冲突模组对 / 已隔离
- **冲突判定**:智能模式按内容(忽略脚本/完全相同);**严格模式**按文件路径,接近游戏本体。
- **冲突视图**:按**模组对聚合**,同一对模组的多处冲突合并成一张卡片(显示冲突数量,可展开查看具体文件);
  内容完全相同、或与游戏本体相同的文件不计冲突;`scripts/` 脚本类默认隐藏,可在工具栏勾选「脚本冲突」显示。
  每个模组右侧都有**开关**,拨动即可关闭;还有 **「只保留此」** 一键关掉其余(全部可逆);
  支持搜索与按类型筛选
- **模组列表**:按类型自动分组(武器 / 角色 / 声音 / 界面 / 地图 / 特效 / 模型 / 材质 / 脚本…),
  每个模组右侧都有一个**开关**,直接启用 / 关闭(关闭 = 写入游戏附加组件开关(与游戏内一致));旁边还有「工坊 / 文件夹 / 删除」;
  支持搜索、按分类筛选、只看有冲突的模组
- 顶部控制台有 **「应用并启动游戏」**:先写入目标状态,再直接拉起游戏(游戏需先退出)。
- 工具只在**游戏未运行**时写 `addonlist.txt`;游戏运行中只记录目标状态,退出后写回,避免与游戏抢写导致条目丢失。
- **持续校正**:守护进程在游戏运行中每 2 秒、退出后每 0.5 秒对比目标状态与 `addonlist.txt`,不一致就纠正;快速重启也不会漏。
- **退出游戏后自动写回**:开关后即使关闭管理器,也会有一个后台守护进程在游戏退出时把目标状态写入 `addonlist.txt`,重启游戏即生效。
- 顶部可「选择游戏目录」、打开「隔离目录」、重新扫描

- **中文标注**:自动根据模组内容推断它用于哪个**角色 / 武器 / 特感**(如「角色·教练」「武器·AK47」「特感·坦克」),直接显示在模组名称旁

> 「关闭」记录为目标状态:游戏未运行时立即写入 `left4dead2/addonlist.txt`;游戏运行中会在退出后自动写入(游戏会覆盖该文件)。
> 松散文件夹模组仍用物理隔离(`addons_disabled/`)。「删除」需输入 `DELETE` 二次确认,不可恢复。

### 方式二:双击 .bat(网页版)

| 文件 | 作用 |
|---|---|
| `open_manager.bat` | 打开图形化网页管理器(在本机浏览器里操作) |
| `check_conflicts.bat` | 检查一次,生成 `report.html` / `report.json` |
| `check_and_launch.bat` | 先检查,然后自动通过 Steam 启动游戏 |

双击 `open_manager.bat` 后:程序会在本机启动一个网页(`http://127.0.0.1:8765/`)
并自动打开浏览器。**关闭那个黑色命令行窗口即停止服务。**

> 关于「启用/禁用」:本工具把开关记录为**目标状态**,并在游戏未运行时同步到游戏自己的
> `left4dead2/addonlist.txt`(与游戏内「额外内容→附加组件」同一份状态)。不移动文件、不注入引擎。
> 游戏运行中做的改动会在退出游戏后自动写入(游戏运行时会用内存状态覆盖该文件)。
> 外部程序无法让运行中的游戏即时热重载附加组件(游戏内菜单位于进程内);
> 本工具保证「设置一次,退出后自动应用」,并提供「应用并启动游戏」一步完成。
> 创意工坊模组若彻底不用,请点「工坊」取消订阅。

---

## 命令行用法

工具自带便携 Python,命令行可这样调用(Windows):

```bat
python-embed\python.exe l4d2_conflict_checker.py --serve
```

如果你自己装了 Python 3,也可以用 `python l4d2_conflict_checker.py ...`。

常用参数:

```bash
# 启动本地网页管理器(默认端口 8765,自动打开浏览器)
python-embed\python.exe l4d2_conflict_checker.py --serve

# 换端口 / 不自动打开浏览器
python-embed\python.exe l4d2_conflict_checker.py --serve --port 9000 --no-browser

# 检查并生成报告
python-embed\python.exe l4d2_conflict_checker.py --html report.html --json report.json

# 手动指定游戏目录(任意一层都行)
python-embed\python.exe l4d2_conflict_checker.py --game-dir "E:\SteamLibrary\steamapps\common\Left 4 Dead 2"

# 检查完自动启动游戏 / 常驻监视模组变化
python-embed\python.exe l4d2_conflict_checker.py --launch
python-embed\python.exe l4d2_conflict_checker.py --watch
```

| 参数 | 说明 |
|---|---|
| `--game-dir, -g PATH` | 指定 L4D2 目录;不给则自动从 Steam 注册表/libraryfolders.vdf 查找 |
| `--html FILE` / `--json FILE` | 输出报告 |
| `--min-files N` / `--max-files N` | 报告过滤与显示条数 |
| `--include-scripts` | 把 scripts/ 类冲突也计入(默认忽略) |
| `--include-meta` | 把 addoninfo.txt 等元信息也算冲突(一般不需要) |
| `--launch` | 检查完自动启动游戏(appid 550) |
| `--watch` | 常驻监视模式 |
| `--serve` | 启动本地网页管理器 |
| `--port N` / `--no-browser` | 网页管理器端口 / 不自动开浏览器 |
| `--verbose, -v` | 显示扫描细节 |

退出码:`0` 无冲突,`1` 有冲突,`2` 出错。

---

## 原理(简述)

- L4D2 模组以 **VPK** 打包,VPK 内部有一棵「目录树」记录每个文件的虚拟路径;
- 本工具解析每个 VPK 的目录树,得到每个模组的完整文件列表;
- 以内部路径为 key,统计被 2 个及以上不同模组提供的路径 → 即冲突;
- 从 VPK 内的 `addoninfo.txt` 读取 `addontitle` 作为模组显示名。

已用真实游戏 `pak01_dir.vpk`(22,273 个条目)与 Valve 官方 `vpk` 库逐条对比验证,结果完全一致。

---

## 局限与说明

- 「谁覆盖谁」取决于模组挂载顺序;本工具只找出冲突并列出相关模组,不断言最终生效者。
- 只扫描 `left4dead2/addons`(创意工坊、手动 VPK、松散文件夹);
  游戏本体 `pak01_*.vpk` 不算冲突(模组覆盖本体是正常的)。
- 若从未安装模组,会显示「未找到模组」,属正常现象。

---

## 故障排查

- **窗口一闪而过 / 显示「服务已停止」**:
  1. 先确认是在工具文件夹里双击的 `.bat`;
  2. 若端口被占用,工具会自动换端口;也可手动 `--port 8790`;
  3. 看黑窗口上方是否有报错信息(会保留窗口等待按键)。
- 工坊目录里**未列入 addonlist 的旧 VPK 会被游戏默认加载**;可在管理器里禁用不需要的,减少加载体积。
- **游戏运行中**:开关会记录为目标状态,退出游戏后自动写入 `addonlist.txt`(游戏运行时会覆盖该文件,所以不能即时写);删除/松散文件夹被占用时加入待处理,退出后自动完成。
- **自动识别游戏**:会查 Steam 注册表、所有 Steam 库、各盘符常见目录,若游戏正在运行还会直接定位到 `left4dead2.exe`;仍找不到时会**弹窗让你手动选择游戏目录**,也可用 `--game-dir` 指定。
- **自带 Python 被删**:重新解压完整工具包,或安装 Python 3 后使用 `python` 运行。

---

## 文件清单

```
L4D2-Mod-Conflict-Checker/
├─ L4D2模组管理器.exe       # 桌面版(双击运行,无需安装任何东西)
├─ open_manager.bat           # 网页版:启动本地网页管理器
├─ check_conflicts.bat        # 网页版:检查 + 生成 HTML 报告
├─ check_and_launch.bat       # 网页版:检查 + 启动游戏
├─ 打开网页管理器.bat          # 中文名快捷方式(调用 open_manager.bat)
├─ 检查模组冲突.bat            # 中文名快捷方式
├─ 检查并启动游戏.bat          # 中文名快捷方式
├─ l4d2_webapp.py             # 桌面 WebView 版源码(exe 入口)
├─ page.html                  # 界面文件(主题/动效,可直接编辑,改完重启界面即可)
├─ l4d2_gui.py                # 旧的 Tkinter 桌面版源码(备用)
├─ l4d2_conflict_checker.py   # 主程序(扫描/报告,仅标准库)
├─ l4d2_dashboard.py          # 网页版后台 + 管理操作
├─ python-embed/              # 便携版 Python(供 .bat 网页版使用)
├─ 使用说明.md                # 本文件
└─ tests/make_test_vpks.py    # 自测用合成 VPK 生成器(可删除)
```L4D2 模组冲突检查器 · 桌面版 / 网页版

为《求生之路2》(Left 4 Dead 2) 做的模组冲突检测与管理工具。

- **自动扫描**你订阅/安装的所有模组,找出「多个模组提供同一个内部文件」的冲突;
- 详细列出**内部路径、文件类型、模组名称、创意工坊 ID、磁盘位置**;
- 提供两种界面:**原生桌面窗口(exe,推荐)** 和 **本地网页管理器**;
- 可直接查看冲突、启用/关闭/删除模组。

> ✅ **无需安装 Python**:桌面版是自带运行环境的单文件 exe;网页版也自带便携 Python。

---

## 快速开始(双击即可)

### 方式一:桌面版 exe(推荐,原生窗口,不开网页)

双击 **`L4D2模组管理器.exe`**。

- 单文件程序,自带运行环境,**无需安装 Python**;
- 打开的是原生窗口,不依赖浏览器;
- **深色现代界面**:侧边栏导航、圆角统计卡片、无边框表格、悬停高亮;
- 首次运行 Windows 可能弹出 SmartScreen 提示(因为未做代码签名),
  点「更多信息 → 仍要运行」即可。

**桌面版功能:**

- **统计**:已启用模组 / 冲突文件 / 冲突模组对 / 已隔离
- **冲突视图**:列出每个冲突文件的内部路径、类型、涉及模组;支持搜索与按类型筛选;
  **双击某行**会弹窗列出所有涉及该文件的模组,并对每个模组提供「工坊 / 文件夹 / 隔离」操作
- **模组列表**:按类型自动分组(武器 / 角色 / 声音 / 界面 / 地图 / 特效 / 模型 / 材质 / 脚本…),
  每个模组右侧都有一个**开关**,直接启用 / 关闭(关闭 = 写入游戏附加组件开关(与游戏内一致));旁边还有「工坊 / 文件夹 / 删除」;
  支持搜索、按分类筛选、只看有冲突的模组
- 顶部可「选择游戏目录」、打开「隔离目录」、重新扫描

> 「关闭」记录为目标状态:游戏未运行时立即写入 `left4dead2/addonlist.txt`;游戏运行中会在退出后自动写入(游戏会覆盖该文件)。
> 松散文件夹模组仍用物理隔离(`addons_disabled/`)。「删除」需输入 `DELETE` 二次确认,不可恢复。

### 方式二:双击 .bat(网页版)

| 文件 | 作用 |
|---|---|
| `open_manager.bat` | 打开图形化网页管理器(在本机浏览器里操作) |
| `check_conflicts.bat` | 检查一次,生成 `report.html` / `report.json` |
| `check_and_launch.bat` | 先检查,然后自动通过 Steam 启动游戏 |

双击 `open_manager.bat` 后:程序会在本机启动一个网页(`http://127.0.0.1:8765/`)
并自动打开浏览器。**关闭那个黑色命令行窗口即停止服务。**

> 关于「启用/禁用」:本工具把开关记录为**目标状态**,并在游戏未运行时同步到游戏自己的
> `left4dead2/addonlist.txt`(与游戏内「额外内容→附加组件」同一份状态)。不移动文件、不注入引擎。
> 游戏运行中做的改动会在退出游戏后自动写入(游戏运行时会用内存状态覆盖该文件)。
> 创意工坊模组若彻底不用,请点「工坊」取消订阅。

---

## 命令行用法

工具自带便携 Python,命令行可这样调用(Windows):

```bat
python-embed\python.exe l4d2_conflict_checker.py --serve
```

如果你自己装了 Python 3,也可以用 `python l4d2_conflict_checker.py ...`。

常用参数:

```bash
# 启动本地网页管理器(默认端口 8765,自动打开浏览器)
python-embed\python.exe l4d2_conflict_checker.py --serve

# 换端口 / 不自动打开浏览器
python-embed\python.exe l4d2_conflict_checker.py --serve --port 9000 --no-browser

# 检查并生成报告
python-embed\python.exe l4d2_conflict_checker.py --html report.html --json report.json

# 手动指定游戏目录(任意一层都行)
python-embed\python.exe l4d2_conflict_checker.py --game-dir "E:\SteamLibrary\steamapps\common\Left 4 Dead 2"

# 检查完自动启动游戏 / 常驻监视模组变化
python-embed\python.exe l4d2_conflict_checker.py --launch
python-embed\python.exe l4d2_conflict_checker.py --watch
```

| 参数 | 说明 |
|---|---|
| `--game-dir, -g PATH` | 指定 L4D2 目录;不给则自动从 Steam 注册表/libraryfolders.vdf 查找 |
| `--html FILE` / `--json FILE` | 输出报告 |
| `--min-files N` / `--max-files N` | 报告过滤与显示条数 |
| `--include-scripts` | 把 scripts/ 类冲突也计入(默认忽略) |
| `--include-meta` | 把 addoninfo.txt 等元信息也算冲突(一般不需要) |
| `--launch` | 检查完自动启动游戏(appid 550) |
| `--watch` | 常驻监视模式 |
| `--serve` | 启动本地网页管理器 |
| `--port N` / `--no-browser` | 网页管理器端口 / 不自动开浏览器 |
| `--verbose, -v` | 显示扫描细节 |

退出码:`0` 无冲突,`1` 有冲突,`2` 出错。

---

## 原理(简述)

- L4D2 模组以 **VPK** 打包,VPK 内部有一棵「目录树」记录每个文件的虚拟路径;
- 本工具解析每个 VPK 的目录树,得到每个模组的完整文件列表;
- 以内部路径为 key,统计被 2 个及以上不同模组提供的路径 → 即冲突;
- 从 VPK 内的 `addoninfo.txt` 读取 `addontitle` 作为模组显示名。

已用真实游戏 `pak01_dir.vpk`(22,273 个条目)与 Valve 官方 `vpk` 库逐条对比验证,结果完全一致。

---

## 局限与说明

- 「谁覆盖谁」取决于模组挂载顺序;本工具只找出冲突并列出相关模组,不断言最终生效者。
- 只扫描 `left4dead2/addons`(创意工坊、手动 VPK、松散文件夹);
  游戏本体 `pak01_*.vpk` 不算冲突(模组覆盖本体是正常的)。
- 若从未安装模组,会显示「未找到模组」,属正常现象。

---

## 故障排查

- **窗口一闪而过 / 显示「服务已停止」**:
  1. 先确认是在工具文件夹里双击的 `.bat`;
  2. 若端口被占用,工具会自动换端口;也可手动 `--port 8790`;
  3. 看黑窗口上方是否有报错信息(会保留窗口等待按键)。
- **没有自动找到游戏**:用 `--game-dir` 指定一次即可。
- **自带 Python 被删**:重新解压完整工具包,或安装 Python 3 后使用 `python` 运行。

---

## 文件清单

```
L4D2-Mod-Conflict-Checker/
├─ open_manager.bat           # 双击:启动网页管理器(推荐)
├─ check_conflicts.bat        # 双击:检查 + 生成 HTML 报告
├─ check_and_launch.bat       # 双击:检查 + 启动游戏
├─ l4d2_conflict_checker.py   # 主程序(仅标准库)
├─ l4d2_dashboard.py          # 本地网页管理器
├─ python-embed/              # 便携版 Python(无需另行安装)
├─ 使用说明.md                # 本文件
└─ tests/make_test_vpks.py    # 自测用合成 VPK 生成器(可删除)
```
