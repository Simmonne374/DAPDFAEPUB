## 2024-05-18 - Better visibility for long operations
**Learning:** Text-only terminal logs in Gradio applications are often insufficient for conveying critical and terminal state information (like success, failure, or missing prerequisites) because they require users to constantly monitor the scrolling area.
**Action:** Always use native UI toast notifications (`gr.Info`, `gr.Warning`, `gr.Error`) in conjunction with terminal logs to ensure critical events are immediately visible to the user regardless of where they are looking on the page.
## 2024-05-24 - Info Text vs Placeholder
**Learning:** Important instructions (like 'leave blank to save in default location') should go in the `info` property, not `placeholder`, because placeholders disappear when the user starts typing, causing them to forget the instruction.
**Action:** Always prefer `info` for persistent field-level instructions, keeping `placeholder` strictly for formatting examples.
## 2026-08-20 - Placeholder text vs Persistent Instructions
**Learning:** Placeholders disappear when the user starts typing, making them unsuitable for persistent field-level instructions. The `info` property in Gradio is better suited for persistent instructions, while `placeholder` should strictly be used for formatting examples. Repeating the label in the placeholder is redundant.
**Action:** Changed the `placeholder` for `title` and `author` inputs to provide formatting examples (e.g., "Es: Il Nome della Rosa") instead of repeating the field labels.
## 2024-05-25 - Disabling actions dependent on input
**Learning:** Actions that operate on user-provided inputs (such as clearing a cache for a specific uploaded file) can cause errors or confusion if triggered before the input is provided. The `gr.Button(interactive=False)` combined with dynamic state updates on `change` events ensures users understand when an action is available.
**Action:** Always disable buttons that require a specific input (like a selected file) by default and enable them dynamically when the prerequisite input is provided using `gr.update(interactive=is_active)`.
## 2024-05-26 - Dynamic text for disabled buttons
**Learning:** While disabling buttons dependent on input (like `gr.Button(interactive=False)`) prevents premature clicks, the default label (e.g., "Converti in EPUB") doesn't explain *why* the button is disabled. Changing the text to state the required action (e.g., "Seleziona un PDF per convertire") significantly reduces user confusion and clearly communicates the prerequisite.
**Action:** Always provide dynamic text for disabled buttons that clarifies the required action, and revert to the primary action text when the button becomes interactive using `gr.update(value=...)`.
