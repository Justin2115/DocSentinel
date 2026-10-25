import assert from "node:assert/strict";
import { describe, it } from "node:test";

import type { DocumentDetail, OCRResult } from "../api/document";
import {
	applySavedOcrToDocuments,
	canonicalExtractedText,
	ocrSaveFeedback,
	originalOcrText,
} from "./ocrCorrection.ts";

const ocr = (
	overrides: Partial<OCRResult> = {}
): OCRResult => ({
	id: 7,
	document_id: 3,
	page_id: 11,
	extracted_text: "corrected invoice total 12500",
	raw_text: "c0rrected 1nvoice t0tal 12500",
	confidence: 72,
	ocr_engine: "PaddleOCR",
	processed_at: null,
	...overrides,
});

const document = (
	overrides: Partial<DocumentDetail> = {}
): DocumentDetail =>
	({
		id: 3,
		original_filename: "invoice.pdf",
		stored_filename: "invoice.pdf",
		file_path: "uploads/invoice.pdf",
		file_type: "application/pdf",
		file_size: 100,
		document_type: null,
		department: "FINANCE",
		status: "needs_review",
		uploaded_by: 1,
		assigned_checker: null,
		overall_confidence: 70,
		uploaded_at: null,
		processed_at: null,
		pages: [],
		ocr_results: [ocr({ extracted_text: "old extracted" })],
		extracted_fields: [],
		extracted_tables: [],
		review_items: [],
		...overrides,
	}) as DocumentDetail;

describe("OCR correction display and save state", () => {
	it("displays the saved extracted_text rather than raw_text", () => {
		const saved = ocr();
		assert.equal(
			canonicalExtractedText(saved),
			"corrected invoice total 12500"
		);
		assert.equal(
			originalOcrText(saved),
			"c0rrected 1nvoice t0tal 12500"
		);
		assert.notEqual(canonicalExtractedText(saved), originalOcrText(saved));
	});

	it("applies the saved OCR row onto the matching document and page", () => {
		const saved = ocr();
		const next = applySavedOcrToDocuments([document()], 3, saved);
		const updated = next[0].ocr_results[0];
		assert.equal(updated.extracted_text, saved.extracted_text);
		assert.equal(updated.raw_text, saved.raw_text);
	});

	it("does not treat a failed save as success or mutate OCR text", () => {
		const feedback = ocrSaveFeedback({
			ok: false,
			detail: "OCR record not found",
		});
		assert.equal(feedback.successMessage, null);
		assert.equal(feedback.errorMessage, "OCR record not found");
		assert.equal(feedback.savedOcr, null);
	});
});
