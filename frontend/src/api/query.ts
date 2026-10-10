import apiClient from "./client";

export interface SourceCitation {
	document_id: number;
	document_name: string;
	page_number: number | null;
	snippet: string;
	similarity: number;
}

export interface ChatMessage {
	role: string;
	content: string;
}

export interface ChatQueryRequest {
	question: string;
	document_id?: number;
	history?: ChatMessage[];
}

export interface ChatQueryResponse {
	answer: string;
	sources: SourceCitation[];
	confidence: number;
	question: string;
	has_evidence: boolean;
	engine: string;
	notes?: string;
}

export const askQuestion = (
	data: ChatQueryRequest,
	signal?: AbortSignal
) =>
	apiClient.post<ChatQueryResponse>("/query/ask", data, { signal });
