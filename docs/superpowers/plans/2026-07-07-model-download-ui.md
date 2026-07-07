# Model Download UI and Dependencies Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a model download button and status indicator at the top of the Gradio UI, streaming the download progress live to the main log, and resolve missing system/Python dependencies for Unlimited-OCR.

**Architecture:** Update `pyproject.toml` with `addict`, `matplotlib`, and `torchvision`. Add a check helper in components.py. Modify gradio_app.py to display the model status and run a subprocess calling `huggingface-cli download` when the download button is clicked, yielding stdout/stderr updates back to the log panel.

**Tech Stack:** Python, Gradio, subprocess, huggingface-hub (huggingface-cli).

## Global Constraints

- Requires-python: >=3.10
- Maintain compatibility with GTX 1080 Ti / CUDA 11.8.

---

### Task 1: Dependency Updates

**Files:**
- Modify: `pyproject.toml`
- Test: Virtual environment installation test

**Interfaces:**
- Consumes: None
- Produces: Installed packages `addict`, `matplotlib`, and `torchvision` in the virtual environment.

- [ ] **Step 1: Modify `pyproject.toml`**

Add the missing dependencies to the list:
```toml
  # In pyproject.toml under dependencies:
  "addict",
  "matplotlib",
  "torchvision",
```

- [ ] **Step 2: Run installation command**

Run in terminal:
```bash
uv pip install -e ".[dev]"
```
Expected output: Success output resolving and installing `addict`, `matplotlib`, and `torchvision`.

- [ ] **Step 3: Run quick import test**

Run:
```bash
.venv\Scripts\python.exe -c "import addict; import matplotlib; import torchvision; print('Imports OK')"
```
Expected output: `Imports OK`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add addict, matplotlib, and torchvision dependencies"
```

---

### Task 2: Model Status Check Helper

**Files:**
- Modify: `src/relictoepub/ui/components.py`
- Create: `tests/test_model_status.py`

**Interfaces:**
- Consumes: `huggingface_hub.try_to_load_from_cache`
- Produces: `check_model_status(model_id: str = "baidu/Unlimited-OCR") -> tuple[bool, str]`

- [ ] **Step 1: Write the test**

Create `tests/test_model_status.py`:
```python
import pytest
from relictoepub.ui.components import check_model_status

def test_check_model_status():
    is_ok, status_str = check_model_status("baidu/Unlimited-OCR")
    assert isinstance(is_ok, bool)
    assert "Modello" in status_str
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
pytest tests/test_model_status.py -v
```
Expected: FAIL with `ImportError: cannot import name 'check_model_status' from 'relictoepub.ui.components'`

- [ ] **Step 3: Implement `check_model_status`**

Modify `src/relictoepub/ui/components.py`:
Add the import and the function implementation:
```python
# At the top of src/relictoepub/ui/components.py
from huggingface_hub import try_to_load_from_cache

# Add function to components.py
def check_model_status(model_id: str = "baidu/Unlimited-OCR") -> tuple[bool, str]:
    """Controlla se il file di configurazione del modello è presente nella cache locale."""
    try:
        path = try_to_load_from_cache(model_id, "config.json")
        if isinstance(path, str):
            return True, "🟢 **Modello rilevato localmente** (pronto all'uso)"
    except Exception:
        pass
    return False, "🔴 **Modello non presente localmente** (scaricalo ora o verrà scaricato al primo avvio)"
```
Also add `"check_model_status"` to `__all__` list at the bottom of `src/relictoepub/ui/components.py`.

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
pytest tests/test_model_status.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/relictoepub/ui/components.py tests/test_model_status.py
git commit -m "feat: add check_model_status helper function"
```

---

### Task 3: Gradio UI Integration

**Files:**
- Modify: `src/relictoepub/ui/gradio_app.py`
- Test: Manual UI validation

**Interfaces:**
- Consumes: `check_model_status` from `relictoepub.ui.components`
- Produces: Model download interface and `_download_model_ui()` generator in Gradio UI.

- [ ] **Step 1: Import check_model_status and system modules**

Modify `src/relictoepub/ui/gradio_app.py`:
Add imports near the top:
```python
# In src/relictoepub/ui/gradio_app.py:
import sys
import subprocess
from relictoepub.ui.components import (
    advanced_options,
    epub_download,
    gallery_preview,
    log_panel,
    upload_pdf,
    check_model_status,  # <-- Add this import
)
```

- [ ] **Step 2: Implement `_download_model_ui`**

Add the generator function in `src/relictoepub/ui/gradio_app.py` before `build_demo`:
```python
def _download_model_ui() -> Iterator[tuple[str, str, gr.components.Component]]:
    """Avvia il download del modello Unlimited-OCR tramite subprocess.
    
    Yields tuple (log_text, model_status_text, download_button_update).
    """
    log_text = "🔄 Inizio download del modello 'baidu/Unlimited-OCR' (circa 6 GB)..."
    yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False)
    
    # Trova il percorso dell'eseguibile huggingface-cli
    cli_path = Path(sys.executable).parent / "huggingface-cli"
    if not cli_path.exists() and not cli_path.with_suffix(".exe").exists():
        cli_path = Path("huggingface-cli")  # fallback
        
    cmd = [
        str(cli_path), "download", "baidu/Unlimited-OCR",
        "--include", "*.safetensors", "--include", "*.json", "--include", "*.py",
        "--include", "*.txt", "--include", "*.bin", "--include", "*.md"
    ]
    
    try:
        import os
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=os.environ.copy()
        )
        
        # Legge lo standard output del processo riga per riga
        for line in iter(process.stdout.readline, ""):
            log_text = (log_text + "\n" + line.strip()).strip()
            yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False)
            
        process.wait()
        if process.returncode == 0:
            log_text += "\n\n✅ Modello scaricato e verificato con successo!"
            _, status_str = check_model_status()
            yield log_text, status_str, gr.Button(interactive=True)
        else:
            log_text += f"\n\n❌ Errore durante il download del modello. Codice d'uscita: {process.returncode}"
            yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True)
    except Exception as e:
        log_text += f"\n\n❌ Errore imprevisto durante l'avvio del download: {e}"
        yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True)
```

- [ ] **Step 3: Modify `build_demo` to insert model block**

Modify `build_demo()` to display the new UI component above `upload_pdf()` (around line 159):
```python
        with gr.Row():
            # ============= COLONNA SINISTRA — input =============
            with gr.Column(scale=1):
                # Nuova sezione download modello
                with gr.Group():
                    gr.Markdown("### 📦 Modello OCR (Unlimited-OCR)")
                    model_status = gr.Markdown(value=check_model_status()[1])
                    download_btn = gr.Button("📥 Scarica/Aggiorna Modello (~6 GB)", variant="secondary")
                
                pdf_input = upload_pdf()
```
And wire the click listener near the end of `build_demo()`:
```python
        # Wiring per il download del modello
        download_btn.click(
            fn=_download_model_ui,
            inputs=[],
            outputs=[log, model_status, download_btn],
        )

        # Wiring: click → streaming updates su log, gallery, download
        run_btn.click(
```

- [ ] **Step 4: Manual Test Verification**

1. Launch UI:
```bash
.venv\Scripts\python.exe scripts/launch_ui.py
```
2. Open http://127.0.0.1:7860 in the browser.
3. Verify that the "📦 Modello OCR (Unlimited-OCR)" group appears at the top left.
4. If model is already cached, it shows the green status indicator. If not, it shows the red indicator.
5. Click "📥 Scarica/Aggiorna Modello (~6 GB)".
6. Verify that the button is disabled, status changes to "Download in corso...", and download progress streams into the right Log pane.
7. Verify that when complete, the status becomes green and the button is enabled again.

- [ ] **Step 5: Commit**

```bash
git add src/relictoepub/ui/gradio_app.py
git commit -m "feat: integrate model download box and process in Gradio UI"
```
