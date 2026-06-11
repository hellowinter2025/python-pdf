"""
PDF 压缩工具 —— 智能图像压缩，保留矢量内容

功能：
  - 仅对 PDF 中嵌入的图像进行有损压缩（JPEG 重编码），文字、矢量图形、排版保持不变
  - 自动检测 FlateDecode/LZWDecode 的原始图像，解码后以 JPEG 重新编码
  - 已是 JPEG/JPX 的图像直接重新编码到目标质量
  - 支持指定目标大小上限，自动调整图像质量使输出文件满足要求
  - 支持批量压缩（传入文件或目录）

依赖：
  pip install pikepdf Pillow

用法示例：
  # 压缩到 10MB 以下
  py pdf_compress.py input.pdf -s 10M

  # 压缩到 5MB 以下，输出到指定路径
  py pdf_compress.py input.pdf -s 5M -o output.pdf

  # 批量压缩目录下所有 PDF
  py pdf_compress.py ./PDF/ -s 8M

  # 不指定大小，直接以质量 75 压缩
  py pdf_compress.py input.pdf -q 75
"""

import argparse
import io
import math
import os
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pikepdf
from PIL import Image

# 默认并行进程数（自动检测 CPU 核数，留 2 核给系统）
import multiprocessing
_DEFAULT_WORKERS = max(2, multiprocessing.cpu_count() - 2)

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def parse_size(size_str: str) -> int:
    """将人类可读的大小字符串转为字节数，如 '10M' / '10MB' -> 10485760"""
    size_str = size_str.strip().upper()
    # 去掉 B 后缀（支持 MB/GB/KB 和 M/G/K 两种写法）
    if size_str.endswith("B"):
        size_str = size_str[:-1]
    if size_str.endswith("G"):
        return int(float(size_str[:-1]) * 1024 ** 3)
    if size_str.endswith("M"):
        return int(float(size_str[:-1]) * 1024 ** 2)
    if size_str.endswith("K"):
        return int(float(size_str[:-1]) * 1024)
    return int(size_str)


def format_size(size_bytes: int) -> str:
    """字节数 -> 人类可读"""
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / 1024 ** 3:.2f} GB"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / 1024 ** 2:.2f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} KB"
    return f"{size_bytes} B"


def get_filter_name(obj) -> str:
    """安全获取对象的 Filter 名称，支持各种格式"""
    try:
        if "/Filter" not in obj:
            return ""
        filt = obj["/Filter"]
        # pikepdf.Name 对象：str() 返回 "/FlateDecode" 等
        # pikepdf.Array 对象：迭代取第一个
        # 注意：pikepdf.Name 有 __iter__ 但实际迭代为空，需先判断是否为 dict/list
        if isinstance(filt, (list, dict)):
            # 数组情况，取第一个
            filt = filt[0] if len(filt) > 0 else ""
        return str(filt).strip()
    except Exception:
        return ""


def is_skip_filter(filter_name: str) -> bool:
    """判断是否为应该跳过的压缩过滤器（非图像压缩过滤器）"""
    skip = {
        "/ASCIIHexDecode", "/ASCII85Decode",
        "/RunLengthDecode", "/CCITTFaxDecode",
        "/JBIG2Decode", "/Crypt",
    }
    return filter_name in skip


def ensure_rgb(img: Image.Image) -> Image.Image:
    """确保图像为 RGB 模式（适合 JPEG 编码）"""
    if img.mode == "RGB":
        return img
    if img.mode == "CMYK":
        # CMYK -> RGB，使用 Pillow 默认色彩配置
        return img.convert("RGB")
    if img.mode in ("RGBA", "LA"):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    if img.mode == "P":
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        return bg
    # 其他模式（如 L, 1 等）直接转 RGB
    return img.convert("RGB")


# ---------------------------------------------------------------------------
# 图像压缩核心
# ---------------------------------------------------------------------------

def compress_image_stream_with_size(image_bytes: bytes, quality: int = 75, max_dim: int = None):
    """
    将图像字节数据以指定 JPEG 质量重新编码并返回新字节。
    返回 None 表示跳过（不压缩）。
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Exception:
        return None, None, None

    # 极小图像（可能是图标/装饰线条）跳过
    if img.width < 30 or img.height < 30:
        return None, None, None

    scale = 1.0
    if max_dim and max(img.width, img.height) > max_dim:
        scale = max_dim / max(img.width, img.height)
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))), Image.LANCZOS)

    img = ensure_rgb(img)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    result = buf.getvalue()

    # 压缩后更大，返回 None 保持原数据
    if len(result) >= len(image_bytes):
        return None, None, None

    return result, img.width, img.height


def compress_image_stream(image_bytes: bytes, quality: int = 75) -> bytes:
    """
    将图像字节数据以指定 JPEG 质量重新编码并返回新字节。
    返回 None 表示跳过（不压缩）。
    """
    result, _, _ = compress_image_stream_with_size(image_bytes, quality)
    return result




def compress_pdf(input_path: str, output_path: str, quality: int = 75, max_image_dim: int = None) -> dict:
    """
    以固定 quality 压缩 PDF 中的所有嵌入图像。
    返回统计信息 dict。
    """
    pdf = pikepdf.Pdf.open(input_path)
    stats = {
        "images_found": 0, "images_compressed": 0,
        "original_bytes": 0, "compressed_bytes": 0,
    }

    # 收集所有需要处理的图像对象
    image_candidates = []
    for i, obj in enumerate(pdf.objects):
        try:
            if not isinstance(obj, pikepdf.Stream):
                continue
            if "/Subtype" not in obj or str(obj["/Subtype"]) != "/Image":
                continue
            if "/Width" not in obj or "/Height" not in obj:
                continue
            image_candidates.append(obj)
        except Exception:
            continue

    stats["images_found"] = len(image_candidates)

    # 收集所有图像的原始存储大小（在修改任何对象之前一次性获取）
    image_info = []
    for obj in image_candidates:
        try:
            filter_name = get_filter_name(obj)
            width = int(obj["/Width"])
            height = int(obj["/Height"])
            bpc = int(obj.get("/BitsPerComponent", 8))
            cs_str = str(obj.get("/ColorSpace", "")).strip()

            # 跳过非压缩过滤器
            if filter_name and is_skip_filter(filter_name):
                continue

            # 获取原始存储大小
            try:
                raw_size = len(obj.read_raw_bytes())
            except Exception:
                raw_size = 0

            stats["original_bytes"] += raw_size
            image_info.append((obj, filter_name, width, height, bpc, cs_str, raw_size))
        except Exception:
            continue

    # 逐个处理图像
    for obj, filter_name, width, height, bpc, cs_str, raw_size in image_info:
        try:
            # ===== JPEG/JPX 图像 =====
            if "DCTDecode" in filter_name or "JPXDecode" in filter_name:
                try:
                    image_bytes = obj.read_raw_bytes()
                    jpeg_data, new_width, new_height = compress_image_stream_with_size(image_bytes, quality, max_image_dim)
                    if jpeg_data is not None and len(jpeg_data) < raw_size:
                        obj.write(jpeg_data, filter=pikepdf.Name.DCTDecode)
                        if new_width and new_height:
                            obj["/Width"] = new_width
                            obj["/Height"] = new_height
                        obj["/ColorSpace"] = pikepdf.Name.DeviceRGB
                        obj["/BitsPerComponent"] = 8
                        for key in ["/DecodeParms", "/ColorTransform"]:
                            if key in obj:
                                del obj[key]
                        stats["images_compressed"] += 1
                        stats["compressed_bytes"] += len(jpeg_data)
                except Exception:
                    pass
                continue

            # ===== FlateDecode / LZWDecode 原始像素数据 =====
            if "FlateDecode" not in filter_name and "LZWDecode" not in filter_name:
                continue

            # 跳过太小的图像
            if width < 30 or height < 30:
                continue

            # 跳过大位深
            if bpc not in (1, 2, 4, 8):
                continue

            # 确定通道数和 PIL 模式
            if "CMYK" in cs_str:
                n_comp = 4
                pil_mode = "CMYK"
            elif "RGB" in cs_str or "Lab" in cs_str:
                n_comp = 3
                pil_mode = "RGB"
            elif "Gray" in cs_str:
                n_comp = 1
                pil_mode = "L"
            else:
                n_comp = 3
                pil_mode = "RGB"

            # 计算期望大小
            if bpc == 1:
                expected = (width * height * n_comp + 7) // 8
            else:
                expected = width * height * n_comp * (bpc // 8)

            # 解压
            try:
                decoded = obj.read_bytes()
            except Exception:
                continue

            if len(decoded) < expected * 0.8:
                continue

            # 构建 PIL 图像
            try:
                img = Image.frombytes(pil_mode, (width, height), decoded[:expected])
            except Exception:
                continue

            # 缩放大图
            scale = 1.0
            max_dim = max_image_dim or 2400
            if max(width, height) > max_dim:
                scale = max_dim / max(width, height)
                img = img.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

            # 转 RGB + JPEG 编码
            img = ensure_rgb(img)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality, optimize=True)
            jpeg_data = buf.getvalue()

            # 比较
            if raw_size > 0 and len(jpeg_data) < raw_size:
                try:
                    obj.write(jpeg_data, filter=pikepdf.Name.DCTDecode)
                    if scale < 1.0:
                        obj["/Width"] = int(width * scale)
                        obj["/Height"] = int(height * scale)
                    obj["/ColorSpace"] = pikepdf.Name.DeviceRGB
                    obj["/BitsPerComponent"] = 8
                    for key in ["/DecodeParms", "/Decode"]:
                        if key in obj:
                            del obj[key]
                    stats["images_compressed"] += 1
                    stats["compressed_bytes"] += len(jpeg_data)
                except Exception:
                    pass
        except Exception:
            continue

    pdf.save(
        output_path,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
        recompress_flate=True,
    )
    pdf.close()

    stats["original_file_size"] = os.path.getsize(input_path)
    stats["compressed_file_size"] = os.path.getsize(output_path)

    return stats


def _compress_for_quality(args):
    """供多进程调用的包装函数：(input_path, quality) -> (quality, output_bytes, stats)"""
    if len(args) == 4:
        input_path, quality, tmp_dir, max_image_dim = args
    else:
        input_path, quality, tmp_dir = args
        max_image_dim = None
    try:
        suffix = f"_d{max_image_dim}" if max_image_dim else ""
        tmp_out = os.path.join(tmp_dir, f"probe_q{quality}{suffix}.pdf")
        stats = compress_pdf(input_path, tmp_out, quality=quality, max_image_dim=max_image_dim)
        sz = stats["compressed_file_size"]
        return (quality, sz, stats, tmp_out)
    except Exception as e:
        return (quality, -1, {"error": str(e)}, None)


def _mark_target_result(stats: dict, target_bytes: int) -> dict:
    """Add target-size metadata while keeping older callers compatible."""
    final_size = stats.get("compressed_file_size", 0)
    stats["target_bytes"] = target_bytes
    stats["target_met"] = final_size <= target_bytes if final_size else False
    return stats


def _copy_if_different(src: str, dst: str):
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)


def _fit_exp_model(quality_size_pairs):
    """
    用 (quality, file_size) 数据点拟合指数衰减模型：
        size(q) ≈ A · exp(-B · q) + C
    返回 (A, B, C, max_error_ratio) 或 None（拟合失败时）
    """
    if len(quality_size_pairs) < 3:
        return None

    # 按 quality 排序
    pts = sorted(quality_size_pairs)
    n = len(pts)

    # 用 scipy 的 curve_fit 最理想，但为了零额外依赖，这里用最小二乘手动拟合
    # 取最大的 3 个点和最小的 3 个点来估算参数
    # C ≈ size(95)（质量最高时接近下限）
    # A + C ≈ size(5)（质量最低时接近上限）
    # 通过中点估算 B

    # 简化：取两端 + 中间
    if n >= 5:
        sample_indices = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    elif n >= 3:
        sample_indices = [0, n // 2, n - 1]
    else:
        return None

    sample = [pts[i] for i in sample_indices]
    q_lo, s_lo = sample[0]    # 低质量 → 大文件
    q_mid, s_mid = sample[len(sample) // 2]
    q_hi, s_hi = sample[-1]   # 高质量 → 小文件（但可能不是最小的）

    # C 估算：高质量端的极限大小
    C = s_hi * 0.95
    if C < 0:
        C = 0

    # A 估算
    A = s_lo - C
    if A <= 0:
        return None

    # B 估算：用中间点
    # s_mid ≈ A * exp(-B * q_mid) + C
    # exp(-B * q_mid) ≈ (s_mid - C) / A
    ratio = (s_mid - C) / A
    if ratio <= 0 or ratio >= 1:
        return None
    try:
        B = -math.log(ratio) / q_mid
    except (ValueError, ZeroDivisionError):
        return None

    # 验证拟合精度
    max_error = 0
    for q, s in quality_size_pairs:
        predicted = A * math.exp(-B * q) + C
        if s > 0:
            err = abs(predicted - s) / s
            max_error = max(max_error, err)

    return (A, B, C, max_error)


def _predict_quality(model, target_bytes):
    """
    用拟合模型预测达到目标大小所需的 quality。
    A * exp(-B * q) + C = target
    q = -ln((target - C) / A) / B
    """
    A, B, C, max_error = model
    if target_bytes <= C:
        return 95
    if target_bytes >= A + C:
        return 5
    try:
        ratio = (target_bytes - C) / A
        if ratio <= 0 or ratio >= 1:
            return None
        q = -math.log(ratio) / B
        return max(5, min(95, round(q)))
    except (ValueError, ZeroDivisionError):
        return None


def compress_pdf_to_target(input_path: str, output_path: str, target_bytes: int,
                            workers: int = _DEFAULT_WORKERS,
                            progress_callback=None) -> dict:
    """
    并行探测 + 公式拟合，快速找到最优 JPEG quality 使输出 PDF <= target_bytes。

    策略：
      1. 第一轮：用 ProcessPoolExecutor 并行压缩 5 个探测质量点
      2. 用实测数据拟合 size(q) = A·exp(-B·q) + C 经验公式
      3. 用公式反算最优 quality，再做 1-2 次微调验证
      4. 最多总耗时 ≈ 1轮并行 + 1次微调，而非串行二分的 7 轮
    """
    original_size = os.path.getsize(input_path)

    if original_size <= target_bytes:
        _copy_if_different(input_path, output_path)
        return _mark_target_result({
            "images_found": 0, "images_compressed": 0,
            "original_bytes": 0, "compressed_bytes": 0,
            "original_file_size": original_size,
            "compressed_file_size": original_size,
            "skipped": True,
        }, target_bytes)

    # 创建临时目录存放探测文件
    tmp_dir = output_path + ".tmp_probe"
    os.makedirs(tmp_dir, exist_ok=True)

    best_quality = 95
    best_stats = None
    best_size = original_size
    best_tmp = None  # 保留最优结果的临时文件路径

    try:
        # ===== 第一轮：并行探测 5 个质量点 =====
        probe_qualities = [10, 30, 50, 70, 90]
        if progress_callback:
            progress_callback("probe", 0.1)

        # 如果只有 1-2 核，退化为串行
        actual_workers = min(workers, len(probe_qualities), 4)

        results = []  # (quality, size, stats, tmp_path)
        with ProcessPoolExecutor(max_workers=actual_workers) as pool:
            futures = {
                pool.submit(_compress_for_quality, (input_path, q, tmp_dir)): q
                for q in probe_qualities
            }
            for future in as_completed(futures):
                try:
                    q, sz, stats, tmp_path = future.result()
                    if sz > 0:
                        results.append((q, sz, stats, tmp_path))
                        if sz <= target_bytes and sz < best_size:
                            best_quality = q
                            best_stats = stats
                            best_size = sz
                            best_tmp = tmp_path
                except Exception:
                    pass

        if progress_callback:
            progress_callback("probe", 0.5)

        # 排序
        results.sort(key=lambda x: x[0])

        # 如果探测中已找到满足条件的，尝试用公式找更高质量
        if best_stats is not None:
            # ===== 公式拟合 =====
            quality_size_pairs = [(q, sz) for q, sz, _, _ in results if sz > 0]
            model = _fit_exp_model(quality_size_pairs)

            if model is not None:
                predicted_q = _predict_quality(model, target_bytes)
                if predicted_q is not None and predicted_q > best_quality:
                    # 尝试预测的质量（可能得到更高的质量）
                    if progress_callback:
                        progress_callback("refine", 0.7)

                    try:
                        tmp_out = os.path.join(tmp_dir, f"probe_q{predicted_q}.pdf")
                        stats = compress_pdf(input_path, tmp_out, quality=predicted_q)
                        sz = stats["compressed_file_size"]
                        if sz <= target_bytes:
                            # 更新最优
                            if best_tmp and os.path.exists(best_tmp) and best_tmp != tmp_out:
                                pass  # 保留旧的不删，最后统一清理
                            best_quality = predicted_q
                            best_stats = stats
                            best_size = sz
                            best_tmp = tmp_out
                        # 无论是否满足，都加入数据点做二次拟合
                        results.append((predicted_q, sz, stats, tmp_out))
                        quality_size_pairs.append((predicted_q, sz))

                        # 二次拟合微调
                        model2 = _fit_exp_model(quality_size_pairs)
                        if model2 is not None:
                            predicted_q2 = _predict_quality(model2, target_bytes)
                            if (predicted_q2 is not None
                                and predicted_q2 > best_quality
                                and predicted_q2 != predicted_q):
                                tmp_out2 = os.path.join(tmp_dir, f"probe_q{predicted_q2}.pdf")
                                stats2 = compress_pdf(input_path, tmp_out2, quality=predicted_q2)
                                sz2 = stats2["compressed_file_size"]
                                if sz2 <= target_bytes:
                                    best_quality = predicted_q2
                                    best_stats = stats2
                                    best_size = sz2
                                    best_tmp = tmp_out2

                    except Exception:
                        pass

            if progress_callback:
                progress_callback("done", 1.0)

        else:
            # 所有探测都超目标，选最小的
            for q, sz, stats, tmp_path in results:
                if 0 < sz < best_size:
                    best_quality = q
                    best_stats = stats
                    best_size = sz
                    best_tmp = tmp_path

            # 还超目标的话，尝试 q=5
            if best_size > target_bytes:
                try:
                    tmp_out = os.path.join(tmp_dir, "probe_q5.pdf")
                    stats = compress_pdf(input_path, tmp_out, quality=5)
                    sz = stats["compressed_file_size"]
                    if sz < best_size:
                        best_quality = 5
                        best_stats = stats
                        best_size = sz
                        best_tmp = tmp_out
                except Exception:
                    pass

            if best_size > target_bytes:
                downsample_plan = [
                    (2200, 75), (2000, 70), (1800, 65), (1600, 60),
                    (1400, 55), (1200, 50), (1000, 45), (850, 40),
                ]
                for max_dim, q in downsample_plan:
                    try:
                        tmp_out = os.path.join(tmp_dir, f"probe_d{max_dim}_q{q}.pdf")
                        stats = compress_pdf(input_path, tmp_out, quality=q, max_image_dim=max_dim)
                        stats["max_image_dim"] = max_dim
                        sz = stats["compressed_file_size"]
                        if sz < best_size:
                            best_quality = q
                            best_stats = stats
                            best_size = sz
                            best_tmp = tmp_out
                        elif os.path.exists(tmp_out):
                            os.remove(tmp_out)
                        if sz <= target_bytes:
                            break
                    except Exception:
                        pass

            if progress_callback:
                progress_callback("done", 1.0)

        if best_stats is None:
            tmp_out = os.path.join(tmp_dir, "probe_q5.pdf")
            stats = compress_pdf(input_path, tmp_out, quality=5)
            sz = stats["compressed_file_size"]
            if sz < original_size:
                best_quality = 5
                best_stats = stats
                best_size = sz
                best_tmp = tmp_out
            else:
                best_stats = stats
                best_stats["compressed_file_size"] = original_size
                best_stats["no_smaller_output"] = True
                _copy_if_different(input_path, output_path)

    finally:
        # 清理临时目录（保留 best_tmp）
        try:
            for f in os.listdir(tmp_dir):
                fp = os.path.join(tmp_dir, f)
                if fp != best_tmp and os.path.isfile(fp):
                    os.remove(fp)
            if best_tmp:
                # 移动最优结果到目标路径
                if os.path.exists(output_path):
                    os.remove(output_path)
                os.replace(best_tmp, output_path)
            os.rmdir(tmp_dir)
        except Exception:
            pass

    if best_stats is None:
        raise RuntimeError("压缩失败：未能生成输出文件")

    if os.path.exists(output_path):
        best_stats["compressed_file_size"] = os.path.getsize(output_path)
    best_stats["best_quality"] = best_quality
    return _mark_target_result(best_stats, target_bytes)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def process_file(input_path: str, output_path: str, target_size: int = None, quality: int = 75):
    """处理单个 PDF 文件"""
    input_path = os.path.abspath(input_path)

    if not os.path.isfile(input_path):
        print(f"  [!] 文件不存在: {input_path}")
        return

    original_size = os.path.getsize(input_path)
    print(f"  {os.path.basename(input_path)}  ({format_size(original_size)})")

    start = time.time()

    if target_size is not None:
        stats = compress_pdf_to_target(input_path, output_path, target_size)
    else:
        stats = compress_pdf(input_path, output_path, quality=quality)

    elapsed = time.time() - start
    final_size = stats.get("compressed_file_size", os.path.getsize(output_path) if os.path.exists(output_path) else original_size)
    ratio = (1 - final_size / original_size) * 100 if original_size > 0 else 0

    skipped = stats.get("skipped", False)

    if skipped:
        print(f"     >> 原文件已满足大小要求，已直接复制")
    else:
        print(f"     >> 压缩完成: {format_size(final_size)}  (减少 {ratio:.1f}%)")
        print(f"     >> 扫描图像: {stats['images_found']}  |  已压缩: {stats['images_compressed']}")
        img_saved = stats.get('original_bytes', 0) - stats.get('compressed_bytes', 0)
        if img_saved > 0:
            print(f"     >> 图像数据: {format_size(stats['original_bytes'])} -> {format_size(stats['compressed_bytes'])}")

    print(f"     >> 耗时: {elapsed:.1f}s")
    print()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="PDF 压缩工具 -- 智能图像压缩，保留矢量内容",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s input.pdf -s 10M              压缩到 10MB 以下
  %(prog)s input.pdf -s 5M -o out.pdf    压缩到 5MB 以下，指定输出路径
  %(prog)s ./folder/ -s 8M               批量压缩文件夹中所有 PDF
  %(prog)s input.pdf -q 70               以 JPEG 质量 70 压缩
        """,
    )
    parser.add_argument("input", help="输入 PDF 文件或目录路径")
    parser.add_argument("-o", "--output", help="输出文件路径（目录时指定输出目录）")
    parser.add_argument("-s", "--size", help="目标最大文件大小，如 10M, 500K, 1G")
    parser.add_argument("-q", "--quality", type=int, default=75, help="JPEG 压缩质量 (5-95)，默认 75")

    args = parser.parse_args()

    target_size = parse_size(args.size) if args.size else None
    input_path = os.path.abspath(args.input)

    if not os.path.exists(input_path):
        print(f"错误: 路径不存在: {input_path}")
        sys.exit(1)

    print("=" * 55)
    print("  PDF 压缩工具 -- 智能图像压缩，保留矢量内容")
    print("=" * 55)
    print()

    if os.path.isfile(input_path):
        if not input_path.lower().endswith(".pdf"):
            print("错误: 输入文件不是 PDF 格式")
            sys.exit(1)

        output_path = args.output
        if not output_path:
            base, ext = os.path.splitext(input_path)
            output_path = f"{base}_compressed{ext}"

        if target_size:
            print(f"目标大小: {format_size(target_size)}")
        else:
            print(f"压缩质量: JPEG {args.quality}")
        print()

        process_file(input_path, output_path, target_size, args.quality)

    elif os.path.isdir(input_path):
        pdfs = sorted(Path(input_path).glob("*.pdf"))
        if not pdfs:
            print(f"错误: 目录中没有找到 PDF 文件: {input_path}")
            sys.exit(1)

        output_dir = args.output if args.output else os.path.join(input_path, "compressed")
        os.makedirs(output_dir, exist_ok=True)

        print(f"找到 {len(pdfs)} 个 PDF 文件")
        if target_size:
            print(f"目标大小: {format_size(target_size)}")
        else:
            print(f"压缩质量: JPEG {args.quality}")
        print()

        total_orig = 0
        total_comp = 0

        for pdf_path in pdfs:
            output_path = os.path.join(output_dir, pdf_path.name)
            result = process_file(str(pdf_path), output_path, target_size, args.quality)

            total_orig += os.path.getsize(str(pdf_path))
            if result and not result.get("skipped", False):
                total_comp += result.get("compressed_file_size", os.path.getsize(output_path))

        if total_orig > 0 and total_comp > 0:
            print("=" * 55)
            print(f"汇总: {format_size(total_orig)} -> {format_size(total_comp)}  "
                  f"(减少 {(1 - total_comp / total_orig) * 100:.1f}%)")
            print(f"输出目录: {output_dir}")
            print("=" * 55)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
