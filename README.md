# MediaDupFinder（影视作品去重助手）

一个面向 Windows 的本地桌面工具：同时通过“文件名代表同一作品”和“文件大小 + 完整 MD5 完全重复”两条通道寻找候选，让用户自己决定保留哪个、删除哪个。

例如，下列文件会被归入同一候选组：

- `MIDA-630.mp4`
- `MIDA-630-C.mp4`
- `MIDA-630-4k.mov`

下列中文片名也会被识别为同一候选作品：

- `寒战.mp4`
- `寒战1.mp4`
- `经典剧情《寒战1》.mp4`

## 已完成的功能

- Windows 7 SP1、Windows 10、Windows 11 兼容技术路线
- 同时添加多个目录，可选是否扫描子目录
- 默认扫描 17 类常见视频，也可切换为全部文件
- 识别 `MIDA-630`、`FC2-PPV-1234567`、1PONDO、Tokyo-Hot、字母数字混合前缀等作品编号
- 统一番号前导零，例如 `ABC-001` 与 `ABC-1`
- 去除网站域名、常见站点前缀、发布组、`4K`、`BluRay`、编码、字幕、语言和复合标签
- 繁简体匹配、罗马数字序号转换、英文标题词序变化匹配
- 年份安全规则：单侧年份可匹配，双侧不同年份不合并
- 中文/英文文件名模糊相似度匹配，并显示匹配原因和置信度
- MD5 提供关闭、智能、完整三档；默认智能模式只处理同大小候选
- 大文件先读取开头/中间/结尾各 1 MB，快速指纹相同后才计算完整 MD5
- 本地缓存已验证的 MD5，复用前重新核对文件状态和快速指纹
- 大扫描量开始前显示读取上限和时间区间，可继续、跳过 MD5 或停止
- 哈希阶段显示真实读取容量、吞吐速度和预计剩余时间
- 自动合并文件名与 MD5 结果，避免同一文件重复出现在不同组
- 主动区分 `CD1/CD2`、`Part1/Part2`，降低把分段文件当重复文件的风险
- 逐组手动设置“保留 / 删除 / 未决定”
- 按“分辨率 → 格式 → 文件大小”提供智能保留建议
- 可选读取视频真实分辨率、时长和编码（需要外置 `ffprobe.exe`）
- 片长差异过大时标为重点复核，并禁止智能批量标记删除
- 结果表直接展示编码、完整 MD5 状态、目录等信息，并支持横向滚动
- 文件结果右键菜单支持完整信息、打开、资源管理器定位、Windows 属性和复制路径
- 完整信息窗口集中展示文件系统、媒体、MD5、名称解析及候选组判定信息
- 删除前二次确认；不提供静默永久删除，Windows 下默认进入回收站
- 阻止把同一候选组全部标记删除
- 导出带 BOM 的 CSV 报告，可直接用 Excel 打开
- 后台扫描和停止按钮，大目录扫描时界面不会因主任务阻塞
- 自动保存窗口、目录和扫描偏好；记录回收站操作历史
- GitHub Actions 自动构建 Windows x64 与 x86 发布包

## 直接使用

发布版用户只需要：

1. 下载与你系统匹配的 ZIP。多数电脑选择 `Windows-x64`；32 位 Windows 7 选择 `Windows-x86`。
2. 解压整个 ZIP。
3. 双击 `MediaDupFinder.exe`。
4. 添加目录并开始扫描。
5. 逐组检查，设置保留和删除，再执行回收站操作。

> Windows 7 必须是 SP1。极精简或长期未更新的 Windows 7 可能缺少 Universal C Runtime，此时需要先安装微软 Visual C++ 2015–2022 Redistributable 或相应系统更新。Windows 10/11 通常已具备所需运行组件。

更细的操作步骤见 [用户使用指南](docs/USER_GUIDE.md)。

## 从源码运行

运行环境只需要 Python 3.8 或更高版本，运行时没有第三方 Python 依赖。

Windows 用户可以双击：

```text
run_from_source.bat
```

也可以直接双击 `MediaDupFinder.pyw`。

也可在终端运行：

```powershell
$env:PYTHONPATH = "src"
python -m media_dup_finder
```

## 构建 Windows EXE

为了兼容 Windows 7，构建环境固定为：

- Python 3.8.x（推荐 3.8.10）
- PyInstaller 5.13.2
- Tkinter / Tcl-Tk 8.6

Windows 上安装 Python 3.8 后，双击 `build_windows.bat`。脚本会自动创建隔离环境、运行测试、生成 EXE 和 ZIP：

```text
dist\MediaDupFinder.exe
release\MediaDupFinder-v1.2.2-Windows-x64.zip
```

PyInstaller 不是交叉编译器，因此 Windows EXE 必须在 Windows 或 GitHub 的 Windows Runner 上生成。完整说明见 [Windows 构建与发布](docs/WINDOWS_BUILD.md)。

## 发布到 GitHub

仓库已经带有 `.github/workflows/build-windows.yml`：

1. 把整个项目推送到 GitHub 的 `main` 分支。
2. 打开仓库的 **Actions** 页面，可手动运行 **Build Windows releases**。
3. 每次推送都会生成 x64、x86 两个可下载的工作流产物。
4. 创建并推送 `v1.2.2` 标签时，工作流还会自动创建 GitHub Release 并附上两个 ZIP。

示例命令：

```bash
git tag v1.2.2
git push origin v1.2.2
```

## 识别逻辑概览

程序只把结果称为“候选组”，不会断言文件一定相同：

1. Unicode、大小写、繁简体和尾部罗马数字归一化。
2. 清理网站、发布组、画质、编码、片源、复合字幕语言及宣传标签。
3. 优先提取作品编号；编号相同强匹配，编号不同明确隔离。
4. 年份只生成安全别名；两侧明确年份不同则隔离同名翻拍作品。
5. 综合字符序列、二元字符集合、包含关系和英文词集合计算置信度。
6. MD5 智能模式对同大小文件先计算三段快速指纹，只对指纹相同者读取完整内容；完整模式则校验全部同大小候选。
7. 合并两条识别通道，对连锁匹配再次按锚点拆组。
8. 读取到视频时长后，为片长差异组增加保护，而不是提高自动删除力度。

详细规则与边界见 [算法说明](docs/ALGORITHM.md)。

## 安全边界

- 扫描和分组阶段是只读操作；MD5 只在本机顺序读取文件。
- 智能选择只做标记，不会自动执行删除。
- 同一组不能全部标记删除。
- Windows 删除使用带撤销能力的系统回收站接口。
- 为避免网络共享文件被不可恢复删除，UNC 网络路径会被拒绝。
- 不跟随符号链接，也不处理目录。
- MD5 期间会检测文件变化；缓存复用前也会重新验证快速指纹，删除前再次检查大小和修改时间。
- 片长差异较大的文件不会被“智能选择”自动标记删除。
- 软件完全本地运行，不上传文件名、路径或媒体内容。

文件名相似不等于内容相同。正式删除前仍应播放或打开关键文件进行人工确认，并保留重要数据的独立备份。

## 项目结构

```text
MediaDupFinder/
├─ src/media_dup_finder/       核心代码与桌面界面
├─ tests/                      自动化测试
├─ docs/                       用户、算法、构建文档
├─ resources/                  Windows manifest 与版本信息
├─ scripts/                    发布打包脚本
├─ tools/                      可选 ffprobe 放置说明
├─ .github/workflows/          Windows x64/x86 自动构建
├─ build_windows.bat           本地一键构建
└─ run_from_source.bat         源码直接启动
```

## 许可证

项目代码采用 [MIT License](LICENSE)。仓库不包含 FFmpeg/ffprobe 二进制文件。
