## 2024-05-18 - [Gradio UI] Explicit Guidance on Optional Fields

**Learning:** Non-technical users using Gradio UI interfaces are often unsure about the backend's default behavior for optional fields (like 'Book Title' falling back to the filename if left empty). This leads to confusion or repetitive work. Adding descriptive helper texts explicitly outlining default fallbacks is crucial for easing cognitive load.

**Action:** Whenever implementing optional configuration inputs in a Gradio block (or equivalent), ensure an `info` description explicitly communicates what the default behavior will be if the user leaves the field untouched.
