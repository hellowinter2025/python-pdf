# PDF 压缩工具

一个基于 Python、pikepdf、Pillow 和 CustomTkinter 的本地 PDF 压缩工具。

## 功能

- 按 JPEG 质量批量压缩 PDF 内嵌图片
- 按目标大小自动探测压缩参数
- 保留文字、矢量内容、页面结构
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
