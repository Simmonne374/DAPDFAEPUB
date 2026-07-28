## 2024-05-18 - [Gradio UI] Explicit Guidance on Optional Fields

**Learning:** Non-technical users using Gradio UI interfaces are often unsure about the backend's default behavior for optional fields (like 'Book Title' falling back to the filename if left empty). This leads to confusion or repetitive work. Adding descriptive helper texts explicitly outlining default fallbacks is crucial for easing cognitive load.

**Action:** Whenever implementing optional configuration inputs in a Gradio block (or equivalent), ensure an `info` description explicitly communicates what the default behavior will be if the user leaves the field untouched.
## 2024-05-18 - Disable convert button until file is selected
**Learning:** Having a main action button enabled when the required input (PDF file) is missing causes a confusing experience (and potentially an immediate error upon clicking). Preventing invalid states by disabling the button until a file is selected provides clearer feedback and prevents errors.
**Action:** Always make sure primary action buttons are only interactive when all required inputs for the action are provided by the user. Use `.change` events in Gradio to check the file input state dynamically.
