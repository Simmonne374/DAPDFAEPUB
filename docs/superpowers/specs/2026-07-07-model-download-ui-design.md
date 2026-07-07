# Design Spec: Model Download UI and Missing Dependencies

This document details the design for adding a model check/download UI section to the RelicToEpub Gradio interface, and resolving missing model dependencies.

## 1. Goal
Provide a way for the user to pre-download the ~6 GB `baidu/Unlimited-OCR` model directly from the Gradio web interface with real-time feedback, and resolve runtime dependencies required by the Hugging Face model dynamic code.

## 2. Missing Dependencies
The model card code for `baidu/Unlimited-OCR` dynamically imports:
* `addict`
* `matplotlib`
* `torchvision`

We will add these to `pyproject.toml` under `dependencies` and install them in the virtual environment.

## 3. Architecture & Code Changes

### 3.1 Dependency Registry Updates
In [pyproject.toml](file:///C:/Users/simmo/Desktop/DAPDFAEPUB/pyproject.toml), we add:
* `"addict"`
* `"matplotlib"`
* `"torchvision"` (compatible with PyTorch)

### 3.2 UI Components Changes
In [src/relictoepub/ui/components.py](file:///C:/Users/simmo/Desktop/DAPDFAEPUB/src/relictoepub/ui/components.py):
* We will implement a `check_model_status()` function:
  ```python
  from huggingface_hub import try_to_load_from_cache
  
  def check_model_status(model_id: str = "baidu/Unlimited-OCR") -> tuple[bool, str]:
      """Checks if the model config file is cached locally.
      
      Returns (is_cached, status_text_formatted_markdown).
      """
      try:
          # Check for config.json cache path
          path = try_to_load_from_cache(model_id, "config.json")
          if isinstance(path, str):
              return True, "🟢 **Modello rilevato localmente** (pronto all'uso)"
      except Exception:
          pass
      return False, "🔴 **Modello non presente localmente** (sarà scaricato al primo avvio)"
  ```

### 3.3 Main App UI Changes
In [src/relictoepub/ui/gradio_app.py](file:///C:/Users/simmo/Desktop/DAPDFAEPUB/src/relictoepub/ui/gradio_app.py):
* Import `check_model_status` and `subprocess`, `sys`.
* Update `build_demo` to add the model download section in the Left Column, above the PDF upload component:
  ```python
  with gr.Group():
      gr.Markdown("### 📦 Modello OCR (Unlimited-OCR)")
      model_status = gr.Markdown(value=check_model_status()[1])
      download_btn = gr.Button("📥 Scarica/Aggiorna Modello (~6 GB)", variant="secondary")
  ```
* Define the generator function `_download_model_ui()` that handles the download:
  ```python
  def _download_model_ui() -> Iterator[tuple[str, str, gr.components.Component]]:
      """Generator that runs huggingface-cli download in a subprocess.
      
      Yields updates to (log_text, model_status_text, download_button_update).
      """
      log_text = "🔄 Inizio download del modello 'baidu/Unlimited-OCR' (circa 6 GB)..."
      yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False)
      
      # Determine executable path
      # Under Windows .venv, it's .venv/Scripts/huggingface-cli
      cli_path = Path(sys.executable).parent / "huggingface-cli"
      if not cli_path.exists() and not cli_path.with_suffix(".exe").exists():
          cli_path = "huggingface-cli"  # fallback to system path
          
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
          
          # Read lines and stream
          for line in iter(process.stdout.readline, ""):
              log_text = (log_text + "\n" + line.strip()).strip()
              yield log_text, "⏳ **Download in corso...**", gr.Button(interactive=False)
              
          process.wait()
          if process.returncode == 0:
              log_text += "\n\n✅ Modello scaricato e verificato con successo!"
              is_ok, status_str = check_model_status()
              yield log_text, status_str, gr.Button(interactive=True)
          else:
              log_text += f"\n\n❌ Errore durante il download del modello. Codice d'uscita: {process.returncode}"
              yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True)
      except Exception as e:
          log_text += f"\n\n❌ Errore imprevisto durante l'avvio del download: {e}"
          yield log_text, "🔴 **Errore nel download del modello**", gr.Button(interactive=True)
  ```
* Wire the `download_btn` click event to `_download_model_ui`:
  ```python
  download_btn.click(
      fn=_download_model_ui,
      inputs=[],
      outputs=[log, model_status, download_btn]
  )
  ```

## 4. Testing & Verification
1. Install new dependencies: `uv pip install -e ".[dev]"`
2. Verify dependency resolution and model loading behavior.
3. Launch UI and trigger download. Verify that output is streamed correctly to the log pane.
4. Verify that status changes to green `🟢 Modello rilevato localmente` once download finishes.
