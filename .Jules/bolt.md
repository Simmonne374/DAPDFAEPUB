## 2025-09-09 - Fast-path Substring Guarding before Regex Operations
**Learning:** Running regex substitutions on large Markdown and XHTML strings overheads execution even when target tags (such as `<img` or `<|det|>`) are completely absent. Hoisting pre-compiled regex objects to module level combined with cheap string membership guards (`if "<img" in html:`) yields significant execution speedups (35%+ on non-image HTML fragments).
**Action:** Always place quick string membership checks before invoking regex searches or substitutions when target tokens are sparse or optional.
