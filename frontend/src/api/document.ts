import apiClient from "./client";

export interface DocumentRecord {
	id: number;
	original_filename: string;
	stored_filename: string;
	file_path: string;
	file_type: string;
	file_size: number | null;
	document_type: string | null;
	status: string | null;
	uploaded_by?: number | null;
	overall_confidence: number | null;
	uploaded_at: string | null;
	processed_at: string | null;
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

export interface DocumentDetail
	extends DocumentRecord {
	pages: DocumentPage[];
	ocr_results: OCRResult[];
	extracted_fields: ExtractedField[];
	review_items: ReviewQueueItem[];
}

export interface DocumentListResponse {
	items: DocumentRecord[];
	total: number;
	skip: number;
	limit: number;
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
	language: string
) => {
	const formData = new FormData();

	formData.append(
		"file",
		file
	);

	formData.append(
		"language",
		language
	);

	return apiClient.post<DocumentRecord>(
		"/documents/upload",
		formData
	);
};