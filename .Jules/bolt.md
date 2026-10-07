# Bolt's Journal - Critical Learnings

## 2026-05-14 - Fast-path Substring Membership Guarding on High-Frequency Token Cleaners
**Learning:** In inner document processing loops and page-level pipeline passes, running regex searches (`_DET_PATTERN.finditer`) or `str.replace` sequences without verifying tag membership incurs unnecessary C-extension scanning overhead on plain text pages. Adding cheap C-level substring checks (`if "<|det|>" in page_text:` and `if "image" in text:`) cuts execution time by up to ~50% on pages without layout tags.
**Action:** Guard regex iterations and token stripping passes with fast substring checks (`in`) when tags are optional per page.

## 2026-04-20 - Substring Guard Branching and Direct Tuple Unpacking for Token Parsing
**Learning:** Evaluated regex searches and generator comprehensions when parsing token strings (like `<|det|>` / `<|bbox|>` tags) add non-trivial overhead in inner parsing loops. Guarding regex calls with fast `in` string checks and directly unpacking match group tuples yields ~20-25% faster parsing without sacrificing code clarity.
**Action:** Use fast substring branching and direct tuple unpacking for frequently invoked string token parsers.

## 2026-04-12 - Direct PyMuPDF Resolution Matrix Rendering & PNG Filter Search Removal
**Learning:** Downsampling 300 DPI rendered pages in PIL is 9x slower than rendering directly at target model resolution (`target_size / max(w, h)`) in PyMuPDF's C rasterizer. Furthermore, passing `optimize=True` when saving intermediate model input or crop PNGs adds ~3.5x CPU encoding overhead for no practical benefit on temporary files.
**Action:** Render multi-resolution PDF pages directly via PyMuPDF matrix scaling in RAM with `Image.frombytes`, and omit `optimize=True` on non-final intermediate PNG saves.

## 2026-03-30 - Parallelizing Image Optimization with ThreadPoolExecutor
**Learning:** Heavy image filtering and WebP compression in PIL/Pillow and libwebp release Python's GIL. Using `ThreadPoolExecutor` in batch image processing functions yields ~3x speedups across CPU cores without incurring process serialization overhead.
**Action:** Use `ThreadPoolExecutor` for batch PIL image processing and WebP conversion tasks.

## 2025-09-09 - Fast-path Substring Guarding before Regex Operations
**Learning:** Running regex substitutions on large Markdown and XHTML strings overheads execution even when target tags (such as `<img` or `<|det|>`) are completely absent. Hoisting pre-compiled regex objects to module level combined with cheap string membership guards (`if "<img" in html:`) yields significant execution speedups (35%+ on non-image HTML fragments).
**Action:** Always place quick string membership checks before invoking regex searches or substitutions when target tokens are sparse or optional.
