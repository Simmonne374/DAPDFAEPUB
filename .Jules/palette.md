## 2024-05-18 - Better visibility for long operations
**Learning:** Text-only terminal logs in Gradio applications are often insufficient for conveying critical and terminal state information (like success, failure, or missing prerequisites) because they require users to constantly monitor the scrolling area.
**Action:** Always use native UI toast notifications (`gr.Info`, `gr.Warning`, `gr.Error`) in conjunction with terminal logs to ensure critical events are immediately visible to the user regardless of where they are looking on the page.
