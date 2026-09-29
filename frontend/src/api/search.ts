import apiClient from "./client";

export interface SearchHit {
	document_id: number;
	document_name: string;
	page_number: number | null;
	snippet: string;
	match_field: string;
	score: number | null;
	highlight_terms?: string[];
}

export interface SemanticSearchHit {
	document_id: number;
	document_name: string;
	page_number: number;
	chunk_index: number;
	snippet: string;
	similarity: number;
	match_type: string;
	highlight_terms?: string[];
}

export interface SearchResponse {
	items: SearchHit[];
	total: number;
	skip: number;
	limit: number;
}

export interface SemanticSearchResponse {
	items: SemanticSearchHit[];
	total: number;
	query: string;
}

export const searchDocuments = (
	q: string,
	skip = 0,
	limit = 20
) =>
	apiClient.get<SearchResponse>("/search", {
		params: { q, skip, limit },
	});

export const searchSemantic = (
	q: string,
	topK = 10,
	minScore = 0.2
) =>
	apiClient.get<SemanticSearchResponse>("/search/semantic", {
		params: {
			q,
			top_k: topK,
			min_score: minScore,
		},
	});
