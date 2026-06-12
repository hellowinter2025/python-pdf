"""
PDF 压缩工具 —— GUI 界面 (CustomTkinter 现代化版本)

基于 CustomTkinter 构建美观的图形界面，支持配置参数、拖拽添加文件、批量压缩、实时进度显示。

依赖：
  pip install pikepdf Pillow customtkinter

运行：
  py pdf_gui.pyw
"""

import os
import sys
import threading
import time
import multiprocessing
import base64
import zlib
from pathlib import Path

# 拖拽必须在 import customtkinter 之前加载 tkDnD
_dnd_ok = False
try:
    from tkinterdnd2 import DND_FILES, Tk as DndTk
    _dnd_ok = True
except ImportError:
    pass

import customtkinter as ctk
from tkinter import filedialog, messagebox
from PIL import Image

# 导入压缩核心
from pdf_compress import compress_pdf, compress_pdf_to_target, format_size, parse_size

# 设置外观
ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

# ======================================================================
# 主题色
# ======================================================================

class Theme:
    BG = "#f3f6fb"
    BG2 = "#ffffff"
    BG3 = "#eef3fb"
    SURFACE = "#dbe7f5"
    SURFACE_HOVER = "#c6d8ef"
    PRIMARY = "#2563eb"
    PRIMARY_HOVER = "#1d4ed8"
    PRIMARY_DARK = "#0f3aa7"
    ACCENT = "#0f766e"
    ACCENT2 = "#d97706"
    SUCCESS = "#059669"
    WARNING = "#d97706"
    ERROR = "#e11d48"
    TEXT = "#0f172a"
    TEXT2 = "#334155"
    TEXT3 = "#64748b"
    BORDER = "#d7e0ec"
    CARD = "#ffffff"
    INPUT_BG = "#ffffff"
    PROGRESS = "#3b82f6"
    DIVIDER = "#d7e0ec"
    ROW = "#f8fbff"
    ROW_ALT = "#edf3fb"


# ======================================================================
# 窗口图标（内嵌 ICO 数据）
# ======================================================================

def _make_icon():
    """Generate this app's custom PDF compressor icon as a PNG data string."""
    from PIL import Image, ImageDraw, ImageFont
    import io

    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Soft app tile.
    draw.rounded_rectangle([14, 14, 242, 242], radius=48, fill="#dbeafe")
    draw.rounded_rectangle([24, 24, 232, 232], radius=40, outline="#93c5fd", width=3)

    # Document body and folded corner.
    doc = [66, 42, 190, 218]
    draw.rounded_rectangle(doc, radius=14, fill="#ffffff", outline="#bfdbfe", width=3)
    draw.polygon([(154, 42), (190, 78), (154, 78)], fill="#e0f2fe", outline="#bfdbfe")
    draw.line([(154, 42), (154, 78), (190, 78)], fill="#bfdbfe", width=3)

    # Compact text lines, suggesting preserved text content.
    for y, w in [(104, 80), (120, 66), (136, 84), (152, 58)]:
        draw.rounded_rectangle([86, y, 86 + w, y + 6], radius=3, fill="#cbd5e1")

    # Compression mark: two blue blocks becoming one teal line.
    draw.rounded_rectangle([82, 176, 122, 190], radius=5, fill="#2563eb")
    draw.rounded_rectangle([134, 176, 174, 190], radius=5, fill="#60a5fa")
    draw.line([(92, 202), (166, 202)], fill="#0f766e", width=7)
    draw.polygon([(166, 202), (150, 192), (150, 212)], fill="#0f766e")

    # PDF badge.
    draw.rounded_rectangle([50, 58, 122, 92], radius=10, fill="#2563eb")
    try:
        font = ImageFont.truetype("arialbd.ttf", 22)
    except Exception:
        font = ImageFont.load_default()
    text = "PDF"
    draw.text((66, 62), text, fill="#ffffff", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _set_window_icon(root):
    """设置窗口图标"""
    try:
        icon_data = _make_icon()
        # 写入临时文件
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_icon.png")
        with open(icon_path, "wb") as f:
            f.write(base64.b64decode(icon_data))
        root.iconphoto(False, ctk.CTkImage(light_image=Image.open(icon_path), size=(32, 32)))
        # 也设置任务栏图标
        root.wm_iconphoto(False, Image.open(icon_path))
    except Exception:
        pass


# ======================================================================
# 主窗口
# ======================================================================

class PDFCompressorGUI:
    """PDF 压缩工具 GUI —— CustomTkinter 现代化版本"""

    def __init__(self):
        # 高 DPI（必须在创建窗口前设置）
        try:
            from ctypes import windll
            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

        # 根窗口：优先使用 DndTk 以支持拖拽
        self._dnd_available = False
        if _dnd_ok:
            try:
                self.root = DndTk()
                self._dnd_available = True
            except Exception:
                self.root = ctk.CTk()
        else:
            self.root = ctk.CTk()

        self.root.title("PDF 压缩工具")
        self.root.geometry("940x760")
        self.root.minsize(820, 640)
        if isinstance(self.root, ctk.CTk):
            self.root.configure(fg_color=Theme.BG)
        else:
            self.root.configure(bg=Theme.BG)

        # 设置图标
        _set_window_icon(self.root)

        # 状态
        self.task_items: list[dict] = []
        self.task_widgets: list[ctk.CTkFrame] = []
        self.is_running = False
        self.cancel_flag = False
        self.total_original = 0
        self.total_compressed = 0
        self.stat_cards = {}

        # 构建界面
        self._build_ui()

        # 拖拽绑定
        self._setup_dnd()

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self):
        # 主容器，左右边距
        main = ctk.CTkFrame(self.root, fg_color=Theme.BG)
        main.pack(fill="both", expand=True, padx=24, pady=16)

        # ===== 标题区 =====
        self._build_title(main)

        self._build_stats(main)

        # ===== 参数配置区 =====
        self._build_config(main)

        # ===== 文件列表区 =====
        self._build_file_list(main)

        # ===== 底部操作栏 =====
        self._build_bottom(main)

    def _build_title(self, parent):
        title_frame = ctk.CTkFrame(parent, fg_color=Theme.BG)
        title_frame.pack(fill="x", pady=(2, 14))

        # 左侧：图标 + 标题
        left = ctk.CTkFrame(title_frame, fg_color=Theme.BG)
        left.pack(side="left")

        # 应用图标（用 CTkImage）
        try:
            self._app_icon = ctk.CTkImage(
                light_image=Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_icon.png")),
                size=(32, 32)
            )
            ctk.CTkLabel(left, image=self._app_icon, text="").pack(side="left", padx=(0, 10))
        except Exception:
            ctk.CTkLabel(left, text="📄", font=("Segoe UI Emoji", 28)).pack(side="left", padx=(0, 10))

        ctk.CTkLabel(
            left, text="PDF 压缩工具",
            font=("Microsoft YaHei UI", 20, "bold"),
            text_color=Theme.TEXT
        ).pack(side="left")

        # 右侧：版本信息
        ctk.CTkLabel(
            title_frame, text="智能图像压缩 · 保留矢量内容",
            font=("Microsoft YaHei UI", 11),
            text_color=Theme.TEXT3
        ).pack(side="right", padx=(0, 4))

    def _build_stats(self, parent):
        stats = ctk.CTkFrame(parent, fg_color=Theme.BG)
        stats.pack(fill="x", pady=(0, 14))

        for key, title, value, color in [
            ("files", "待处理文件", "0", Theme.PRIMARY_HOVER),
            ("input", "原始大小", "0 B", Theme.TEXT),
            ("output", "输出大小", "0 B", Theme.ACCENT),
            ("saved", "节省比例", "0.0%", Theme.SUCCESS),
        ]:
            card = ctk.CTkFrame(stats, fg_color=Theme.BG2, corner_radius=8, border_width=1, border_color=Theme.BORDER)
            card.pack(side="left", fill="x", expand=True, padx=(0, 10) if key != "saved" else 0)
            ctk.CTkLabel(
                card, text=title,
                font=("Microsoft YaHei UI", 10), text_color=Theme.TEXT3
            ).pack(anchor="w", padx=14, pady=(10, 0))
            value_label = ctk.CTkLabel(
                card, text=value,
                font=("Microsoft YaHei UI", 17, "bold"), text_color=color
            )
            value_label.pack(anchor="w", padx=14, pady=(0, 10))
            self.stat_cards[key] = value_label

    def _update_stats(self):
        file_count = len(self.task_items)
        input_size = sum(os.path.getsize(item["file_path"]) for item in self.task_items if os.path.exists(item["file_path"]))
        output_size = self.total_compressed if self.total_compressed else 0
        saved_ratio = (1 - output_size / input_size) * 100 if input_size > 0 and output_size > 0 else 0

        if self.stat_cards:
            self.stat_cards["files"].configure(text=str(file_count))
            self.stat_cards["input"].configure(text=format_size(input_size))
            self.stat_cards["output"].configure(text=format_size(output_size))
            self.stat_cards["saved"].configure(text=f"{saved_ratio:.1f}%")

    def _build_config(self, parent):
        """构建参数配置区"""
        config = ctk.CTkFrame(parent, fg_color=Theme.BG2, corner_radius=8, border_width=1, border_color=Theme.BORDER)
        config.pack(fill="x", pady=(0, 14))

        inner = ctk.CTkFrame(config, fg_color=Theme.BG2)
        inner.pack(fill="x", padx=20, pady=16)

        # 第一行：压缩模式 + 输出目录
        row0 = ctk.CTkFrame(inner, fg_color=Theme.BG2)
        row0.pack(fill="x", pady=(0, 12))

        # 左侧：模式
        left_col = ctk.CTkFrame(row0, fg_color=Theme.BG2)
        left_col.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            left_col, text="压缩模式",
            font=("Microsoft YaHei UI", 12, "bold"),
            text_color=Theme.TEXT
        ).pack(anchor="w")

        mode_row = ctk.CTkFrame(left_col, fg_color=Theme.BG2)
        mode_row.pack(fill="x", pady=(6, 0))

        self.mode_var = ctk.StringVar(value="quality")
        self.rb_quality = ctk.CTkRadioButton(
            mode_row, text="按质量压缩", variable=self.mode_var, value="quality",
            command=self._on_mode_change,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color=Theme.TEXT, font=("Microsoft YaHei UI", 11),
            radiobutton_width=18, radiobutton_height=18,
            corner_radius=10, border_width_unchecked=2, border_width_checked=2
        )
        self.rb_quality.pack(side="left", padx=(0, 20))

        self.rb_target = ctk.CTkRadioButton(
            mode_row, text="按目标大小", variable=self.mode_var, value="target_size",
            command=self._on_mode_change,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color=Theme.TEXT, font=("Microsoft YaHei UI", 11),
            radiobutton_width=18, radiobutton_height=18,
            corner_radius=10, border_width_unchecked=2, border_width_checked=2
        )
        self.rb_target.pack(side="left")

        # 右侧：输出目录
        right_col = ctk.CTkFrame(row0, fg_color=Theme.BG2)
        right_col.pack(side="right", fill="both", expand=True, padx=(32, 0))

        ctk.CTkLabel(
            right_col, text="输出目录",
            font=("Microsoft YaHei UI", 12, "bold"),
            text_color=Theme.TEXT
        ).pack(anchor="w")

        dir_row = ctk.CTkFrame(right_col, fg_color=Theme.BG2)
        dir_row.pack(fill="x", pady=(6, 0))

        self.output_dir_var = ctk.StringVar(value="")
        self.output_entry = ctk.CTkEntry(
            dir_row, textvariable=self.output_dir_var,
            fg_color=Theme.INPUT_BG, border_color=Theme.BORDER,
            text_color=Theme.TEXT, placeholder_text="默认：原文件同目录",
            font=("Microsoft YaHei UI", 10), height=34, corner_radius=6
        )
        self.output_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))

        browse_btn = ctk.CTkButton(
            dir_row, text="浏览", width=60, height=32,
            fg_color=Theme.SURFACE, hover_color=Theme.SURFACE_HOVER,
            text_color=Theme.TEXT, font=("Microsoft YaHei UI", 10),
            corner_radius=6, command=self._browse_output_dir
        )
        browse_btn.pack(side="right")

        # 覆盖选项
        self.overwrite_var = ctk.BooleanVar(value=True)
        cb = ctk.CTkCheckBox(
            right_col, text="覆盖已存在的文件", variable=self.overwrite_var,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color=Theme.TEXT2, font=("Microsoft YaHei UI", 10),
            checkbox_width=16, checkbox_height=16, corner_radius=4, border_width=2
        )
        cb.pack(anchor="w", pady=(4, 0))

        # 文件夹模式：是否加 _compressed 后缀
        self.compressed_suffix_var = ctk.BooleanVar(value=True)
        cb2 = ctk.CTkCheckBox(
            right_col, text="文件名加 _compressed 后缀", variable=self.compressed_suffix_var,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color=Theme.TEXT2, font=("Microsoft YaHei UI", 10),
            checkbox_width=16, checkbox_height=16, corner_radius=4, border_width=2
        )
        cb2.pack(anchor="w", pady=(4, 0))

        # 第二行：质量滑块 / 目标大小（切换显示）
        self.param_frame = ctk.CTkFrame(inner, fg_color=Theme.BG2)
        self.param_frame.pack(fill="x")

        # --- 质量面板 ---
        self.quality_frame = ctk.CTkFrame(self.param_frame, fg_color=Theme.BG2)
        self.quality_frame.pack(fill="x")

        ctk.CTkLabel(
            self.quality_frame, text="JPEG 质量",
            font=("Microsoft YaHei UI", 11), text_color=Theme.TEXT2
        ).pack(side="left")

        self.quality_var = ctk.IntVar(value=75)
        self.quality_scale = ctk.CTkSlider(
            self.quality_frame, from_=5, to=95, variable=self.quality_var,
            command=self._on_quality_change,
            fg_color=Theme.SURFACE, progress_color=Theme.PRIMARY,
            button_color=Theme.PRIMARY, button_hover_color=Theme.PRIMARY_HOVER,
            height=16, number_of_steps=90, width=300
        )
        self.quality_scale.pack(side="left", padx=(12, 12))

        self.quality_display = ctk.CTkLabel(
            self.quality_frame, text="75",
            font=("Microsoft YaHei UI", 16, "bold"),
            text_color=Theme.PRIMARY_HOVER, width=3
        )
        self.quality_display.pack(side="left")

        # 质量预设按钮
        for q, label in [(50, "低"), (75, "中"), (90, "高")]:
            btn = ctk.CTkButton(
                self.quality_frame, text=label, width=36, height=24,
                fg_color=Theme.BG3, hover_color=Theme.SURFACE_HOVER,
                text_color=Theme.TEXT2, font=("Microsoft YaHei UI", 9),
                corner_radius=4,
                command=lambda q=q: self._set_quality(q)
            )
            btn.pack(side="left", padx=2)

        # --- 目标大小面板（初始隐藏）---
        self.target_frame = ctk.CTkFrame(self.param_frame, fg_color=Theme.BG2)

        ctk.CTkLabel(
            self.target_frame, text="目标大小",
            font=("Microsoft YaHei UI", 11), text_color=Theme.TEXT2
        ).pack(side="left")

        self.target_size_var = ctk.StringVar(value="10")
        self.target_entry = ctk.CTkEntry(
            self.target_frame, textvariable=self.target_size_var,
            fg_color=Theme.INPUT_BG, border_color=Theme.BORDER,
            text_color=Theme.TEXT, placeholder_text="输入数值",
            font=("Microsoft YaHei UI", 11), width=88, height=34, corner_radius=6
        )
        self.target_entry.pack(side="left", padx=(12, 4))

        self.target_unit_var = ctk.StringVar(value="MB")
        self.target_unit = ctk.CTkOptionMenu(
            self.target_frame, variable=self.target_unit_var,
            values=["KB", "MB", "GB"],
            fg_color=Theme.INPUT_BG, button_color=Theme.SURFACE,
            button_hover_color=Theme.SURFACE_HOVER, text_color=Theme.TEXT,
            dropdown_fg_color=Theme.BG3, dropdown_hover_color=Theme.SURFACE,
            font=("Microsoft YaHei UI", 10), width=70, height=32,
            corner_radius=6, dropdown_font=("Microsoft YaHei UI", 10)
        )
        self.target_unit.pack(side="left")

        # 快捷预设
        for size, label in [("5", "5MB"), ("10", "10MB"), ("20", "20MB"), ("50", "50MB")]:
            btn = ctk.CTkButton(
                self.target_frame, text=label, width=44, height=24,
                fg_color=Theme.BG3, hover_color=Theme.SURFACE_HOVER,
                text_color=Theme.TEXT2, font=("Microsoft YaHei UI", 9),
                corner_radius=4,
                command=lambda s=size: self._set_target_size(s)
            )
            btn.pack(side="left", padx=2)

    def _build_file_list(self, parent):
        """构建文件列表区"""
        file_section = ctk.CTkFrame(parent, fg_color=Theme.BG)
        file_section.pack(fill="both", expand=True, pady=(0, 12))

        # 标题行
        header = ctk.CTkFrame(file_section, fg_color=Theme.BG)
        header.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(
            header, text="文件列表",
            font=("Microsoft YaHei UI", 13, "bold"), text_color=Theme.TEXT
        ).pack(side="left")

        self.file_count_label = ctk.CTkLabel(
            header, text="(0 个文件)",
            font=("Microsoft YaHei UI", 10), text_color=Theme.TEXT3
        )
        self.file_count_label.pack(side="left", padx=(8, 0))

        # 按钮组
        btn_group = ctk.CTkFrame(header, fg_color=Theme.BG)
        btn_group.pack(side="right")

        self.clear_all_btn = ctk.CTkButton(
            btn_group, text="清空", width=56, height=28,
            fg_color=Theme.BG2, hover_color=Theme.ERROR,
            text_color=Theme.TEXT2, font=("Microsoft YaHei UI", 10),
            corner_radius=6, command=self._clear_all
        )
        self.clear_all_btn.pack(side="right", padx=(6, 0))

        self.add_dir_btn = ctk.CTkButton(
            btn_group, text="+ 文件夹", width=76, height=28,
            fg_color=Theme.SURFACE, hover_color=Theme.SURFACE_HOVER,
            text_color=Theme.TEXT, font=("Microsoft YaHei UI", 10),
            corner_radius=6, command=self._add_directory
        )
        self.add_dir_btn.pack(side="right", padx=(6, 0))

        self.add_file_btn = ctk.CTkButton(
            btn_group, text="+ 添加文件", width=88, height=28,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color="white", font=("Microsoft YaHei UI", 10, "bold"),
            corner_radius=6, command=self._add_files
        )
        self.add_file_btn.pack(side="right")

        # 文件列表容器（可滚动）
        list_outer = ctk.CTkScrollableFrame(
            file_section, fg_color=Theme.BG2, corner_radius=8,
            scrollbar_fg_color=Theme.SURFACE, scrollbar_button_color=Theme.PRIMARY,
            border_width=1, border_color=Theme.BORDER
        )
        list_outer.pack(fill="both", expand=True)

        self.list_container = list_outer
        self.scrollable_frame = list_outer

        # 空列表提示
        self.empty_hint = ctk.CTkLabel(
            list_outer, text="\n暂无文件\n点击添加文件，或将 PDF / 文件夹拖到窗口中\n",
            font=("Microsoft YaHei UI", 13), text_color=Theme.TEXT3
        )
        self.empty_hint.pack(pady=60)

        # 拖拽提示
        if self._dnd_available:
            hint_text = "✓ 支持拖拽文件到窗口"
            hint_color = Theme.SUCCESS
        else:
            hint_text = ""
            hint_color = Theme.TEXT3

        self.drag_hint = ctk.CTkLabel(
            file_section, text=hint_text,
            font=("Microsoft YaHei UI", 9), text_color=hint_color
        )
        self.drag_hint.pack(pady=(4, 0))

    def _build_bottom(self, parent):
        """构建底部操作栏"""
        bottom = ctk.CTkFrame(parent, fg_color=Theme.BG2, corner_radius=8, border_width=1, border_color=Theme.BORDER)
        bottom.pack(fill="x")

        row = ctk.CTkFrame(bottom, fg_color=Theme.BG2)
        row.pack(fill="x", padx=14, pady=12)

        # 总计信息
        self.summary_label = ctk.CTkLabel(
            row, text="",
            font=("Microsoft YaHei UI", 10), text_color=Theme.TEXT2, anchor="w"
        )
        self.summary_label.pack(side="left", fill="x", expand=True)

        # 按钮组
        btn_group = ctk.CTkFrame(row, fg_color=Theme.BG2)
        btn_group.pack(side="right")

        self.cancel_btn = ctk.CTkButton(
            btn_group, text="取消", width=92, height=40,
            fg_color=Theme.SURFACE, hover_color=Theme.ERROR,
            text_color=Theme.TEXT, font=("Microsoft YaHei UI", 11, "bold"),
            corner_radius=8, command=self._cancel
        )

        self.start_btn = ctk.CTkButton(
            btn_group, text="▶  开始压缩", width=140, height=38,
            fg_color=Theme.PRIMARY, hover_color=Theme.PRIMARY_HOVER,
            text_color="white", font=("Microsoft YaHei UI", 12, "bold"),
            corner_radius=10, command=self._start_compress
        )
        self.start_btn.pack(side="left", padx=(8, 0))

    # ------------------------------------------------------------------
    # 拖拽支持
    # ------------------------------------------------------------------

    def _setup_dnd(self):
        """在 DndTk 根窗口上注册拖拽"""
        if not self._dnd_available:
            return
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception as e:
            self._dnd_available = False
            try:
                self.drag_hint.configure(text=f"拖拽不可用", text_color=Theme.WARNING)
            except Exception:
                pass

    def _on_drop(self, event):
        """处理拖拽文件"""
        try:
            files = self.root.tk.splitlist(event.data)
        except Exception:
            files = [event.data]

        for f in files:
            f = f.strip("{} \n\r")
            if not f:
                continue
            if f.lower().endswith(".pdf") and os.path.isfile(f):
                self._add_single_file(f)
            elif os.path.isdir(f):
                self._add_pdfs_from_dir(f)

    # ------------------------------------------------------------------
    # 交互逻辑
    # ------------------------------------------------------------------

    def _on_mode_change(self):
        mode = self.mode_var.get()
        if mode == "quality":
            self.quality_frame.pack(fill="x")
            self.target_frame.pack_forget()
        else:
            self.target_frame.pack(fill="x")
            self.quality_frame.pack_forget()

    def _on_quality_change(self, value):
        try:
            val = int(float(value))
            self.quality_display.configure(text=str(val))
        except Exception:
            pass

    def _set_quality(self, q):
        self.quality_var.set(q)
        self.quality_display.configure(text=str(q))

    def _set_target_size(self, size):
        self.target_size_var.set(size)

    def _browse_output_dir(self):
        dir_path = filedialog.askdirectory(title="选择输出目录")
        if dir_path:
            self.output_dir_var.set(dir_path)

    def _add_files(self):
        files = filedialog.askopenfilenames(
            title="选择 PDF 文件",
            filetypes=[("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
        )
        for f in files:
            self._add_single_file(f)

    def _add_directory(self):
        dir_path = filedialog.askdirectory(title="选择包含 PDF 的文件夹")
        if dir_path:
            self._add_pdfs_from_dir(dir_path)

    def _add_pdfs_from_dir(self, dir_path):
        """递归搜索文件夹中所有 PDF（含不限层数的子文件夹）"""
        dir_path = os.path.abspath(dir_path)
        pdfs = sorted(Path(dir_path).rglob("*.pdf"))
        for p in pdfs:
            self._add_single_file(str(p), source_dir=dir_path)

    def _add_single_file(self, file_path, source_dir=None):
        file_path = os.path.abspath(file_path)
        for item in self.task_items:
            if item["file_path"] == file_path:
                return
        if not file_path.lower().endswith(".pdf"):
            return

        # 计算相对于源文件夹的路径（用于保持输出目录结构）
        if source_dir and file_path.startswith(source_dir):
            rel_path = os.path.relpath(file_path, source_dir)
        else:
            rel_path = os.path.basename(file_path)

        item = {
            "file_path": file_path,
            "source_dir": source_dir,   # 源文件夹路径（文件夹模式时有值）
            "rel_path": rel_path,       # 相对于源文件夹的路径（如 "子文件夹/文件.pdf"）
            "status": "等待中",
            "progress": 0,
            "result_text": "",
        }
        self.task_items.append(item)
        widget = self._create_task_widget(item)
        self.task_widgets.append(widget)
        self._refresh_list()
        self._update_stats()

    def _create_task_widget(self, item):
        """为任务创建 UI 行"""
        row_color = Theme.ROW if len(self.task_items) % 2 else Theme.ROW_ALT
        row = ctk.CTkFrame(self.scrollable_frame, fg_color=row_color, corner_radius=6, height=48)
        row.pack(fill="x", padx=6, pady=3)
        row.pack_propagate(False)

        idx = len(self.task_items) - 1

        # 文件图标
        try:
            if not hasattr(self, '_file_icon'):
                self._file_icon = ctk.CTkImage(
                    light_image=Image.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_icon.png")),
                    size=(20, 20)
                )
            ctk.CTkLabel(row, image=self._file_icon, text="", width=32).pack(side="left", padx=(10, 4))
        except Exception:
            ctk.CTkLabel(row, text="PDF", font=("Microsoft YaHei UI", 10, "bold"), width=32, text_color=Theme.PRIMARY_HOVER).pack(side="left", padx=(10, 4))

        # 文件名（文件夹模式显示相对路径）
        rel = item.get("rel_path", "")
        display_name = rel if (rel and os.path.dirname(rel)) else os.path.basename(item["file_path"])
        name_label = ctk.CTkLabel(
            row, text=display_name,
            font=("Microsoft YaHei UI", 10), text_color=Theme.TEXT, anchor="w"
        )
        name_label.pack(side="left", fill="x", expand=True, padx=(0, 10))

        # 文件大小
        size = os.path.getsize(item["file_path"])
        size_label = ctk.CTkLabel(
            row, text=format_size(size),
            font=("Microsoft YaHei UI", 9), text_color=Theme.TEXT3, width=72
        )
        size_label.pack(side="left")

        # 状态
        status_label = ctk.CTkLabel(
            row, text="等待中",
            font=("Microsoft YaHei UI", 9), text_color=Theme.TEXT3, width=76
        )
        status_label.pack(side="left")

        # 进度条
        progress_bar = ctk.CTkProgressBar(
            row, width=92, height=7,
            fg_color=Theme.INPUT_BG, progress_color=Theme.PROGRESS, corner_radius=4
        )
        progress_bar.pack(side="left", padx=(8, 8))
        progress_bar.set(0)

        # 结果
        result_label = ctk.CTkLabel(
            row, text="",
            font=("Microsoft YaHei UI", 9), text_color=Theme.TEXT3, width=190
        )
        result_label.pack(side="left")

        # 删除按钮
        remove_btn = ctk.CTkButton(
            row, text="×", width=26, height=26,
            fg_color="transparent", hover_color=Theme.ERROR,
            text_color=Theme.TEXT3, font=("Microsoft YaHei UI", 14),
            corner_radius=6,
            command=lambda: self._remove_task(idx)
        )
        remove_btn.pack(side="right", padx=(0, 10))

        # 保存引用
        item["name_label"] = name_label
        item["size_label"] = size_label
        item["status_label"] = status_label
        item["progress_bar"] = progress_bar
        item["result_label"] = result_label
        item["remove_btn"] = remove_btn
        item["widget"] = row

        return row

    def _remove_task(self, idx):
        if self.is_running:
            return
        if 0 <= idx < len(self.task_items):
            item = self.task_items[idx]
            item["widget"].destroy()
            self.task_items.pop(idx)
            self.task_widgets.pop(idx)
            for i, it in enumerate(self.task_items):
                it["remove_btn"].configure(command=lambda i=i: self._remove_task(i))
            self._refresh_list()
            self._update_stats()

    def _clear_all(self):
        if self.is_running:
            return
        for item in self.task_items:
            item["widget"].destroy()
        self.task_items.clear()
        self.task_widgets.clear()
        self._refresh_list()
        self._update_stats()

    def _refresh_list(self):
        n = len(self.task_items)
        self.file_count_label.configure(text=f"({n} 个文件)")
        if n == 0:
            self.empty_hint.pack(pady=60)
            self.summary_label.configure(text="")
        else:
            self.empty_hint.pack_forget()
        self._update_stats()

    def _get_output_path(self, input_path: str, source_dir: str = None, rel_path: str = None) -> str:
        output_dir = self.output_dir_var.get().strip()
        add_suffix = self.compressed_suffix_var.get()

        # 辅助函数：生成文件名（加或不加 _compressed 后缀）
        def output_filename(filename):
            if add_suffix:
                base, ext = os.path.splitext(filename)
                return f"{base}_compressed{ext}"
            return filename

        if output_dir:
            # 用户指定了输出目录
            if source_dir and rel_path and os.path.dirname(rel_path):
                # 文件夹模式：在输出目录下创建源文件夹名/子文件夹结构
                source_name = os.path.basename(source_dir)
                out = os.path.join(output_dir, source_name, rel_path)
                # 替换文件名为压缩后的文件名
                out_dir = os.path.dirname(out)
                out = os.path.join(out_dir, output_filename(os.path.basename(out)))
                os.makedirs(out_dir, exist_ok=True)
                return out
            else:
                return os.path.join(output_dir, output_filename(os.path.basename(input_path)))
        else:
            # 默认输出到源文件夹同级
            if source_dir and rel_path and os.path.dirname(rel_path):
                # 文件夹模式：在源文件夹旁边创建 "源文件夹名_compressed/" 保持结构
                source_name = os.path.basename(source_dir)
                parent_dir = os.path.dirname(source_dir)
                out_dir = os.path.join(parent_dir, f"{source_name}_compressed")
                # 替换文件名为压缩后的文件名
                out = os.path.join(out_dir, output_filename(os.path.basename(rel_path)))
                os.makedirs(os.path.dirname(out), exist_ok=True)
                return out
            else:
                base, ext = os.path.splitext(input_path)
                if add_suffix:
                    return f"{base}_compressed{ext}"
                return input_path

    # ------------------------------------------------------------------
    # 压缩控制
    # ------------------------------------------------------------------

    def _start_compress(self):
        if self.is_running:
            return
        if not self.task_items:
            messagebox.showinfo("提示", "请先添加 PDF 文件")
            return

        mode = self.mode_var.get()
        if mode == "target_size":
            try:
                raw = self.target_size_var.get().strip().upper()
                # 去掉用户可能自带的后缀（如 "20MB"、"20mb"）
                for suffix in ("GB", "MB", "KB", "G", "M", "K", "B"):
                    if raw.endswith(suffix):
                        raw = raw[:-len(suffix)].strip()
                size_val = float(raw)
                if size_val <= 0:
                    raise ValueError
                unit = self.target_unit_var.get()
                self._target_bytes = parse_size(f"{size_val}{unit}")
            except Exception:
                messagebox.showerror("错误", "请输入有效的目标大小")
                return
        else:
            self._target_bytes = None
            self._quality = self.quality_var.get()

        self.is_running = True
        self.cancel_flag = False
        self.total_original = 0
        self.total_compressed = 0

        self.start_btn.pack_forget()
        self.cancel_btn.pack(side="left", padx=(8, 0))

        self.add_file_btn.configure(state="disabled")
        self.add_dir_btn.configure(state="disabled")
        self.clear_all_btn.configure(state="disabled")

        for item in self.task_items:
            item["status"] = "等待中"
            item["progress"] = 0
            item["result_text"] = ""
            self.root.after(0, lambda i=item: i["status_label"].configure(text="等待中", text_color=Theme.TEXT3))
            self.root.after(0, lambda i=item: i["progress_bar"].set(0))
            self.root.after(0, lambda i=item: i["result_label"].configure(text=""))

        thread = threading.Thread(target=self._compress_worker, daemon=True)
        thread.start()

    def _cancel(self):
        self.cancel_flag = True
        self.cancel_btn.configure(state="disabled", text="取消中...")
        for item in self.task_items:
            if item["status"] == "等待中":
                item["status"] = "已取消"
                self.root.after(0, lambda i=item: i["status_label"].configure(text="已取消", text_color=Theme.WARNING))

    def _compress_worker(self):
        """后台压缩工作线程"""
        total = len(self.task_items)

        for idx, item in enumerate(self.task_items):
            if self.cancel_flag:
                break

            self.root.after(0, lambda i=item: i["status_label"].configure(text="处理中...", text_color=Theme.WARNING))
            self.root.after(0, lambda i=item: i["progress_bar"].set(0.1))

            try:
                output_path = self._get_output_path(
                    item["file_path"],
                    source_dir=item.get("source_dir"),
                    rel_path=item.get("rel_path"),
                )

                if os.path.exists(output_path) and not self.overwrite_var.get():
                    self.root.after(0, lambda i=item: i["result_label"].configure(text="已跳过（文件已存在）", text_color=Theme.WARNING))
                    self.root.after(0, lambda i=item: i["status_label"].configure(text="已跳过", text_color=Theme.WARNING))
                    self.root.after(0, lambda i=item: i["progress_bar"].set(1.0))
                    continue

                original_size = os.path.getsize(item["file_path"])
                self.total_original += original_size

                self.root.after(0, lambda i=item: i["progress_bar"].set(0.3))

                start_time = time.time()

                if self._target_bytes is not None:
                    # 并行探测的进度回调
                    def make_progress_cb(it):
                        def cb(stage, progress):
                            if stage == "probe":
                                p = 0.1 + progress * 0.4  # 0.1 ~ 0.5
                            elif stage == "refine":
                                p = 0.5 + progress * 0.3  # 0.5 ~ 0.8
                            else:
                                p = 0.8 + progress * 0.2  # 0.8 ~ 1.0
                            self.root.after(0, lambda i=it, pp=p: i["progress_bar"].set(pp))
                        return cb

                    workers = min(multiprocessing.cpu_count(), 6)
                    stats = compress_pdf_to_target(
                        item["file_path"], output_path, self._target_bytes,
                        workers=workers,
                        progress_callback=make_progress_cb(item)
                    )
                else:
                    stats = compress_pdf(item["file_path"], output_path, quality=self._quality)

                if not isinstance(stats, dict):
                    raise RuntimeError("压缩失败：未生成有效结果")

                elapsed = time.time() - start_time
                compressed_size = stats.get("compressed_file_size",
                                            os.path.getsize(output_path) if os.path.exists(output_path) else original_size)
                self.total_compressed += compressed_size
                self.root.after(0, self._update_stats)

                ratio = (1 - compressed_size / original_size) * 100 if original_size > 0 else 0
                skipped = stats.get("skipped", False)
                target_missed = self._target_bytes is not None and not stats.get("target_met", False)

                self.root.after(0, lambda i=item: i["progress_bar"].set(0.9))

                if skipped:
                    self.root.after(0, lambda i=item: i["result_label"].configure(text="无需压缩", text_color=Theme.TEXT2))
                    self.root.after(0, lambda i=item: i["status_label"].configure(text="跳过", text_color=Theme.TEXT2))
                elif target_missed:
                    result_text = f"{format_size(compressed_size)}  未达到目标  {elapsed:.1f}s"
                    self.root.after(0, lambda i=item, t=result_text: i["result_label"].configure(text=t, text_color=Theme.WARNING))
                    self.root.after(0, lambda i=item: i["status_label"].configure(text="已尽量压缩", text_color=Theme.WARNING))
                else:
                    img_info = f"{stats['images_compressed']}/{stats['images_found']} 图像"
                    result_text = f"{format_size(compressed_size)}  ↓{ratio:.1f}%  {elapsed:.1f}s"
                    self.root.after(0, lambda i=item, t=result_text: i["result_label"].configure(text=t, text_color=Theme.SUCCESS))
                    self.root.after(0, lambda i=item, t=img_info: i["status_label"].configure(text=t, text_color=Theme.TEXT2))

                self.root.after(0, lambda i=item: i["progress_bar"].set(1.0))

                total_ratio = (1 - self.total_compressed / self.total_original) * 100 if self.total_original > 0 else 0
                summary = f"已处理 {idx + 1}/{total}  |  {format_size(self.total_original)} → {format_size(self.total_compressed)}  (↓{total_ratio:.1f}%)"
                self.root.after(0, lambda s=summary: self.summary_label.configure(text=s))

            except Exception as e:
                error_msg = str(e)[:60]
                self.root.after(0, lambda i=item: i["status_label"].configure(text="失败", text_color=Theme.ERROR))
                self.root.after(0, lambda i=item, m=error_msg: i["result_label"].configure(text=f"错误: {m}", text_color=Theme.ERROR))
                self.root.after(0, lambda i=item: i["progress_bar"].set(1.0))

        self.root.after(0, self._on_finish)

    def _on_finish(self):
        self.is_running = False

        self.cancel_btn.pack_forget()
        self.cancel_btn.configure(text="取消", state="normal")
        self.start_btn.pack(side="left", padx=(8, 0))

        self.add_file_btn.configure(state="normal")
        self.add_dir_btn.configure(state="normal")
        self.clear_all_btn.configure(state="normal")

        if self.cancel_flag:
            self.summary_label.configure(
                text=f"已取消  |  {format_size(self.total_original)} → {format_size(self.total_compressed)}",
                text_color=Theme.WARNING
            )
        else:
            if self.total_original > 0:
                ratio = (1 - self.total_compressed / self.total_original) * 100
                self.summary_label.configure(
                    text=f"✓ 全部完成  |  {format_size(self.total_original)} → {format_size(self.total_compressed)}  (↓{ratio:.1f}%)",
                    text_color=Theme.SUCCESS
                )

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------

    def run(self):
        """启动主循环"""
        self.root.update_idletasks()
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 2 - 30
        self.root.geometry(f"+{x}+{y}")

        self.root.mainloop()


# ======================================================================
# 入口
# ======================================================================

if __name__ == "__main__":
    app = PDFCompressorGUI()
    app.run()
