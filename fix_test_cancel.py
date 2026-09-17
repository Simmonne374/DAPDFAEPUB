import re

with open("tests/test_cancel.py", "r", encoding="utf-8") as f:
    content = f.read()

# Replace SlowOCR class in test_cancel_preserves_checkpoint_completed_batches
old_slowocr = """    class SlowOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths, cancel_check=None):
            # Batch 0: emette "done" rapidamente, con un po' di delay
            yield "# B0 partial", "running"
            time.sleep(0.1)  # dà tempo al test di chiamare cancel()
            yield "# B0 partial", "running"
            yield "# B0 done", "done"
            # Batch 1: dovrebbe essere short-circuit dal cancel check
            yield "# B1 partial", "running"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text"""

new_slowocr = """    batch0_done = threading.Event()
    class SlowOCR:
        def __init__(self, cfg):
            pass

        def run_batch_iter(self, paths, cancel_check=None):
            yield "# B0 partial", "running"
            yield "# B0 partial", "running"
            yield "# B0 done", "done"
            batch0_done.set()
            time.sleep(0.5) # Give the cancel watcher time to kick in during the transition
            yield "# B1 partial", "running"

        @staticmethod
        def _strip_image_tokens(text: str) -> str:
            return text"""

content = content.replace(old_slowocr, new_slowocr)

old_simple_cancel = """    # Helper più diretto: attiviamo cancel subito dopo un piccolo delay,
    # assicurandoci che batch 0 sia già stato processato (visto che il mock
    # fa time.sleep 0.1 prima di done).
    def simple_cancel():
        time.sleep(0.15)  # > il delay di 0.1 nel mock → cancel dopo "done" del batch 0
        pipeline.cancel()
    t = threading.Thread(target=simple_cancel, daemon=True)"""

new_simple_cancel = """    # Aspettiamo il completamento del batch 0 prima di richiedere il cancel
    def safe_cancel():
        batch0_done.wait(timeout=5.0)
        pipeline.cancel()
    t = threading.Thread(target=safe_cancel, daemon=True)"""

content = content.replace(old_simple_cancel, new_simple_cancel)

with open("tests/test_cancel.py", "w", encoding="utf-8") as f:
    f.write(content)
