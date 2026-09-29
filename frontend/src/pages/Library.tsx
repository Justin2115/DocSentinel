import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import SearchIcon from "@mui/icons-material/Search";
import FilterListIcon from "@mui/icons-material/FilterList";
import PictureAsPdfIcon from "@mui/icons-material/PictureAsPdf";
import ImageIcon from "@mui/icons-material/Image";
import DescriptionIcon from "@mui/icons-material/Description";
import TableChartIcon from "@mui/icons-material/TableChart";
import VisibilityOutlinedIcon from "@mui/icons-material/VisibilityOutlined";
import DeleteIcon from "@mui/icons-material/Delete";
import CloseIcon from "@mui/icons-material/Close";
import KeyboardArrowDownIcon from "@mui/icons-material/KeyboardArrowDown";

import {
	deleteDocument,
	getDocument,
	getDocuments,
} from "../api/document";
import { searchDocuments, searchSemantic } from "../api/search";
import type { DocumentDetail, DocumentRecord } from "../api/document";
import SearchResults from "../components/search/SearchResults";
import type { SearchResultHit } from "../components/search/SearchResults";

type DocumentItem = DocumentRecord;
type SearchMode = "keyword" | "semantic";

type DisplayType =
	| "PDF"
	| "DOCX"
	| "JPG"
	| "PNG"
	| "XLSX"
	| "DOC"
	| "XLS";

const displayType = (document: DocumentItem): DisplayType => {
	const extension =
		document.original_filename.split(".").pop()?.toUpperCase() || "DOC";

	return ["PDF", "DOCX", "JPG", "PNG", "XLSX", "DOC", "XLS"].includes(
		extension
	)
		? (extension as DisplayType)
		: "DOC";
};

const displayStatus = (
	status: string | null
): "Processed" | "Needs review" | "Indexed" | "Failed" => {
	if (status === "needs_review") return "Needs review";
	if (status === "indexed") return "Indexed";
	if (status === "failed") return "Failed";
	return "Processed";
};

const formatSize = (bytes: number | null) =>
	bytes == null
		? "--"
		: bytes < 1024 * 1024
			? `${(bytes / 1024).toFixed(1)} KB`
			: `${(bytes / 1024 / 1024).toFixed(1)} MB`;

const formatDate = (date: string | null) =>
	date ? new Date(date).toLocaleDateString() : "--";

const ocrPageLabel = (
	ocr: DocumentDetail["ocr_results"][number],
	pages: DocumentDetail["pages"]
) => {
	const page = pages.find((item) => item.id === ocr.page_id);
	return page ? `Page ${page.page_number}` : "Document";
};

const Library = () => {
	const [searchParams, setSearchParams] = useSearchParams();
	const [search, setSearch] = useState(searchParams.get("q") || "");
	const [debouncedSearch, setDebouncedSearch] = useState(search.trim());
	const [searchMode, setSearchMode] = useState<SearchMode>("keyword");
	const [typeFilter, setTypeFilter] = useState("All types");
	const [statusFilter, setStatusFilter] = useState("All status");
	const [selectedDocument, setSelectedDocument] =
		useState<DocumentDetail | null>(null);
	const [documents, setDocuments] = useState<DocumentItem[]>([]);
	const [hits, setHits] = useState<SearchResultHit[]>([]);
	const [total, setTotal] = useState(0);
	const [page, setPage] = useState(1);
	const [loading, setLoading] = useState(true);
	const [searchLoading, setSearchLoading] = useState(false);
	const [error, setError] = useState("");
	const [deletingId, setDeletingId] = useState<number | null>(null);
	const [previewLoading, setPreviewLoading] = useState(false);
	const [previewError, setPreviewError] = useState("");
	const pageSize = 10;
	const showingSearch = debouncedSearch.length > 0;

	useEffect(() => {
		const timer = window.setTimeout(() => {
			setDebouncedSearch(search.trim());
		}, 300);
		return () => window.clearTimeout(timer);
	}, [search]);

	useEffect(() => {
		const query = searchParams.get("q");
		if (query && query !== search) {
			setSearch(query);
		}
		const previewId = searchParams.get("preview");
		if (previewId) {
			openDocumentById(Number(previewId));
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, []);

	useEffect(() => {
		setPage(1);
	}, [debouncedSearch, typeFilter, statusFilter, searchMode]);

	useEffect(() => {
		if (!selectedDocument) {
			return;
		}

		const html = document.documentElement;
		const previousHtmlOverflow = html.style.overflow;
		const previousBodyOverflow = document.body.style.overflow;
		html.classList.add("previewOpen");
		document.body.classList.add("previewOpen");
		html.style.overflow = "hidden";
		document.body.style.overflow = "hidden";

		return () => {
			html.classList.remove("previewOpen");
			document.body.classList.remove("previewOpen");
			html.style.overflow = previousHtmlOverflow;
			document.body.style.overflow = previousBodyOverflow;
		};
	}, [selectedDocument]);

	useEffect(() => {
		setLoading(true);
		setSearchLoading(showingSearch);
		setError("");

		if (showingSearch && searchMode === "semantic") {
			searchSemantic(debouncedSearch)
				.then((response) => {
					setHits(
						response.data.items.map((hit) => ({
							...hit,
							page_number: hit.page_number,
						}))
					);
					setTotal(response.data.total);
					setDocuments([]);
				})
				.catch(() =>
					setError("Unable to run semantic search.")
				)
				.finally(() => {
					setLoading(false);
					setSearchLoading(false);
				});
			return;
		}

		if (showingSearch && searchMode === "keyword") {
			searchDocuments(
				debouncedSearch,
				(page - 1) * pageSize,
				pageSize
			)
				.then((response) => {
					setHits(response.data.items);
					setTotal(response.data.total);
					setDocuments([]);
				})
				.catch(() =>
					setError("Unable to search documents.")
				)
				.finally(() => {
					setLoading(false);
					setSearchLoading(false);
				});
			return;
		}

		const status =
			statusFilter === "All status"
				? undefined
				: statusFilter === "Processed"
					? "completed"
					: statusFilter === "Needs review"
						? "needs_review"
						: statusFilter === "Indexed"
							? "indexed"
							: undefined;

		getDocuments({
			status,
			skip: (page - 1) * pageSize,
			limit: pageSize,
		})
			.then((response) => {
				const items =
					typeFilter === "All types"
						? response.data.items
						: response.data.items.filter(
								(document) =>
									displayType(document) === typeFilter
							);
				setDocuments(items);
				setHits([]);
				setTotal(
					typeFilter === "All types"
						? response.data.total
						: items.length
				);
			})
			.catch(() =>
				setError("Unable to load documents from the server.")
			)
			.finally(() => {
				setLoading(false);
				setSearchLoading(false);
			});
	}, [
		debouncedSearch,
		typeFilter,
		statusFilter,
		page,
		searchMode,
		showingSearch,
	]);

	const openDocumentById = async (id: number) => {
		setPreviewLoading(true);
		setPreviewError("");
		try {
			const response = await getDocument(id);
			setSelectedDocument(response.data);
		} catch {
			setPreviewError("Unable to load document details.");
			setSelectedDocument({
				id,
				original_filename: "Document",
				stored_filename: "",
				file_path: "",
				file_type: "",
				file_size: null,
				document_type: null,
				status: null,
				overall_confidence: null,
				uploaded_at: null,
				processed_at: null,
				pages: [],
				ocr_results: [],
				extracted_fields: [],
				review_items: [],
			});
		} finally {
			setPreviewLoading(false);
		}
	};

	const removeDocument = async (id: number) => {
		setDeletingId(id);
		try {
			await deleteDocument(id);
			setDocuments((current) =>
				current.filter((document) => document.id !== id)
			);
			setHits((current) =>
				current.filter((hit) => hit.document_id !== id)
			);
			setTotal((current) => Math.max(0, current - 1));
			setSelectedDocument(null);
		} catch {
			setError("Unable to delete this document.");
		} finally {
			setDeletingId(null);
		}
	};

	const openDocument = async (document: DocumentItem) => {
		setPreviewLoading(true);
		setPreviewError("");
		setSelectedDocument({
			...document,
			pages: [],
			ocr_results: [],
			extracted_fields: [],
			review_items: [],
		});
		try {
			const response = await getDocument(document.id);
			setSelectedDocument(response.data);
		} catch {
			setPreviewError("Unable to load document details.");
		} finally {
			setPreviewLoading(false);
		}
	};

	const getFileIcon = (type: DisplayType) => {
		switch (type) {
			case "PDF":
				return <PictureAsPdfIcon />;
			case "JPG":
			case "PNG":
				return <ImageIcon />;
			case "XLSX":
				return <TableChartIcon />;
			default:
				return <DescriptionIcon />;
		}
	};

	const updateSearch = (value: string) => {
		setSearch(value);
		const next = new URLSearchParams(searchParams);
		if (value.trim()) {
			next.set("q", value.trim());
		} else {
			next.delete("q");
		}
		setSearchParams(next, { replace: true });
	};

	return (
		<div className="libraryPage">
			<div className="libraryHeader">
				<div>
					<h1>Document Library</h1>
					<p>Manage, search and review all your documents.</p>
				</div>
				<div className="libraryCount">
					<strong>{total}</strong>
					<span>{showingSearch ? "matches" : "documents"}</span>
				</div>
			</div>

			<div className="libraryToolbar">
				<div className="librarySearchCluster">
					<div className="searchModeToggle" role="tablist">
						<button
							type="button"
							className={searchMode === "keyword" ? "active" : ""}
							onClick={() => setSearchMode("keyword")}
						>
							Keyword
						</button>
						<button
							type="button"
							className={searchMode === "semantic" ? "active" : ""}
							onClick={() => setSearchMode("semantic")}
						>
							Semantic
						</button>
					</div>
					<div className="librarySearch">
						<SearchIcon />
						<input
							type="text"
							placeholder={
								searchMode === "semantic"
									? "Search by meaning..."
									: "Search documents..."
							}
							value={search}
							onChange={(event) => updateSearch(event.target.value)}
						/>
					</div>
				</div>

				<div className="filterGroup">
					<div className="filterSelect">
						<FilterListIcon />
						<select
							value={typeFilter}
							onChange={(event) => setTypeFilter(event.target.value)}
							disabled={showingSearch}
						>
							<option>All types</option>
							<option>PDF</option>
							<option>DOCX</option>
							<option>JPG</option>
							<option>PNG</option>
							<option>XLSX</option>
						</select>
						<KeyboardArrowDownIcon />
					</div>
					<div className="filterSelect">
						<select
							value={statusFilter}
							onChange={(event) => setStatusFilter(event.target.value)}
							disabled={showingSearch}
						>
							<option>All status</option>
							<option>Processed</option>
							<option>Needs review</option>
							<option>Indexed</option>
						</select>
						<KeyboardArrowDownIcon />
					</div>
				</div>
			</div>

			{showingSearch ? (
				<SearchResults
					hits={hits}
					query={debouncedSearch}
					loading={searchLoading}
					emptyMessage={
						error
							? error
							: searchMode === "semantic"
								? "No semantic match. Try another phrasing, or wait until documents are indexed."
								: "No keyword match. Try a different word or check spelling."
					}
					onOpenDocument={openDocumentById}
				/>
			) : (
				<div className="libraryTableCard">
					<div className="libraryTableHeader">
						<h2>All Documents</h2>
						<span>{total} results</span>
					</div>
					<div className="libraryTableWrapper">
						<table className="libraryTable">
							<thead>
								<tr>
									<th>NAME</th>
									<th>TYPE</th>
									<th>DATE</th>
									<th>STATUS</th>
									<th>CONFIDENCE</th>
									<th>ACTIONS</th>
								</tr>
							</thead>
							<tbody>
								{loading ? (
									<tr>
										<td colSpan={6}>Loading documents...</td>
									</tr>
								) : error ? (
									<tr>
										<td colSpan={6}>{error}</td>
									</tr>
								) : documents.length > 0 ? (
									documents.map((document) => (
										<tr key={document.id}>
											<td>
												<div className="documentName">
													<div className="documentIcon">
														{getFileIcon(displayType(document))}
													</div>
													<div>
														<strong>{document.original_filename}</strong>
														<span>{formatSize(document.file_size)}</span>
													</div>
												</div>
											</td>
											<td>
												<span className="fileType">
													{displayType(document)}
												</span>
											</td>
											<td>{formatDate(document.uploaded_at)}</td>
											<td>
												<span
													className={`statusBadge ${displayStatus(
														document.status
													)
														.toLowerCase()
														.replace(" ", "-")}`}
												>
													{displayStatus(document.status)}
												</span>
											</td>
											<td>
												<span className="confidence">
													{document.overall_confidence != null
														? `${Number(document.overall_confidence).toFixed(1)}%`
														: "--"}
												</span>
											</td>
											<td>
												<div className="documentActions">
													<button
														className="iconAction"
														title="Preview"
														onClick={() => openDocument(document)}
													>
														<VisibilityOutlinedIcon />
													</button>
													<button
														className="iconAction deleteAction"
														title="Delete"
														disabled={deletingId === document.id}
														onClick={() => removeDocument(document.id)}
													>
														<DeleteIcon />
													</button>
												</div>
											</td>
										</tr>
									))
								) : (
									<tr>
										<td colSpan={6}>
											<div className="emptyLibrary">
												<SearchIcon />
												<h3>No documents found</h3>
												<p>Try changing your search or filters.</p>
											</div>
										</td>
									</tr>
								)}
							</tbody>
						</table>
					</div>
					<div className="libraryFooter">
						<span>
							Showing {documents.length} of {total} documents
						</span>
						<div className="pagination">
							<button
								disabled={page === 1}
								onClick={() => setPage((current) => current - 1)}
							>
								Previous
							</button>
							<button className="pageActive">{page}</button>
							<button
								disabled={page * pageSize >= total}
								onClick={() => setPage((current) => current + 1)}
							>
								Next
							</button>
						</div>
					</div>
				</div>
			)}

			{selectedDocument && (
				<div
					className="previewOverlay"
					onClick={() => setSelectedDocument(null)}
					onWheel={(event) => event.stopPropagation()}
					onTouchMove={(event) => event.stopPropagation()}
				>
					<div
						className="previewModal"
						onClick={(event) => event.stopPropagation()}
					>
						<div className="previewHeader">
							<div>
								<h2>Document Preview</h2>
								<p>{selectedDocument.original_filename}</p>
							</div>
							<button
								className="previewClose"
								onClick={() => setSelectedDocument(null)}
							>
								<CloseIcon />
							</button>
						</div>
						<div className="previewContent">
							{previewLoading ? (
								<div className="previewPlaceholder">
									<DescriptionIcon />
									<span>Loading document details...</span>
								</div>
							) : previewError ? (
								<div className="previewPlaceholder">
									<DescriptionIcon />
									<span>{previewError}</span>
								</div>
							) : (
								<>
									<div className="previewFileIcon">
										{getFileIcon(displayType(selectedDocument))}
									</div>
									<h3>{selectedDocument.original_filename}</h3>
									<p>
										{displayType(selectedDocument)} ·{" "}
										{formatSize(selectedDocument.file_size)}
									</p>
									<div className="previewDetails">
										<div>
											<strong>Status</strong>
											<span>{displayStatus(selectedDocument.status)}</span>
										</div>
										<div>
											<strong>Confidence</strong>
											<span>
												{selectedDocument.overall_confidence != null
													? `${Number(selectedDocument.overall_confidence).toFixed(1)}%`
													: "--"}
											</span>
										</div>
										<div>
											<strong>OCR Results</strong>
											<span>{selectedDocument.ocr_results.length}</span>
										</div>
										<div>
											<strong>Extracted Fields</strong>
											<span>
												{selectedDocument.extracted_fields.length}
											</span>
										</div>
									</div>
									{selectedDocument.ocr_results.length > 0 && (
										<div className="previewOCR">
											<h4>OCR Content</h4>
											{selectedDocument.ocr_results.map((ocr) => (
												<div className="ocrResult" key={ocr.id}>
													<div className="ocrResultHeader">
														<span>
															{ocrPageLabel(
																ocr,
																selectedDocument.pages
															)}
														</span>
														<span>{ocr.ocr_engine ?? "OCR"}</span>
													</div>
													<p>
														{ocr.extracted_text ||
															"No OCR text available."}
													</p>
												</div>
											))}
										</div>
									)}
									{selectedDocument.extracted_fields.length > 0 && (
										<div className="previewOCR">
											<h4>Extracted Fields</h4>
											{selectedDocument.extracted_fields.map((field) => (
												<div className="ocrResult" key={field.id}>
													<div className="ocrResultHeader">
														<strong>{field.field_name}</strong>
														<span>
															{field.confidence != null
																? `${Number(field.confidence).toFixed(1)}%`
																: "--"}
														</span>
													</div>
													<p>
														{field.corrected_value ||
															field.field_value ||
															"No value"}
													</p>
												</div>
											))}
										</div>
									)}
								</>
							)}
						</div>
						<div className="previewFooter">
							<button
								className="previewCancel"
								onClick={() => setSelectedDocument(null)}
							>
								Close
							</button>
						</div>
					</div>
				</div>
			)}
		</div>
	);
};

export default Library;
