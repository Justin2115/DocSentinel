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
};

type SearchResultsProps = {
	hits: SearchResultHit[];
	loading?: boolean;
	emptyMessage: string;
	onOpenDocument?: (documentId: number) => void;
};

const highlightSnippet = (snippet: string) => snippet;

const SearchResults = ({
	hits,
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
								<strong>{hit.document_name}</strong>
								{hit.similarity != null && (
									<span className="similarityBadge">
										{Math.round(hit.similarity * 100)}% match
									</span>
								)}
							</div>
							<div className="searchHitMeta">
								{hit.page_number != null && (
									<span>Page {hit.page_number}</span>
								)}
								{hit.match_field && <span>{hit.match_field}</span>}
							</div>
							<p className="searchHitSnippet">{highlightSnippet(hit.snippet)}</p>
						</button>
					</li>
				))}
			</ul>
		</div>
	);
};

export default SearchResults;
