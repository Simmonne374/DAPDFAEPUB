"""Avvia l'interfaccia Gradio su http://127.0.0.1:7860.

Uso:
    python scripts/launch_ui.py
    python scripts/launch_ui.py --host 0.0.0.0 --port 7860 --share
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))  # così `import relictoepub.*` funziona
sys.path.insert(0, str(PROJECT_ROOT))  # fallback per compatibilità

from relictoepub.ui.gradio_app import build_demo  # noqa: E402


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="launch_ui.py",
        description="Avvia l'interfaccia web Gradio di RelicToEpub.",
    )
    p.add_argument("--host", default="127.0.0.1", help="Hostname binding (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=7860, help="Porta HTTP (default 7860)")
    p.add_argument("--share", action="store_true",
                   help="Crea un link pubblico via Gradio (utile per demo)")
    p.add_argument("--verbose", "-v", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    demo = build_demo()
    demo.queue()  # conversione lunga → coda
    print(f"\n  RelicToEpub UI pronta su http://{args.host}:{args.port}\n")
    demo.launch(
        server_name=args.host,
        server_port=args.port,
        share=args.share,
        inbrowser=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
