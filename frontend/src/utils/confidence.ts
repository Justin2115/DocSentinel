// Confidence values shown in the review UI are only ever numbers an engine or
// the extraction pipeline actually produced. A missing measurement renders as
// "not measured" instead of a default, because a reviewer reading "85%" would
// otherwise treat an invented figure as accuracy.

export const formatConfidence = (
  value: number | null | undefined
): string =>
  typeof value === "number" && Number.isFinite(value)
    ? `${Math.round(value)}%`
    : "not measured";

export const CONFIDENCE_EXPLANATIONS = {
  page:
    "Word-level confidence reported by the OCR engine for this page. " +
    "'not measured' means the page had no OCR-scored text, for example a " +
    "digital text layer or a blank page.",
  field:
    "Strength of the pattern that matched this value. It is a match score, " +
    "not the probability that the value is correct.",
  document:
    "Weighted quality score over the signals available for this document " +
    "(measured OCR confidence, field match strength, classification). " +
    "It is not an accuracy percentage.",
} as const;
