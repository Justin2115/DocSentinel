/** Normalize Unicode so IME / browser variants match indexed OCR text. */
export const normalizeSearchQuery = (value: string): string =>
	value.normalize("NFC").trim();

export const hasDevanagari = (value: string): boolean =>
	/[\u0900-\u097F]/.test(value);
