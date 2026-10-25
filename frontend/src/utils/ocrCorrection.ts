import type { DocumentDetail, OCRResult } from "../api/document";

export type OcrSaveResult =
	| { ok: true; ocr: OCRResult }
	| { ok: false; detail: string };

/** Canonical editable OCR text. Search, RAG, and the editor all read this. */
export const canonicalExtractedText = (
	ocr: Pick<OCRResult, "extracted_text"> | null | undefined
): string => ocr?.extracted_text ?? "";

/** Original engine output, used only as evidence — never as the live text. */
export const originalOcrText = (
	ocr: Pick<OCRResult, "extracted_text" | "raw_text"> | null | undefined
): string => ocr?.raw_text || ocr?.extracted_text || "";

export const mergeOcrResult = (
	results: OCRResult[] | undefined,
	saved: OCRResult
): OCRResult[] =>
	(results ?? []).map((ocr) =>
		Number(ocr.id) === Number(saved.id) ? { ...ocr, ...saved } : ocr
	);

export const applySavedOcrToDocuments = (
	documents: DocumentDetail[],
	documentId: number,
	saved: OCRResult
): DocumentDetail[] =>
	documents.map((document) =>
		document.id === documentId
			? {
					...document,
					ocr_results: mergeOcrResult(document.ocr_results, saved),
				}
			: document
	);

export const ocrSaveFeedback = (
	result: OcrSaveResult
): {
	successMessage: string | null;
	errorMessage: string | null;
	savedOcr: OCRResult | null;
} => {
	if (!result.ok) {
		return {
			successMessage: null,
			errorMessage: result.detail || "Failed to update OCR text.",
			savedOcr: null,
		};
	}
	return {
		successMessage: "OCR text updated and original preserved.",
		errorMessage: null,
		savedOcr: result.ocr,
	};
};
