import apiClient from "./client";
import { hasDevanagari, normalizeSearchQuery } from "../utils/searchQuery";

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
	limit = 20,
	options?: {
		mode?: "hybrid" | "keyword" | "semantic";
		signal?: AbortSignal;
	}
) =>
	apiClient.get<SearchResponse>("/search", {
		params: { q, skip, limit, mode: options?.mode ?? "keyword" },
		signal: options?.signal,
	});

export const searchSemantic = (
	q: string,
	topK = 10,
	minScore = 0.2,
	signal?: AbortSignal
) => {
	const query = normalizeSearchQuery(q);
	const effectiveMinScore =
		minScore > 0 && hasDevanagari(query)
			? Math.min(minScore, 0.15)
			: minScore;

	return apiClient.get<SemanticSearchResponse>("/search/semantic", {
		params: {
			q: query,
			top_k: topK,
			min_score: effectiveMinScore,
		},
		signal,
	});
};
