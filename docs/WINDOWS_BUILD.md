# Windows 构建与 GitHub 发布

## 为什么固定 Python 3.8

Python 3.8 是官方仍可用于 Windows 7 的最后一代 Python。项目同时固定 PyInstaller 5.13.2，其 Windows bootloader 以 Windows 7 feature level 构建。由这套工具生成的程序同样可以在 Windows 10/11 运行。

Windows 7 已停止支持，只建议在 SP1 且已安装安全更新的环境使用。极精简系统可能还需要安装 Universal C Runtime 或 Microsoft Visual C++ 2015–2022 Redistributable。

## 本地一键构建

1. 安装 32 位或 64 位 Python 3.8.10，并勾选 Python Launcher。
2. 解压源码，双击 `build_windows.bat`。
3. 脚本依次创建 `.venv-build`、安装固定版本构建工具、运行测试、生成 EXE、生成 ZIP。
4. 结果位于 `dist` 和 `release`。

EXE 架构与用于构建的 Python 架构一致：64 位 Python 生成 x64，32 位 Python 生成 x86。

## GitHub Actions

推送仓库后，Actions 工作流会在 Windows Runner 上分别安装 Python 3.8 x64 和 x86，执行全部测试并生成两个 ZIP。

普通分支推送的结果位于 Actions 运行详情下方的 Artifacts。推送 `v*` 标签时，第二个任务会下载两个架构产物并自动创建 Release。

## 正式发布前检查

- 在干净的 Windows 7 SP1 x64 虚拟机实际启动和扫描。
- 在 Windows 10 与 Windows 11 各测试一次。
- 用无关的演示文件验证回收站恢复流程。
- 对 EXE 和 ZIP 计算 SHA-256，并写入 Release 说明。
- 如有预算，使用代码签名证书签署 EXE，减少 SmartScreen 警告。
- 不要把来源不明的 ffprobe 二进制直接并入 Release。

