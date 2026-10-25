import apiClient from "./client";

export interface DocumentRecord {
	id: number;
	original_filename: string;
	stored_filename: string;
	file_path: string;
	file_type: string;
	file_size: number | null;
	document_type: string | null;
	department?: string | null;
	status: string | null;
	uploaded_by?: number | null;
	assigned_checker?: number | null;
	overall_confidence: number | null;
	uploaded_at: string | null;
	created_at?: string | null;
	updated_at?: string | null;
	processed_at: string | null;
	uploader_name?: string | null;
	checker_name?: string | null;
}

export interface DocumentPage {
	id: number;
	document_id: number;
	page_number: number;
	page_path: string | null;
	width: number | null;
	height: number | null;
}

export interface OCRResult {
	id: number;
	document_id: number;
	page_id: number | null;
	extracted_text: string | null;
	raw_text?: string | null;
	confidence: number | null;
	ocr_engine: string | null;
	processed_at: string | null;
}

export interface ExtractedField {
	id: number;
	document_id: number;
	page_id: number | null;
	field_name: string;
	field_value: string | null;
	confidence: number | null;
	original_value: string | null;
	corrected_value: string | null;
	is_verified: boolean | null;
	created_at: string | null;
}

export interface ReviewQueueItem {
	id: number;
	document_id: number;
	reason: string | null;
	confidence: number | null;
	status: string | null;
	assigned_to: number | null;
	reviewed_by: number | null;
	reviewed_at: string | null;
	created_at: string | null;
}

// One cell exactly as the engine measured it. `bbox` is in the page image's own
// pixel space, `confidence` is null when nothing was measured (never a score of
// 0), and `uncertain` means the reader could not tell what belongs in this cell.
export interface ExtractedTableCell {
	row: number;
	col: number;
	text: string;
	bbox: number[] | null;
	colspan: number;
	rowspan: number;
	header: boolean;
	confidence: number | null;
	uncertain: boolean;
	// The page's own recognised text fell inside this cell's box. Without it the
	// cell holds a reading nothing on the page confirmed.
	from_page: boolean;
}

export interface ExtractedTable {
	id: number;
	document_id: number;
	page_id: number | null;
	page_number: number;
	row_count: number | null;
	col_count: number | null;
	bbox: number[] | null;
	header_rows: number[];
	engine: string | null;
	source: string;
	confidence: number | null;
	uncertain_cells: number;
	requires_review: boolean;
	note: string | null;
	page_backed_cells: number;
	// "page" or "corrected": which image the cell boxes belong to. Boxes in the
	// "corrected" frame must not be drawn over the displayed page.
	frame: string;
	cells: ExtractedTableCell[];
}

/**
 * Whether this record is a table the reader measured or a grid it refused.
 *
 * A refused grid arrives with the shape it measured, no cells at all and an
 * explanation: rendering it as a table would show an empty grid and hide the page's
 * own recognised text, which is exactly what the reader keeps for the reviewer.
 */
export const isRejectedTable = (table: ExtractedTable): boolean =>
	table.cells.length === 0;

/**
 * Whether a page's stored records include a grid that can actually be drawn.
 *
 * Callers use this to decide whether to take the "cell | cell" lines out of the
 * page's text: only a grid that is rendered may replace them.
 */
export const pageHasRenderableTable = (tables: ExtractedTable[]): boolean =>
	tables.some((table) => !isRejectedTable(table));

export interface DocumentDetail
	extends DocumentRecord {
	pages: DocumentPage[];
	ocr_results: OCRResult[];
	extracted_fields: ExtractedField[];
	extracted_tables: ExtractedTable[];
	review_items: ReviewQueueItem[];
}

export interface DocumentListResponse {
	items: DocumentRecord[];
	total: number;
	skip: number;
	limit: number;
}

// Mirrors DocumentProcessingResponse. Counts are read back from rows the
// pipeline already committed, so nulls stay null instead of becoming guesses.
export interface DocumentProcessing {
	document_id: number;
	status: string;
	stage:
		| "waiting"
		| "reading_text"
		| "extracting_fields"
		| "making_searchable"
		| "done"
		| "failed";
	is_running: boolean;
	pages_total: number | null;
	pages_completed: number;
	pages_failed: number;
	extracted_field_count: number;
	indexed_chunks: number;
	searchable: boolean;
	overall_confidence: number | null;
	elapsed_seconds: number | null;
	timeout_seconds: number;
	timed_out: boolean;
	message: string;
	error: string | null;
}

export interface DashboardStats {
	total_documents: number;
	processed_today: number;
	pending_review: number;
	average_confidence: number | null;
	recent_documents: DocumentRecord[];
}

export interface DocumentQuery {
	skip?: number;
	limit?: number;
	search?: string;
	department?: string;
	document_type?: string;
	file_type?: string;
	status?: string;
}

export const getDocuments = (
	params: DocumentQuery = {},
	signal?: AbortSignal
) =>
	apiClient.get<DocumentListResponse>(
		"/documents",
		{
			params,
			signal,
		}
	);

export const getDocument = (
	id: number
) =>
	apiClient.get<DocumentDetail>(
		`/documents/${id}`
	);

// The list endpoint rejects a page size above 100 with HTTP 422.
const MAX_PAGE_SIZE = 100;

// Collects every document the caller may see, paging with skip/limit until the
// server's own total is covered.
export const getAllDocuments = async (
	signal?: AbortSignal
): Promise<DocumentRecord[]> => {
	const first = await getDocuments(
		{ skip: 0, limit: MAX_PAGE_SIZE },
		signal
	);
	const items = [...first.data.items];
	const total = first.data.total ?? items.length;

	// The first response fixes how many pages there are, so a server that
	// ignored skip could not loop here forever.
	for (let skip = MAX_PAGE_SIZE; skip < total; skip += MAX_PAGE_SIZE) {
		const response = await getDocuments(
			{ skip, limit: MAX_PAGE_SIZE },
			signal
		);
		if (response.data.items.length === 0) {
			break;
		}
		items.push(...response.data.items);
	}

	// A document can move between pages while the pages are being read.
	return [
		...new Map(
			items.map((document) => [document.id, document])
		).values(),
	];
};

export const deleteDocument = (
	id: number
) =>
	apiClient.delete(
		`/documents/${id}`
	);

export const getDashboardStats = () =>
	apiClient.get<DashboardStats>(
		"/documents/stats"
	);

export const uploadDocument = (
	file: File,
	department?: string,
	documentType?: string,
	// Bytes actually transferred, not a simulated bar. The server responds as
	// soon as the file is stored, so this stops at 100% and processing status
	// takes over from getDocumentProcessing.
	onProgress?: (percent: number) => void
) => {
	const formData = new FormData();
	formData.append("file", file);
	formData.append("language", "auto");

	if (department && department !== "auto") {
		formData.append("department", department);
	}
	if (documentType) {
		formData.append("document_type", documentType);
	}

	return apiClient.post<DocumentRecord>(
		"/documents/upload",
		formData,
		{
			onUploadProgress: (event) => {
				if (!onProgress) {
					return;
				}
				const total = event.total ?? file.size;
				onProgress(
					total > 0
						? Math.min(100, Math.round((event.loaded / total) * 100))
						: 0
				);
			},
		}
	);
};

// Poll target for an upload that is still being processed.
export const getDocumentProcessing = (
	id: number,
	signal?: AbortSignal
) =>
	apiClient.get<DocumentProcessing>(
		`/documents/${id}/processing`,
		{ signal }
	);

export const retryDocumentProcessing = (id: number) =>
	apiClient.post<DocumentRecord>(
		`/documents/${id}/retry`
	);

export const getReviewQueueDocuments = () =>
	apiClient.get<DocumentDetail[]>(
		"/documents/review/queue"
	);

export const approveDocument = (id: number) =>
	apiClient.post<DocumentRecord>(
		`/documents/${id}/approve`
	);

export const rejectDocument = (id: number, reason?: string) =>
	apiClient.post<DocumentRecord>(
		`/documents/${id}/reject`,
		{ reason }
	);

export const requestDocumentRevision = (id: number, notes?: string) =>
	apiClient.post<DocumentRecord>(
		`/documents/${id}/request-revision`,
		{ notes }
	);

export const assignDocumentChecker = (id: number, checkerId: number) =>
	apiClient.post<DocumentRecord>(
		`/documents/${id}/assign`,
		{ checker_id: checkerId }
	);

export const updateExtractedField = (
	documentId: number,
	fieldId: number,
	fieldValue: string
) =>
	apiClient.patch(
		`/documents/${documentId}/fields/${fieldId}`,
		{ field_value: fieldValue }
	);


export const updateDocumentOCRText = (
	documentId: number,
	ocrId: number,
	extractedText: string
) =>
	apiClient.patch<OCRResult>(
		`/documents/${documentId}/ocr/${ocrId}`,
		{ extracted_text: extractedText }
	);
