# Bolt's Journal - Critical Learnings

## 2026-04-12 - Direct PyMuPDF Resolution Matrix Rendering & PNG Filter Search Removal
**Learning:** Downsampling 300 DPI rendered pages in PIL is 9x slower than rendering directly at target model resolution (`target_size / max(w, h)`) in PyMuPDF's C rasterizer. Furthermore, passing `optimize=True` when saving intermediate model input or crop PNGs adds ~3.5x CPU encoding overhead for no practical benefit on temporary files.
**Action:** Render multi-resolution PDF pages directly via PyMuPDF matrix scaling in RAM with `Image.frombytes`, and omit `optimize=True` on non-final intermediate PNG saves.

## 2026-03-30 - Parallelizing Image Optimization with ThreadPoolExecutor
**Learning:** Heavy image filtering and WebP compression in PIL/Pillow and libwebp release Python's GIL. Using `ThreadPoolExecutor` in batch image processing functions yields ~3x speedups across CPU cores without incurring process serialization overhead.
**Action:** Use `ThreadPoolExecutor` for batch PIL image processing and WebP conversion tasks.

## 2025-09-09 - Fast-path Substring Guarding before Regex Operations
**Learning:** Running regex substitutions on large Markdown and XHTML strings overheads execution even when target tags (such as `<img` or `<|det|>`) are completely absent. Hoisting pre-compiled regex objects to module level combined with cheap string membership guards (`if "<img" in html:`) yields significant execution speedups (35%+ on non-image HTML fragments).
**Action:** Always place quick string membership checks before invoking regex searches or substitutions when target tokens are sparse or optional.
