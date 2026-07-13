# RelicToEpub

[![Build Windows installer](https://github.com/Simmonne374/DAPDFAEPUB/actions/workflows/build-windows.yml/badge.svg)](https://github.com/Simmonne374/DAPDFAEPUB/actions/workflows/build-windows.yml)
[![Latest release](https://img.shields.io/github/v/release/Simmonne374/DAPDFAEPUB?label=latest)](https://github.com/Simmonne374/DAPDFAEPUB/releases/tag/latest)

> Pipeline PDF → EPUB3 basata su **Baidu Unlimited-OCR** (R-SWA architecture)
>
> Wrapper Python leggero attorno al modello SOTA open-source per il document parsing.

Scarica l'ultimo installer Windows dalla [pagina Releases](../../releases) (tag `latest`,
oppure scegli uno specifico `build-<commit>` per una build puntuale).

---

## Cosa fa

RelicToEpub trasforma PDF scansioni in **EPUB3 reflowable** ottimizzati per lettori E-ink (Kindle, Kobo, reMarkable…).

Il motore OCR è **Baidu Unlimited-OCR** (paper: [arXiv:2606.23050](https://arxiv.org/abs/2606.23050), MIT, 3B-MoE 0.5B activated), che eccelle su **Books / Magazines / Newspapers** secondo i benchmark OmniDocBench (overall 93.23 su v1.5, **SOTA**).

```
PDF ─▶ PyMuPDF (300 DPI + 1024px)
   ─▶ Unlimited-OCR (HF Transformers, 4-bit quant)
   ─▶ bboxes [0–1000] → Pillow crop immagini
   ─▶ WebP grayscale 8-bit (E-ink ready)
   ─▶ pypandoc → XHTML semantico → ebooklib → .epub
```

## Requisiti hardware

| Setup | GPU | RAM | Note |
|---|---|---|---|
| **Consigliato** | GTX 1080 Ti (11GB) o superiore | 16 GB di sistema | Quantizzazione 4-bit (NF4) |
| Solo CPU | – | 32 GB di sistema | ~1 min per pagina, fattibile per MVP 1–10 libri |

**Verificato** su OmniDocBench che Unlimited-OCR è SOTA anche rispetto a modelli 80× più grandi (Qwen3-VL 235B, InternVL3.5 241B). Vantaggio: gira su hardware entry-level grazie al **MoE 3B / 500M attivi**.

## Installazione

### 1. Prerequisiti di sistema

| Tool | Come | Note |
|---|---|---|
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) | 3.11 consigliato; **3.14 ha problemi con torch CUDA wheels** — meglio 3.11/3.12 |
| **pandoc** | [github.com/jgm/pandoc](https://github.com/jgm/pandoc/releases) | Richiesto da `pypandoc`. Windows: MSI installer. |
| **uv** (opzionale ma consigliato) | `pip install uv` | Package manager veloce |

### 2. Virtual environment

```bash
# Con venv classico
python -m venv .venv
.venv\Scripts\activate           # Windows bash / Git Bash
# oppure source .venv/bin/activate  # Linux/macOS

# oppure con uv (più veloce)
uv venv
```

### 3. Installazione dipendenze

```bash
# Stack CPU-only (no GPU, più semplice)
uv pip install -e ".[dev,cpu]"

# GPU Pascal (GTX 1080 Ti, GTX 1070, …) — CUDA 11.8
uv pip install torch --index-url https://download.pytorch.org/whl/cu118
uv pip install -e ".[dev]"

# GPU Ampere/Hopper (RTX 30xx/40xx/50xx, A100, H100) — CUDA 12.4
uv pip install torch --index-url https://download.pytorch.org/whl/cu124
uv pip install -e ".[dev]"
```

### 4. Download del modello (al primo utilizzo)

Il modello (~6 GB, MIT) verrà scaricato automaticamente al primo lancio della pipeline.
Per pre-scaricarlo:

```bash
huggingface-cli download baidu/Unlimited-OCR --include "*.safetensors" "*.json" "*.py"
```

## Utilizzo

### CLI — singolo libro

```bash
python scripts/convert_one.py path/to/book.pdf output.epub
```

Lo script mostra una barra di progresso testuale via `rich` ed emette l'EPUB finale al termine.

### UI Gradio

```bash
python scripts/launch_ui.py
# → apre http://127.0.0.1:7860 nel browser predefinito
```

L'interfaccia espone: upload PDF, opzioni avanzate collassate, progress bar live, log streaming, galleria di preview, download dell'EPUB.

### Python API

```python
from relictoepub.pipeline import Pipeline

pipeline = Pipeline(quantization="4bit", dpi_render=300)
pipeline.run("samples/book.pdf", "output/book.epub")
```

## Architettura (file map)

```
src/
├── ingest.py                      # Modulo 1 — PyMuPDF
├── inference/
│   ├── unlimited_ocr.py           # Modulo 2 — HF Transformers wrapper
│   └── config.py                  # Parametri del paper
├── postprocess/
│   ├── bbox_crop.py               # Modulo 3 — coordinate 0-1000 → pixel
│   ├── webp_optim.py              # Modulo 4 — grayscale + WebP
│   └── text_clean.py              # De-hyphenation regex
├── compile/
│   ├── build_epub.py              # Modulo 5 — pypandoc + ebooklib
│   └── eink_css.py                # CSS specifico E-ink
├── ui/
│   ├── gradio_app.py              # Modulo 6 — Gradio UI
│   └── components.py              # Blocchi UI riusabili
└── pipeline.py                    # Orchestratore unico
```

## Limitazioni note (dal paper)

1. **Context 32K token** → batch di ~20–30 pagine per singolo forward. Libri grandi sono gestiti a batch (la pipeline lo fa in automatico).
2. **Base mode 1024×1024** può perdere testo molto piccolo in pagine dense.
3. Roadmap di Baidu: context 128K in futuro (nessuna azione richiesta qui, basterà cambiare `max_length`).

## Testing

```bash
pytest                          # veloce, niente modello
pytest --run-slow               # include i test che richiedono Unlimited-OCR
```

## Riferimenti

- Paper: *Unlimited OCR Works — Welcome the Era of One-shot Long-horizon Parsing*, Baidu 2026, [arXiv:2606.23050](https://arxiv.org/abs/2606.23050)
- Modello: [huggingface.co/baidu/Unlimited-OCR](https://huggingface.co/baidu/Unlimited-OCR) (MIT license)
- Codice di riferimento: [github.com/baidu/Unlimited-OCR](http://github.com/baidu/Unlimited-OCR)
- Benchmark: [OmniDocBench](https://github.com/OpenDataLab/OmniDocBench)

## License

MIT. Vedi `LICENSE`.
