import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import type { ReactNode } from "react";

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

import { isRequestCanceled } from "../api/client";
import {
	deleteDocument,
	getDocument,
	getDocumentProcessing,
	getDocuments,
	pageHasRenderableTable,
	retryDocumentProcessing,
} from "../api/document";
import type {
	DocumentDetail,
	DocumentProcessing,
	DocumentQuery,
	DocumentRecord,
} from "../api/document";
import { searchDocuments } from "../api/search";
import { useAuth } from "../context/AuthContext";
import {
	CONFIDENCE_EXPLANATIONS,
	formatConfidence,
} from "../utils/confidence";
import SearchResults from "../components/search/SearchResults";
import type { SearchResultHit } from "../components/search/SearchResults";
import {
	PageStructuredTables,
	stripTableBlocks,
} from "../components/tables/StructuredTables";

type DocumentItem = DocumentRecord;

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

// A citation names a passage, not only a page. Marking the quoted wording keeps
// the viewer on the evidence the answer used; an absent or unmatched needle
// leaves the text exactly as the recognizer produced it.
const highlightPassage = (text: string, needle: string | null): ReactNode => {
	if (!text || !needle) {
		return text;
	}
	const want = needle.toLowerCase();
	const lower = text.toLowerCase();
	const parts: ReactNode[] = [];
	let from = 0;
	let at = lower.indexOf(want);
	if (at < 0) {
		return text;
	}
	while (at >= 0) {
		if (at > from) {
			parts.push(text.slice(from, at));
		}
		parts.push(
			<mark className="ocrHighlight" key={at}>
				{text.slice(at, at + want.length)}
			</mark>
		);
		from = at + want.length;
		at = lower.indexOf(want, from);
	}
	if (from < text.length) {
		parts.push(text.slice(from));
	}
	return parts;
};

const filterByType = (
	items: DocumentRecord[],
	typeFilter: string
): DocumentRecord[] =>
	typeFilter === "All types"
		? items
		: items.filter((document) => displayType(document) === typeFilter);

const displayStatus = (
	status: string | null
): "Processed" | "Processing" | "Needs review" | "Indexed" | "Failed" => {
	const normalized = (status || "").toLowerCase();
	if (normalized === "processing" || normalized === "uploaded") return "Processing";
	if (normalized === "pending_review" || normalized === "needs_review" || normalized === "needs_revision")
		return "Needs review";
	if (normalized === "indexed") return "Indexed";
	if (normalized === "failed" || normalized === "rejected") return "Failed";
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

const ocrPageNumber = (
	ocr: DocumentDetail["ocr_results"][number],
	pages: DocumentDetail["pages"]
) => pages.find((item) => item.id === ocr.page_id)?.page_number ?? null;

const ocrPageLabel = (
	ocr: DocumentDetail["ocr_results"][number],
	pages: DocumentDetail["pages"]
) => {
	const page = ocrPageNumber(ocr, pages);
	return page == null ? "Document" : `Page ${page}`;
};

const Library = () => {
	const { canReview, isSuperAdmin } = useAuth();
	const [searchParams, setSearchParams] = useSearchParams();
	const navigate = useNavigate();
	const [search, setSearch] = useState(searchParams.get("q") || "");
	const [debouncedSearch, setDebouncedSearch] = useState(search.trim());
	const [typeFilter, setTypeFilter] = useState("All types");
	const [statusFilter, setStatusFilter] = useState("All status");
	const [departmentFilter, setDepartmentFilter] = useState("All departments");
	const [selectedDocument, setSelectedDocument] =
		useState<DocumentDetail | null>(null);
	const [documents, setDocuments] = useState<DocumentItem[]>([]);
	const [hits, setHits] = useState<SearchResultHit[]>([]);
	const [total, setTotal] = useState(0);
	const [page, setPage] = useState(1);
	const listKey = `${debouncedSearch}|${typeFilter}|${statusFilter}|${departmentFilter}`;
	const [listKeySeen, setListKeySeen] = useState(listKey);
	if (listKey !== listKeySeen) {
		setListKeySeen(listKey);
		setPage(1);
	}
	const [loading, setLoading] = useState(true);
	const [searchLoading, setSearchLoading] = useState(false);
	const [error, setError] = useState("");
	const [deletingId, setDeletingId] = useState<number | null>(null);
	const [previewLoading, setPreviewLoading] = useState(false);
	const [previewError, setPreviewError] = useState("");
	const handledPreview = useRef<string | null>(null);
	const pageSize = 10;
	const showingSearch = debouncedSearch.length > 0;
	const [processingStatus, setProcessingStatus] =
		useState<DocumentProcessing | null>(null);
	const [retrying, setRetrying] = useState(false);
	// Bumped after a retry so the preview poll restarts even though the
	// document keeps the same id and the same PROCESSING status.
	const [statusNonce, setStatusNonce] = useState(0);

	const listQuery = (): DocumentQuery => ({
		status:
			statusFilter === "All status"
				? undefined
				: statusFilter === "Processed"
					? "completed"
					: statusFilter === "Needs review"
						? "needs_review"
						: statusFilter === "Indexed"
							? "indexed"
							: undefined,
		department:
			departmentFilter === "All departments"
				? undefined
				: departmentFilter,
		skip: (page - 1) * pageSize,
		limit: pageSize,
	});

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
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [searchParams]);

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

		// The control that opened the dialog gets its focus back on close.
		const opener = document.activeElement as HTMLElement | null;
		const modal = document.querySelector<HTMLElement>(".previewModal");
		modal?.focus({ preventScroll: true });

		const closeOnEscape = (event: KeyboardEvent) => {
			if (event.key === "Escape") {
				setSelectedDocument(null);
			}
		};
		window.addEventListener("keydown", closeOnEscape);

		return () => {
			window.removeEventListener("keydown", closeOnEscape);
			html.classList.remove("previewOpen");
			document.body.classList.remove("previewOpen");
			html.style.overflow = previousHtmlOverflow;
			document.body.style.overflow = previousBodyOverflow;
			opener?.focus({ preventScroll: true });

			if (searchParams.get("preview")) {
				const cleared = new URLSearchParams(searchParams);
				cleared.delete("preview");
				cleared.delete("page");
				cleared.delete("snippet");
				setSearchParams(cleared, { replace: true });
			}
		};
		// The scroll lock and focus handling belong to the viewer, not to the URL.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [selectedDocument]);

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
				extracted_tables: [],
				review_items: [],
			});
		} finally {
			setPreviewLoading(false);
		}
	};

	// A citation link opens the viewer on the cited page and passage; the key
	// carries every deep-link param so a second citation re-focuses.
	const previewKey = [
		searchParams.get("preview") ?? "",
		searchParams.get("page") ?? "",
		searchParams.get("snippet") ?? "",
	].join("|");
	// The passage a citation pointed at, marked inside the OCR text below.
	const focusText = searchParams.get("snippet");
	// The cited page comes straight from the URL: a deep link is only meaningful
	// while the preview param is present, so closing the viewer clears it.
	const previewParam = searchParams.get("preview");
	const citedPage = Number(searchParams.get("page"));
	const focusPage =
		previewParam !== null && Number.isFinite(citedPage) && citedPage > 0
			? citedPage
			: null;
	useEffect(() => {
		const previewId = searchParams.get("preview");
		if (!previewId) {
			handledPreview.current = null;
			return;
		}
		if (handledPreview.current === previewKey) {
			return;
		}
		handledPreview.current = previewKey;
		void openDocumentById(Number(previewId));
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [previewKey]);

	useEffect(() => {
		if (previewLoading || focusPage == null || !selectedDocument) {
			return;
		}
		document
			.getElementById(`ocr-page-${focusPage}`)
			?.scrollIntoView({ block: "center", behavior: "smooth" });
	}, [previewLoading, focusPage, selectedDocument]);

	useEffect(() => {
		const controller = new AbortController();
		const { signal } = controller;

		setLoading(true);
		setSearchLoading(showingSearch);
		setError("");

		if (showingSearch) {
			searchDocuments(debouncedSearch, (page - 1) * pageSize, pageSize, {
				mode: "hybrid",
				signal,
			})
				.then((response) => {
					setHits(response.data.items);
					setTotal(response.data.total);
					setDocuments([]);
				})
				.catch((error) => {
					if (!isRequestCanceled(error)) {
						setError("Unable to search documents.");
					}
				})
				.finally(() => {
					if (!signal.aborted) {
						setLoading(false);
						setSearchLoading(false);
					}
				});
			return () => controller.abort();
		}

		getDocuments(listQuery(), signal)
			.then((response) => {
				const items = filterByType(response.data.items, typeFilter);
				setDocuments(items);
				setHits([]);
				setTotal(
					typeFilter === "All types"
						? response.data.total
						: items.length
				);
			})
			.catch((error) => {
				if (!isRequestCanceled(error)) {
					setError("Unable to load documents from the server.");
				}
			})
			.finally(() => {
				if (!signal.aborted) {
					setLoading(false);
					setSearchLoading(false);
				}
			});

		return () => controller.abort();
	}, [
		debouncedSearch,
		typeFilter,
		statusFilter,
		departmentFilter,
		page,
		showingSearch,
	]);

	// While any listed document is still unsettled, quietly re-read the current
	// page so status chips move without blanking the table.
	useEffect(() => {
		const hasUnsettled = documents.some((item) => {
			const status = (item.status || "").toLowerCase();
			return status === "processing" || status === "uploaded";
		});

		if (!hasUnsettled || showingSearch) {
			return;
		}

		const timer = window.setTimeout(() => {
			getDocuments(listQuery())
				.then((response) => {
					setDocuments(
						filterByType(response.data.items, typeFilter)
					);
				})
				.catch(() => {
					// Keep the last known statuses; the next tick retries.
				});
		}, 8000);

		return () => window.clearTimeout(timer);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [documents, showingSearch, page, typeFilter, statusFilter, departmentFilter]);

	// Follow the pipeline for the document open in the preview.
	useEffect(() => {
		const id = selectedDocument?.id;
		const status = (selectedDocument?.status || "").toLowerCase();

		if (!id || (status !== "processing" && status !== "uploaded")) {
			return;
		}

		const controller = new AbortController();
		let timer: number | undefined;
		let stopped = false;

		const poll = async () => {
			if (stopped) {
				return;
			}

			try {
				const response = await getDocumentProcessing(id, controller.signal);
				if (stopped) {
					return;
				}
				setProcessingStatus(response.data);
				if (
					response.data.stage === "done" ||
					response.data.stage === "failed"
				) {
					return;
				}
			} catch {
				if (stopped || controller.signal.aborted) {
					return;
				}
				// A status read failure must not look like progress; stop and
				// leave the last real status on screen.
				return;
			}

			timer = window.setTimeout(poll, 4000);
		};

		timer = window.setTimeout(poll, 800);

		return () => {
			stopped = true;
			controller.abort();
			if (timer) {
				window.clearTimeout(timer);
			}
		};
	}, [selectedDocument?.id, selectedDocument?.status, statusNonce]);

	// The live panel only belongs to a document that has not settled yet; a
	// finished row shows its OCR text and the reasons the pipeline recorded.
	const previewUnsettled = ["processing", "uploaded"].includes(
		(selectedDocument?.status || "").toLowerCase()
	);
	const liveProcessing =
		previewUnsettled &&
		processingStatus?.document_id === selectedDocument?.id
			? processingStatus
			: null;
	const retryProcessing = async () => {
		if (!selectedDocument || retrying) {
			return;
		}

		setRetrying(true);
		setError("");

		try {
			const response = await retryDocumentProcessing(selectedDocument.id);
			setSelectedDocument({
				...selectedDocument,
				...response.data,
			});
			setProcessingStatus(null);
			setStatusNonce((current) => current + 1);
		} catch {
			setError(
				"Could not retry processing for this document. It may already be settled, or your role may not allow it."
			);
		} finally {
			setRetrying(false);
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
			extracted_tables: [],
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
					<div className="librarySearch" style={{ flex: 1, position: "relative" }}>
						<SearchIcon
							style={{ cursor: "pointer" }}
							onClick={() => setDebouncedSearch(search.trim())}
							titleAccess="Search documents"
						/>
						<input
							type="text"
							placeholder="Search documents by keyword or meaning (Press Enter)..."
							value={search}
							aria-label="Search documents"
							onChange={(event) => updateSearch(event.target.value)}
							onKeyDown={(event) => {
								if (event.key === "Enter") {
									setDebouncedSearch(search.trim());
								}
							}}
						/>
						{search.trim().length > 0 && (
							<button
								type="button"
								onClick={() => {
									updateSearch("");
									setDebouncedSearch("");
								}}
								style={{
									position: "absolute",
									right: "12px",
									top: "50%",
									transform: "translateY(-50%)",
									border: "none",
									background: "transparent",
									cursor: "pointer",
									color: "var(--text-secondary)",
									display: "flex",
									alignItems: "center",
									padding: "4px",
								}}
								title="Clear search"
								aria-label="Clear search"
							>
								<CloseIcon style={{ fontSize: 16 }} />
							</button>
						)}
					</div>
				</div>

				<div className="filterGroup">
					{isSuperAdmin && (
						<div className="filterSelect">
							<FilterListIcon />
							<select
								value={departmentFilter}
								aria-label="Filter by department"
								onChange={(event) => setDepartmentFilter(event.target.value)}
								disabled={showingSearch}
							>
								<option>All departments</option>
								<option value="HR">HR</option>
								<option value="FINANCE">Finance</option>
								<option value="LEGAL">Legal</option>
								<option value="OPERATIONS">Operations</option>
								<option value="IT">IT</option>
							</select>
							<KeyboardArrowDownIcon />
						</div>
					)}
					<div className="filterSelect">
						<select
							value={typeFilter}
							aria-label="Filter by document type"
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
							aria-label="Filter by processing status"
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
							: "No matching documents found. Try a different query or wait until documents are indexed."
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
									<th>DEPARTMENT</th>
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
										<td colSpan={7}>Loading documents...</td>
									</tr>
								) : error ? (
									<tr>
										<td colSpan={7}>{error}</td>
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
												<span
													style={{
														padding: "4px 8px",
														borderRadius: "6px",
														fontSize: "11px",
														fontWeight: 600,
														background: document.department
															? "rgba(245, 158, 11, 0.12)"
															: "rgba(255, 255, 255, 0.05)",
														color: document.department
															? "#fbbf24"
															: "var(--text-secondary, #94a3b8)",
													}}
												>
													{document.department || "--"}
												</span>
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
												aria-label={`Preview ${document.original_filename}`}
														onClick={() => openDocument(document)}
													>
														<VisibilityOutlinedIcon />
													</button>
													<button
														className="iconAction deleteAction"
														title="Delete"
												aria-label={`Delete ${document.original_filename}`}
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
										<td colSpan={7}>
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
						role="dialog"
						aria-modal="true"
						aria-labelledby="libraryPreviewTitle"
						tabIndex={-1}
						onClick={(event) => event.stopPropagation()}
					>
						<div className="previewHeader">
							<div>
								<h2 id="libraryPreviewTitle">Document Preview</h2>
								<p>{selectedDocument.original_filename}</p>
								{searchParams.get("preview") && (
									<button
										type="button"
										className="previewCancel"
										onClick={() => navigate("/ask")}
									>
										Back to the answer
									</button>
								)
								}
							</div>
							<button
								className="previewClose"
								onClick={() => setSelectedDocument(null)}
								aria-label="Close document preview"
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
											<strong>Department</strong>
											<span>{selectedDocument.department || "Unassigned"}</span>
										</div>
										<div>
											<strong>Document Type</strong>
											<span>{selectedDocument.document_type || "--"}</span>
										</div>
										<div>
											<strong>Status</strong>
											<span>{displayStatus(selectedDocument.status)}</span>
										</div>
										<div>
											<strong>Quality score</strong>
											<span title={CONFIDENCE_EXPLANATIONS.document}>
												{formatConfidence(selectedDocument.overall_confidence)}
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
									{/* Live pipeline state, read from the server's own row counts. */}
									{liveProcessing && (
										<div className="previewOCR" role="status" aria-live="polite" aria-atomic="true">
											<h4>
												{liveProcessing.stage === "failed"
													? "Processing failed"
													: "Processing"}
											</h4>
											<p>{liveProcessing.message}</p>
											<p className="previewFocusMiss">
												{`Pages read: ${liveProcessing.pages_completed}` +
													(liveProcessing.pages_total
														? ` · of ${liveProcessing.pages_total}`
														: "") +
													(liveProcessing.pages_failed
														? ` · ${liveProcessing.pages_failed} failed OCR`
														: "")}
											</p>
											{liveProcessing.stage === "failed" && canReview && (
												<button
													type="button"
													className="previewCancel"
													onClick={retryProcessing}
													disabled={retrying}
												>
													{retrying ? "Retrying..." : "Retry processing"}
												</button>
											)}
										</div>
									)}

									{/* Why the pipeline flagged this document. */}
									{!liveProcessing &&
										selectedDocument.review_items.length > 0 && (
											<div className="previewOCR">
												<h4>Why this needs attention</h4>
												{selectedDocument.review_items.map((item) => (
													<p key={item.id}>
														{item.reason || "No reason recorded."}
													</p>
												))}
											</div>
										)
									}

									{selectedDocument.ocr_results.length > 0 && (
										<div className="previewOCR">
											<h4>OCR Content</h4>
											{focusPage != null &&
												!selectedDocument.ocr_results.some(
													(ocr) => ocrPageNumber(ocr, selectedDocument.pages) === focusPage
												) && (
													<p className="previewFocusMiss">
														This document has no OCR text for page {focusPage}.
													</p>
												)}
												{selectedDocument.ocr_results.map((ocr) => {
													const pageNumber = ocrPageNumber(
														ocr,
														selectedDocument.pages
													);
													const pageTables = (
														selectedDocument.extracted_tables || []
													).filter(
														(table) =>
															pageNumber != null &&
															table.page_number === pageNumber
													);
													const rawPageText =
														ocr.extracted_text ||
														((ocr.ocr_engine || "").includes("Failed")
															? "OCR failed on this page: no text was recovered."
															: (ocr.ocr_engine || "").includes(
																	"SkippedBlank"
																)
																? "This page was read as blank: the scan has no marks dark enough to recognise. If the original carries writing, this page needs re-scanning."
																: "No text layer on this page.");
													// Measured rows are drawn as a table below, so the
													// same rows are not printed twice as "cell | cell".
													// A page whose only record is a refused grid keeps
													// every line the reader recognised: the notice
													// explains it, the text stays searchable.
													const prose =
														pageHasRenderableTable(pageTables)
															? stripTableBlocks(rawPageText)
															: rawPageText;
													const focused =
														focusPage != null &&
														pageNumber === focusPage;
													return (
														<div
															className={
																focused
																	? "ocrResult ocrResultFocus"
																	: "ocrResult"
															}
															id={
																pageNumber == null
																	? undefined
																	: `ocr-page-${pageNumber}`
															}
															key={ocr.id}
														>
															<div className="ocrResultHeader">
																<span>
																	{ocrPageLabel(
																		ocr,
																		selectedDocument.pages
																	)}
																</span>
																<span
																	title={CONFIDENCE_EXPLANATIONS.page}
																>
																	{(ocr.ocr_engine || "OCR") +
																		(typeof ocr.confidence === "number"
																			? ` · ${formatConfidence(ocr.confidence)} word confidence`
																			: ` · word confidence not measured`)}
																</span>
															</div>
															{pageTables.length > 0 && (
																<PageStructuredTables
																	tables={pageTables}
																	highlight={(text) =>
																		highlightPassage(text, focusText)
																	}
																/>
															)}
															{prose ? <p>{highlightPassage(prose, focusText)}</p> : null}
														</div>
													);
												})}
										</div>
									)}
									{selectedDocument.extracted_fields.length > 0 && (
										<div className="previewOCR">
											<h4>Extracted Fields</h4>
											{selectedDocument.extracted_fields.map((field) => (
												<div className="ocrResult" key={field.id}>
													<div className="ocrResultHeader">
														<strong>{field.field_name}</strong>
														<span title={CONFIDENCE_EXPLANATIONS.field}>
															{formatConfidence(field.confidence)} match strength
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
