# PDF 压缩工具

一个本地运行的 PDF 压缩工具，基于 Python、pikepdf、Pillow 和 CustomTkinter 构建。

## 纯 AI 编程声明

本项目为纯 AI 编程项目：代码实现、GUI 美化、文档整理和 Git 仓库初始化均由 AI 编程完成。

- AI 编程工具：OpenAI Codex
- 使用模型：GPT-5
- 人类参与：提出需求、验收方向和运行结果判断

## 功能

- 按 JPEG 质量批量压缩 PDF 内嵌图片
- 按目标大小自动探测压缩参数
- 在保留文字、矢量内容和页面结构的前提下降低图片体积
- 支持文件夹递归添加和拖拽添加
- 支持自定义输出目录、覆盖策略和 `_compressed` 文件名后缀

## 运行

```powershell
py pdf_gui.pyw
```

如缺少依赖，可先运行：

```powershell
pip install pikepdf Pillow customtkinter tkinterdnd2
```

也可以双击 `启动.bat`。

## 命令行

```powershell
py pdf_compress.py input.pdf -s 19M
py pdf_compress.py input.pdf -q 75
```

目标大小模式会优先调节 JPEG 质量；如果仍无法达到目标，会在保留文字和矢量内容的前提下逐步降低图片分辨率。
