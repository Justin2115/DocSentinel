import apiClient, { API_BASE_URL } from "./client";

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

export interface ConversationSummary {
	id: string;
	title: string;
	chat_mode: "documents" | "general";
	created_at: string;
	updated_at: string;
	last_message_at: string | null;
}

export interface StoredChatMessage {
	id: number;
	role: "user" | "assistant";
	content: string;
	status: "completed" | "failed" | "interrupted";
	model_id: string | null;
	usage_metadata: { has_evidence?: boolean; confidence?: number; engine?: string; notes?: string } | null;
	citation_metadata: SourceCitation[] | null;
	request_metadata: { mode?: "documents" | "general"; document_ids?: number[] } | null;
	error_metadata: { message?: string } | null;
	generation_ms: number | null;
	created_at: string;
}

export interface ConversationDetail extends ConversationSummary {
	messages: StoredChatMessage[];
	next_before_id: number | null;
}

export const listConversations = () => apiClient.get<ConversationSummary[]>("/conversations");
export const createConversation = (title?: string, mode: "documents" | "general" = "documents") => apiClient.post<ConversationSummary>("/conversations", { title, mode });
export const getConversation = (id: string, limit = 50, beforeId?: number) =>
	apiClient.get<ConversationDetail>(`/conversations/${encodeURIComponent(id)}`, { params: { limit, before_id: beforeId } });
export const renameConversation = (id: string, title: string) =>
	apiClient.patch<ConversationSummary>(`/conversations/${encodeURIComponent(id)}`, { title });
export const deleteConversation = (id: string) => apiClient.delete(`/conversations/${encodeURIComponent(id)}`);
export const getProviderStatus = () => apiClient.get<{
	enabled: boolean; reachable: boolean; model_installed: boolean; model: string; reason: string;
}>("/conversations/provider/status");

export interface ChatStreamEvent {
	type: "accepted" | "status" | "delta" | "complete" | "error";
	data: Record<string, unknown>;
}

export async function streamConversationMessage(
	conversationId: string,
	content: string,
	documentIds: number[] | undefined,
	requestId: string,
	mode: "documents" | "general",
	signal: AbortSignal,
	onEvent: (event: ChatStreamEvent) => void,
) {
	const token = localStorage.getItem("auth_token");
	const response = await fetch(
		`${API_BASE_URL}/api/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
		{
			method: "POST",
			signal,
			headers: {
				"Content-Type": "application/json",
				Accept: "text/event-stream",
				...(token ? { Authorization: `Bearer ${token}` } : {}),
			},
			body: JSON.stringify({ content, document_ids: documentIds, request_id: requestId, mode }),
		},
	);
	if (!response.ok) {
		let detail = `Chat request failed (HTTP ${response.status}).`;
		try {
			const body = await response.json() as { detail?: string };
			detail = body.detail || detail;
		} catch { /* retain the HTTP status message */ }
		const error = new Error(detail) as Error & { status?: number };
		error.status = response.status;
		if (response.status === 401) {
			localStorage.removeItem("auth_token");
			window.location.href = "/login?error=Session%20expired.%20Please%20log%20in%20again.";
		}
		throw error;
	}
	if (!response.body) throw new Error("The chat stream was not available.");

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	let completed = false;
	const dispatch = (frame: string) => {
		let type = "message";
		const dataLines: string[] = [];
		for (const line of frame.split(/\r?\n/)) {
			if (line.startsWith("event:")) type = line.slice(6).trim();
			if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
		}
		if (!dataLines.length) return;
		const data = JSON.parse(dataLines.join("\n")) as Record<string, unknown>;
		if (type === "complete") completed = true;
		onEvent({ type: type as ChatStreamEvent["type"], data });
	};
	while (true) {
		const { value, done } = await reader.read();
		buffer += decoder.decode(value, { stream: !done });
		let separator: RegExpExecArray | null;
		const frameBreak = /\r?\n\r?\n/g;
		while ((separator = frameBreak.exec(buffer)) !== null) {
			const frame = buffer.slice(0, separator.index);
			buffer = buffer.slice(separator.index + separator[0].length);
			frameBreak.lastIndex = 0;
			dispatch(frame);
		}
		if (done) break;
	}
	if (buffer.trim()) dispatch(buffer);
	if (!completed && !signal.aborted) throw new Error("The response stream ended before the answer was saved.");
}
