import re

with open("tests/test_cancel.py", "r", encoding="utf-8") as f:
    content = f.read()

old_simple_cancel = """    # Helper più diretto: attiviamo cancel subito dopo un piccolo delay,
    # assicurandoci che batch 0 sia già stato processato (visto che il mock
    # fa time.sleep 0.1 prima di done).
    def simple_cancel():
        time.sleep(0.15)  # > il delay di 0.1 nel mock → cancel dopo "done" del batch 0
        pipeline.cancel()
    t = threading.Thread(target=simple_cancel, daemon=True)"""

new_simple_cancel = """    # Helper più diretto: attiviamo cancel subito dopo un piccolo delay,
    # assicurandoci che batch 0 sia già stato processato (visto che il mock
    # fa time.sleep 0.1 prima di done).
    def simple_cancel():
        time.sleep(0.5)  # > il delay di 0.1 nel mock → cancel dopo "done" del batch 0
        pipeline.cancel()
    t = threading.Thread(target=simple_cancel, daemon=True)"""

content = content.replace(old_simple_cancel, new_simple_cancel)

with open("tests/test_cancel.py", "w", encoding="utf-8") as f:
    f.write(content)
