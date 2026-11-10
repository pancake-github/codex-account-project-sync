# 第三方运行时许可

本项目自己的源码采用 MIT 许可证。

Windows 独立 EXE 自带 CPython、Tcl/Tk 以及 PyInstaller bootloader。构建流程从实际使用的独立构建环境收集许可文本，嵌入 EXE，并在 Windows ZIP 的 `licenses/` 目录保留可直接阅读的副本。

- CPython：Python Software Foundation 许可及其安装包所附第三方说明，见 `Python-LICENSE.txt`。
- Tcl/Tk：所分发运行时附带的 `tcl-license.terms` 和 `tk-license.terms`。
- PyInstaller：安装包提供的 `COPYING.txt` 包含其许可和 bootloader 例外；可用时以 `PyInstaller-COPYING.txt` 分发。说明见 [PyInstaller License](https://pyinstaller.org/en/stable/license.html)。

这些运行时的许可保持各自原有条款，不由本项目的 MIT 许可证替换。
