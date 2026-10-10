import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";

export type SearchResultHit = {
	document_id: number;
	document_name: string;
	page_number: number | null;
	snippet: string;
	match_field?: string;
	score?: number | null;
	similarity?: number;
	match_type?: string;
	chunk_index?: number;
	highlight_terms?: string[];
};

type SearchResultsProps = {
	hits: SearchResultHit[];
	query?: string;
	loading?: boolean;
	emptyMessage: string;
	onOpenDocument?: (documentId: number) => void;
};

const escapeRegExp = (value: string) =>
	value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

const collectHighlightTerms = (query: string, extra: string[] = []): string[] => {
	const trimmed = query.trim();
	const terms = new Set<string>();

	if (trimmed) {
		terms.add(trimmed);
		for (const token of trimmed.split(/[^\p{L}\p{N}]+/u)) {
			if (token.length >= 2) {
				terms.add(token);
			}
		}
	}

	for (const term of extra) {
		const value = term.trim();
		if (value.length >= 2) {
			terms.add(value);
		}
	}

	return [...terms].sort((a, b) => b.length - a.length);
};

const SNIPPET_RADIUS = 140;

const earliestTermIndex = (text: string, terms: string[]): { index: number; length: number } => {
	const haystack = text.toLocaleLowerCase();
	let index = -1;
	let length = 0;

	for (const term of terms) {
		const needle = term.toLocaleLowerCase();
		const found = haystack.indexOf(needle);
		if (found >= 0 && (index < 0 || found < index)) {
			index = found;
			length = term.length;
		}
	}

	return { index, length };
};

const clipSnippetToMatch = (
	text: string,
	query: string,
	extraTerms: string[] = []
): string => {
	if (!text) {
		return text;
	}

	const queryTerms = collectHighlightTerms(query);
	const { index: queryIndex, length: queryLength } = earliestTermIndex(
		text,
		queryTerms
	);
	const { index: extraIndex, length: extraLength } = earliestTermIndex(
		text,
		extraTerms.map((term) => term.trim()).filter((term) => term.length >= 2)
	);

	const index = queryIndex >= 0 ? queryIndex : extraIndex;
	const matchLength = queryIndex >= 0 ? queryLength : extraLength;

	if (index < 0) {
		const leading = text.slice(0, SNIPPET_RADIUS * 2).trim();
		return leading.length < text.trim().length ? `${leading}…` : text;
	}

	const matchEnd = index + Math.max(matchLength, 1);
	const previousBreak = text.lastIndexOf("\n", index);
	let lineStart = previousBreak < 0 ? 0 : previousBreak + 1;
	if (lineStart > 0) {
		const priorBreak = text.lastIndexOf("\n", lineStart - 1);
		lineStart = priorBreak < 0 ? 0 : priorBreak + 1;
	}

	let lineEnd = text.indexOf("\n", matchEnd);
	if (lineEnd < 0) {
		lineEnd = text.length;
	} else {
		const nextBreak = text.indexOf("\n", lineEnd + 1);
		lineEnd = nextBreak < 0 ? text.length : nextBreak;
	}

	let window = text.slice(lineStart, lineEnd).trim();
	if (window.length > SNIPPET_RADIUS * 3) {
		const start = Math.max(0, index - SNIPPET_RADIUS);
		const end = Math.min(text.length, matchEnd + SNIPPET_RADIUS);
		window = text.slice(start, end).trim();
		const prefix = start > 0 ? "…" : "";
		const suffix = end < text.length ? "…" : "";
		return `${prefix}${window}${suffix}`;
	}

	const prefix = lineStart > 0 ? "…" : "";
	const suffix = lineEnd < text.length ? "…" : "";
	return `${prefix}${window}${suffix}`;
};

const highlightText = (
	text: string,
	query: string,
	extraTerms: string[] = []
): ReactNode => {
	if (!text) {
		return text;
	}

	const terms = collectHighlightTerms(query, extraTerms);
	if (terms.length === 0) {
		return text;
	}

	const pattern = new RegExp(`(${terms.map(escapeRegExp).join("|")})`, "giu");
	const parts = text.split(pattern);

	return parts.map((part, index) => {
		if (!part) {
			return null;
		}

		const isMatch = terms.some(
			(term) => part.toLocaleLowerCase() === term.toLocaleLowerCase()
		);

		if (isMatch) {
			return (
				<mark className="searchHighlight" key={`${part}-${index}`}>
					{part}
				</mark>
			);
		}

		return <span key={`${part}-${index}`}>{part}</span>;
	});
};

const SearchResults = ({
	hits,
	query = "",
	loading = false,
	emptyMessage,
	onOpenDocument,
}: SearchResultsProps) => {
	const navigate = useNavigate();

	if (loading) {
		return (
			<div className="searchResultsCard">
				<p className="searchResultsStatus">Searching documents...</p>
			</div>
		);
	}

	if (hits.length === 0) {
		return (
			<div className="searchResultsCard">
				<div className="emptyLibrary">
					<h3>No matches</h3>
					<p>{emptyMessage}</p>
				</div>
			</div>
		);
	}

	return (
		<div className="searchResultsCard">
			<ul className="searchResultsList">
				{hits.map((hit, index) => (
					<li
						key={`${hit.document_id}-${hit.page_number}-${hit.chunk_index ?? index}`}
						className="searchHit"
					>
						<button
							type="button"
							className="searchHitButton"
							onClick={() => {
								if (onOpenDocument) {
									onOpenDocument(hit.document_id);
									return;
								}
								navigate(`/library?preview=${hit.document_id}`);
							}}
						>
							<div className="searchHitHeader">
								<strong>
									{highlightText(
										hit.document_name,
										query,
										hit.highlight_terms
									)}
								</strong>
								{hit.similarity != null && (
									<span className="similarityBadge">
										Score: {hit.similarity.toFixed(2)}
									</span>
								)}
							</div>
							<div className="searchHitMeta">
								{hit.page_number != null && (
									<span>Page {hit.page_number}</span>
								)}
								{hit.match_field && <span>{hit.match_field}</span>}
							</div>
							<p className="searchHitSnippet">
								{highlightText(
									clipSnippetToMatch(
										hit.snippet,
										query,
										hit.highlight_terms
									),
									query,
									hit.highlight_terms
								)}
							</p>
						</button>
					</li>
				))}
			</ul>
		</div>
	);
};

export default SearchResults;
