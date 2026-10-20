import apiClient from "./client";

export interface SearchHit {
	document_id: number;
	document_name: string;
	page_number: number | null;
	chunk_index: number | null;
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

export interface AnswerCitation {
	source_id: string;
	document_id: number;
	document_title: string;
	filename: string;
	page_number: number | null;
	excerpt: string;
	chunk_index: number | null;
	href: string;
}

export interface AnswerResponse {
	answer: string;
	citations: AnswerCitation[];
	evidence_status: "answered" | "insufficient_evidence" | "retrieved_only";
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
) =>
	apiClient.get<SemanticSearchResponse>("/search/semantic", {
		params: {
			q,
			top_k: topK,
			min_score: minScore,
		},
		signal,
	});

export const askDocuments = (
	question: string,
	includeCitations = true,
	topK = 8,
	signal?: AbortSignal
) =>
	apiClient.post<AnswerResponse>(
		"/search/ask",
		{ question, include_citations: includeCitations, top_k: topK },
		{ signal }
	);
