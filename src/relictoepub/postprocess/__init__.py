"""Post-processing module — layout & asset extraction from OCR output."""

from relictoepub.postprocess.bbox_crop import crop_image_from_bbox
from relictoepub.postprocess.text_clean import clean_text
from relictoepub.postprocess.webp_optim import optimize_for_eink

__all__ = ["clean_text", "crop_image_from_bbox", "optimize_for_eink"]
