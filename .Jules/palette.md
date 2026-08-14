## 2024-05-18 - Better visibility for long operations
**Learning:** Text-only terminal logs in Gradio applications are often insufficient for conveying critical and terminal state information (like success, failure, or missing prerequisites) because they require users to constantly monitor the scrolling area.
**Action:** Always use native UI toast notifications (`gr.Info`, `gr.Warning`, `gr.Error`) in conjunction with terminal logs to ensure critical events are immediately visible to the user regardless of where they are looking on the page.
## 2024-05-24 - Info Text vs Placeholder
**Learning:** Important instructions (like 'leave blank to save in default location') should go in the `info` property, not `placeholder`, because placeholders disappear when the user starts typing, causing them to forget the instruction.
**Action:** Always prefer `info` for persistent field-level instructions, keeping `placeholder` strictly for formatting examples.
