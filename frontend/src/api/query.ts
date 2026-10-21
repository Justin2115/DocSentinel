import apiClient from "./client";

export interface SourceCitation {
	document_id: number;
	document_name: string;
	page_number: number | null;
	snippet: string;
	/** Raw cosine retrieval score, or null when the passage never went through
	 *  the vector store (lexical or table evidence has no similarity to show). */
	similarity: number | null;
	/** How this passage earned its place: semantic, lexical, table, aggregate... */
	support?: string | null;
	source_kind?: string | null;
	table_columns?: string[] | null;
	line_index?: number | null;
	/** Set when the passage holds a value the OCR could not verify. */
	value_state?: string | null;
}

export const supportLabels: Record<string, string> = {
	semantic: "Semantic match",
	lexical: "Exact wording",
	anchored_lexical: "Exact wording",
	anchored_fuzzy: "Close OCR match",
	table: "Table row",
	aggregate: "Table totals",
	// The question asked what the document is about, so the front of the selected
	// document was read instead of a passage matching its wording.
	front_matter: "Opening lines",
	none: "Uncited",
};

export const sourceKindLabels: Record<string, string> = {
	chunk: "Passage",
	line_window: "Lines",
	table_row: "Table row",
	table_header: "Table",
	entry: "List entry",
	front_matter: "Page front",
};

/** States that mean the OCR could not produce a trustworthy number. */
export const valueStateLabels: Record<string, string> = {
	corrupted: "OCR digits corrupted",
	unknown: "OCR value unreadable",
};

export interface ChatMessage {
	role: string;
	content: string;
}

export interface ChatQueryRequest {
	question: string;
	document_id?: number;
	/** Optional scope from the "+" selector; absent means the whole library. */
	document_ids?: number[];
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
