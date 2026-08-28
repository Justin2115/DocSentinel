import apiClient from "./axios";

export interface DocumentRecord {
	id: number;
	original_filename: string;
	stored_filename: string;
	file_path: string;
	file_type: string;
	file_size: number | null;
	document_type: string | null;
	status: string | null;
	overall_confidence: number | null;
	uploaded_at: string | null;
	processed_at: string | null;
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
	file_type?: string;
	status?: string;
}

export const getDocuments = (params: DocumentQuery = {}) =>
	apiClient.get<DocumentListResponse>("/documents", { params });

export const getDocument = (id: number) =>
	apiClient.get<DocumentRecord>(`/documents/${id}`);

export const deleteDocument = (id: number) =>
	apiClient.delete(`/documents/${id}`);

export const getDashboardStats = () =>
	apiClient.get<DashboardStats>("/documents/stats");

export const uploadDocument = (file: File, language: string) => {
	const formData = new FormData();
	formData.append("file", file);
	formData.append("language", language);
	return apiClient.post<DocumentRecord>("/documents/upload", formData);
};
