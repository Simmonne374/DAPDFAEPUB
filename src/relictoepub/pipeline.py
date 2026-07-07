"""Orchestratore end-to-end: PDF → EPUB3.

La classe :class:`Pipeline` è l'unico punto di contatto che la CLI e
la UI Gradio usano. Espone un'API sincrona (con aggiornamenti di stato)
e un'API generator-based (``run_iter``) che emette eventi
``ProgressEvent`` consumabili dalla UI live.

Flusso:
1. **Ingest** (PyMuPDF) → PNG 300 DPI + 1024×1024
2. **OCR** (Unlimited-OCR, 4-bit) → Markdown per batch di N pagine
3. **Clean** → testo normalizzato, BBox estratti
4. **Crop** → immagini ritagliate con Pillow
5. **Optimize** → WebP grayscale ottimizzato E-ink
6. **Compile** (pypandoc + ebooklib) → file ``.epub``
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from relictoepub.compile.build_epub import BookMetadata, build_epub
from relictoepub.ingest import IngestResult, render_pdf
from relictoepub.inference.config import InferenceConfig, QuantizationMode
from relictoepub.inference.unlimited_ocr import UnlimitedOCRRunner
from relictoepub.postprocess.bbox_crop import (
    BBox,
    crop_image_from_bbox,
    extract_bbox_tokens,
)
from relictoepub.postprocess.text_clean import clean_text
from relictoepub.postprocess.webp_optim import optimize_batch

logger = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """Singolo evento di progresso emesso dalla pipeline.

    Attributes:
        phase: fase corrente (``"rendering"``, ``"ocr"``, ``"cropping"``,
            ``"optimizing"``, ``"compiling"``, ``"done"``, ``"error"``).
        message: descrizione human-readable.
        current: elemento corrente (pagina, immagine...).
        total: totale elementi della fase.
        percent: 0-100 (per comodità della UI).
        extra: dati extra (path, statistiche...).
    """

    phase: str
    message: str = ""
    current: int = 0
    total: int = 0
    percent: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """Risultato finale di una conversione."""

    output_path: Path
    pages_processed: int
    images_extracted: int
    total_seconds: float
    markdown_chars: int
    extra: dict[str, Any] = field(default_factory=dict)


class Pipeline:
    """Orchestratore della pipeline PDF → EPUB3.

    Esempio (CLI):
        >>> pipeline = Pipeline()
        >>> for event in pipeline.run_iter(Path("book.pdf"), Path("book.epub")):
        ...     print(event.phase, event.message)
        >>> # Risultato finale: Path("book.epub")

    Esempio (sincrono):
        >>> result = pipeline.run(Path("book.pdf"), Path("book.epub"))
    """

    def __init__(
        self,
        *,
        inference_config: InferenceConfig | None = None,
        dpi: int = 300,
        target_size: int = 1024,
        max_pages_per_batch: int = 20,
        eink_optimize: bool = True,
        metadata: BookMetadata | None = None,
        work_dir: Path | None = None,
    ) -> None:
        self.inference_config = inference_config or InferenceConfig(
            quantization=QuantizationMode.INT4
        )
        self.dpi = dpi
        self.target_size = target_size
        # Sincronizza il batch size pipeline ↔ config modello
        self.max_pages_per_batch = min(
            max_pages_per_batch, max(1, self.inference_config.pages_per_batch)
        )
        self.inference_config.pages_per_batch = self.max_pages_per_batch
        self.eink_optimize = eink_optimize
        self.metadata = metadata
        self.work_dir = work_dir
        self._runner: UnlimitedOCRRunner | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        input_pdf: Path,
        output_epub: Path,
        progress_callback: Callable[[ProgressEvent], None] | None = None,
    ) -> PipelineResult:
        """Versione sincronizzata. Eventuali eventi arrivano al callback.

        Returns:
            :class:`PipelineResult` con i dati finali della conversione.
        """
        last_result: PipelineResult | None = None
        for event in self.run_iter(input_pdf, output_epub):
            if progress_callback is not None:
                progress_callback(event)
            if event.phase == "done":
                data = event.extra.get("result")
                if data:
                    last_result = PipelineResult(**data)
        if last_result is None:
            raise RuntimeError(
                "Pipeline terminata senza emettere l'evento 'done'. "
                "Controllare i log per errori."
            )
        return last_result

    def run_iter(
        self,
        input_pdf: Path,
        output_epub: Path,
    ) -> Iterator[ProgressEvent]:
        """Generatore di eventi di progresso; alla fine emette ``"done"``.

        Yields:
            Eventi :class:`ProgressEvent` per ogni fase.
        """
        input_pdf = Path(input_pdf)
        output_epub = Path(output_epub)
        if self.metadata is None:
            self.metadata = BookMetadata(title=input_pdf.stem)
        start = time.perf_counter()

        # 1) Ingest
        yield ProgressEvent(phase="rendering", message="Inizio rendering PDF…")
        ingest_result = render_pdf(
            input_pdf,
            output_dir=self.work_dir,
            dpi=self.dpi,
            target_size=self.target_size,
        )
        total_pages = len(ingest_result.pages)
        yield ProgressEvent(
            phase="rendering",
            message=f"Renderizzate {total_pages} pagine",
            current=total_pages, total=total_pages, percent=100.0,
            extra={"output_dir": str(ingest_result.output_dir)},
        )

        # 2) OCR in batch
        if self._runner is None:
            self._runner = UnlimitedOCRRunner(self.inference_config)
        all_markdown_parts: list[str] = []
        all_bboxes_per_page: list[list[BBox]] = []
        all_pages_processed = 0

        for batch_start in range(0, total_pages, self.max_pages_per_batch):
            batch_end = min(batch_start + self.max_pages_per_batch, total_pages)
            batch_pages = ingest_result.pages[batch_start:batch_end]
            yield ProgressEvent(
                phase="ocr",
                message=f"OCR batch pagine {batch_start+1}-{batch_end}/{total_pages}",
                current=batch_start + 1, total=total_pages,
                percent=(batch_end / total_pages) * 100.0,
                extra={"batch_size": len(batch_pages)},
            )
            normalized_paths = [p.normalized_path for p in batch_pages]
            
            final_raw_text = ""
            for partial_text, status in self._runner.run_batch_iter(normalized_paths):
                if status == "running":
                    chunk = partial_text[-500:] if len(partial_text) > 500 else partial_text
                    yield ProgressEvent(
                        phase="ocr",
                        message=f"OCR batch pagine {batch_start+1}-{batch_end}/{total_pages}\n\n[Testo estratto in tempo reale]:\n{chunk}",
                        current=batch_start + 1, total=total_pages,
                        percent=(batch_end / total_pages) * 100.0,
                        extra={"batch_size": len(batch_pages)},
                    )
                else:
                    final_raw_text = partial_text
                    
            raw_text = final_raw_text.strip()
            markdown = self._runner._strip_image_tokens(raw_text)
            all_markdown_parts.append(markdown)
            all_pages_processed += raw_text.count("<page>") or len(batch_pages)
            # Le BBox arrivano nel raw_text; le estraiamo ora (lazy)
            page_bboxes: list[BBox] = extract_bbox_tokens(raw_text)
            # Distribuisci uniformemente le bbox sulle pagine del batch
            per_page: list[list[BBox]] = [[] for _ in batch_pages]
            if page_bboxes:
                # euristica: divide in modo equo se il numero non corrisponde
                per_chunk = max(1, len(page_bboxes) // len(batch_pages))
                for i, bbox in enumerate(page_bboxes):
                    target_idx = min(i // per_chunk, len(batch_pages) - 1)
                    per_page[target_idx].append(bbox)
            all_bboxes_per_page.extend(per_page)

        full_markdown = "\n\n".join(all_markdown_parts)

        # 3) Clean
        yield ProgressEvent(phase="cleaning", message="Pulizia testo OCR…")
        cleaned = clean_text(full_markdown)

        # 4) Crop immagini
        yield ProgressEvent(phase="cropping", message="Ritaglio immagini…")
        crops_dir = ingest_result.output_dir / "crops"
        crops_dir.mkdir(exist_ok=True)
        saved_crops: list[Path] = []
        for page, bboxes in zip(ingest_result.pages, all_bboxes_per_page):
            for j, bbox in enumerate(bboxes):
                label = bbox.label or f"asset{j:02d}"
                out = crops_dir / f"page{page.page_num:04d}_{label}.png"
                result_path = crop_image_from_bbox(
                    page.original_path, bbox, output_path=out
                )
                if result_path is not None:
                    saved_crops.append(result_path)
        yield ProgressEvent(
            phase="cropping",
            message=f"Ritagliate {len(saved_crops)} immagini",
            current=len(saved_crops), total=len(saved_crops), percent=100.0,
        )

        # 5) WebP optimization
        if self.eink_optimize and saved_crops:
            yield ProgressEvent(phase="optimizing", message="Ottimizzazione WebP…")
            webp_dir = ingest_result.output_dir / "webp"
            webp_paths = optimize_batch(saved_crops, webp_dir)
            final_images = webp_paths
        else:
            final_images = saved_crops

        # 6) Compile EPUB
        yield ProgressEvent(phase="compiling", message="Compilazione EPUB3…")
        result_path = build_epub(
            markdown=cleaned,
            images=final_images,
            metadata=self.metadata,
            output_path=output_epub,
            cover_image=ingest_result.pages[0].original_path if ingest_result.pages else None,
        )

        elapsed = time.perf_counter() - start
        result = PipelineResult(
            output_path=result_path,
            pages_processed=all_pages_processed,
            images_extracted=len(saved_crops),
            total_seconds=elapsed,
            markdown_chars=len(cleaned),
            extra={
                "rendered_pages": total_pages,
                "images_used_in_epub": len(final_images),
                "dpi": self.dpi,
                "quantization": self.inference_config.quantization.value,
            },
        )
        yield ProgressEvent(
            phase="done",
            message=f"Fatto in {elapsed:.1f}s — EPUB: {result_path}",
            current=total_pages, total=total_pages, percent=100.0,
            extra={"result": result.__dict__, "output": str(result_path)},
        )


__all__ = ["Pipeline", "PipelineResult", "ProgressEvent"]
