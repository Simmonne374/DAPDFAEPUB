"""Convertitore CLI single-book.

Uso:
    python scripts/convert_one.py path/to/book.pdf [output.epub]
    python scripts/convert_one.py book.pdf -o book.epub --quant int4 --dpi 300

Stampa a stdout l'avanzamento passo-passo.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Aggiunge la cartella radice del progetto al sys.path così `import relictoepub.*`
# funziona anche lanciando lo script da qualunque working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))  # così `import relictoepub.*` funziona
sys.path.insert(0, str(PROJECT_ROOT))  # fallback per compatibilità

from relictoepub.compile.build_epub import BookMetadata  # noqa: E402
from relictoepub.inference.config import InferenceConfig, QuantizationMode  # noqa: E402
from relictoepub.pipeline import (  # noqa: E402
    ModelNotFoundError,
    Pipeline,
    ProgressEvent,
    check_model_available,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="convert_one.py",
        description="Converti un PDF in EPUB3 via Unlimited-OCR (R-SWA).",
        epilog="Esempio: python convert_one.py samples/book.pdf out.epub --quant int4",
    )
    parser.add_argument("input", type=Path, help="PDF sorgente")
    parser.add_argument("output", type=Path, nargs="?", default=None,
                        help="EPUB di destinazione (default: <input>.epub)")
    parser.add_argument("--quant", choices=["none", "int8", "int4"],
                        default="int4", help="Quantizzazione del modello (default: int4)")
    parser.add_argument("--dpi", type=int, default=300,
                        help="Risoluzione di rendering per i crop (default: 300)")
    parser.add_argument("--pages-per-batch", type=int, default=20,
                        help="Pagine per batch di inferenza (default: 20, max 30 consigliati)")
    parser.add_argument("--title", type=str, default=None,
                        help="Titolo del libro (default: nome del PDF)")
    parser.add_argument("--author", type=str, default="Unknown",
                        help="Autore (default: Unknown)")
    parser.add_argument("--language", type=str, default="it",
                        help="Codice lingua ISO 639-1 (default: it)")
    parser.add_argument("--no-eink-optim", action="store_true",
                        help="Disabilita l'ottimizzazione WebP/E-ink")
        parser.add_argument("--chapter-pages", type=int, default=None,
                            help="Se il libro non ha struttura a heading (H1/H2), "
                                 "raggruppa le pagine in capitoli di N pagine.")
        parser.add_argument("--verbose", "-v", action="store_true",
                            help="Log dettagliato (DEBUG)")
        return parser.parse_args(argv)


def _event_printer() -> callable:
    """Ritorna una callback che stampa eventi in modo compatto."""
    t0 = time.perf_counter()

    def _cb(event: ProgressEvent) -> None:
        elapsed = time.perf_counter() - t0
        prefix = f"[{event.phase.upper():<10s}] t={elapsed:6.1f}s"
        bar = ""
        if event.total and event.percent:
            filled = int(round(event.percent / 5))
            bar = f" [{('█' * filled):<20s}] {event.percent:5.1f}%"
        print(f"{prefix}{bar} {event.message}", flush=True)
        if event.phase == "error":
            print("ERRORE:", event.message, file=sys.stderr)

    return _cb


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not args.input.exists():
        print(f"File non trovato: {args.input}", file=sys.stderr)
        return 2

    # Controlla preventivamente che il modello OCR sia in cache HF.
    # In caso contrario, evita un crash tardivo e mostra istruzioni chiare.
    if not check_model_available():
        print("⚠️  Modello 'baidu/Unlimited-OCR' non trovato nella cache HuggingFace.", file=sys.stderr)
        print("", file=sys.stderr)
        print("Per scaricarlo (~6 GB), esegui:", file=sys.stderr)
        print("    python scripts/download_model.py", file=sys.stderr)
        print("", file=sys.stderr)
        print("Oppure aprì la UI Gradio e usa il pulsante 'Scarica modello'.", file=sys.stderr)
        return 3

    output_epub = args.output or args.input.with_suffix(".epub")
    metadata = BookMetadata(
        title=args.title or args.input.stem,
        author=args.author,
        language=args.language,
    )
    config = InferenceConfig(
        quantization=QuantizationMode(args.quant),
        pages_per_batch=args.pages_per_batch,
    )

    pipeline = Pipeline(
        inference_config=config,
        dpi=args.dpi,
        max_pages_per_batch=args.pages_per_batch,
        eink_optimize=not args.no_eink_optim,
        metadata=metadata,
            chapter_pages=args.chapter_pages,
        )
    try:
        result = pipeline.run(args.input, output_epub, progress_callback=_event_printer())
    except KeyboardInterrupt:
        print("\nInterrotto dall'utente.", file=sys.stderr)
        return 130
    except ModelNotFoundError as exc:
        print(f"⚠️  {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        print(f"Conversione fallita: {exc}", file=sys.stderr)
        logging.exception("Dettaglio errore")
        return 1

    print()
    print("=" * 60)
    print(f"EPUB creato: {result.output_path}")
    print(f"Pagine processate: {result.pages_processed}")
    print(f"Immagini estratte: {result.images_extracted}")
    print(f"Caratteri Markdown: {result.markdown_chars}")
    print(f"Tempo totale: {result.total_seconds:.1f}s")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
