"""Pre-download del modello Unlimited-OCR.

Mostra i progressi real-time del download da HuggingFace (~6 GB).
Utile per evitare che la prima conversione blocchi la UI senza feedback.
"""

from __future__ import annotations

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
    print("Download in corso (potrebbe richiedere 5-20 minuti)...")
    print()

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("ERRORE: huggingface_hub non installato. Eseguo pip install...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface_hub"])
        from huggingface_hub import snapshot_download

    path = snapshot_download(
        repo_id=cfg.model_id,
        cache_dir=str(cfg.cache_dir) if cfg.cache_dir else None,
        allow_patterns=["*.json", "*.py", "*.txt", "*.safetensors", "*.bin", "*.md"],
        tqdm_class=None,  # usa il tqdm di default che mostra la barra
    )
    print()
    print(f"✅ Modello scaricato in: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())