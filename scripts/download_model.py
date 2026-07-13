"""Pre-download del modello Unlimited-OCR.

Mostra i progressi real-time del download da HuggingFace (~6 GB).
Utile per evitare che la prima conversione blocchi la UI senza feedback.

Uso:
    python scripts/download_model.py
    python scripts/download_model.py --quiet     # solo barra tqdm, no log iniziale
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))
sys.path.insert(0, str(PROJECT_ROOT))

from relictoepub.inference.config import InferenceConfig  # noqa: E402


def main() -> int:
    cfg = InferenceConfig()
    print(f"Modello: {cfg.model_id}")
    print(f"Cache:   {cfg.cache_dir or '~/.cache/huggingface'}")
    print()
    print("Download in corso (potrebbe richiedere 5-20 minuti, ~6 GB)...")
    print()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERRORE: huggingface_hub non installato. Eseguo pip install...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface_hub"])
        from huggingface_hub import snapshot_download

    # HF_TOKEN opzionale per modelli gated
    kwargs: dict = {}
    hf_token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_HUB_TOKEN")
    if hf_token:
        kwargs["token"] = hf_token

    try:
        path = snapshot_download(
            repo_id=cfg.model_id,
            cache_dir=str(cfg.cache_dir) if cfg.cache_dir else None,
            allow_patterns=[
                "*.json", "*.py", "*.txt", "*.md", "*.model",
                "*.safetensors", "*.bin",
                "tokenizer*", "vocab.*", "merges.*", "special_tokens*",
            ],
            tqdm_class=None,  # usa il tqdm di default che mostra la barra
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ Download fallito: {exc}", file=sys.stderr)
        return 1

    print()
    print(f"✅ Modello scaricato in: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
