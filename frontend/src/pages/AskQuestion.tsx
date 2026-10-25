import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import CloseOutlinedIcon from "@mui/icons-material/CloseOutlined";

import {
	askQuestion,
	sourceKindLabels,
	supportLabels,
	valueStateLabels,
} from "../api/query";
import type { SourceCitation } from "../api/query";
import { getAllDocuments } from "../api/document";
import { useAuth } from "../context/AuthContext";
import type { DocumentRecord } from "../api/document";
import { isRequestCanceled, requestStatus } from "../api/client";


const sampleQuestions = [
	"How much does the customer owe?",
	"When is the payment due?",
	"What is the invoice number?",
	"What happens if payment is late?",
];

type Message = {
	type: "user" | "ai";
	text: string;
	sources?: SourceCitation[];
	hasEvidence?: boolean;
	notes?: string;
	engine?: string;
};

// Which code path actually produced the answer. An extractive composition is
// never labelled as a model answer.
const engineLabels: Record<string, string> = {
	llm_ollama: "Local Ollama model",
	extractive_evidence: "Quoted from document evidence",
	ambiguous_evidence: "Conflicting evidence",
	no_evidence: "No supporting evidence",
	unauthorized: "Outside your access",
	metadata: "From document records",
	none: "Nothing to search",
};

const describeEngine = (engine: string) =>
	engine
		.split("+")
		.map((part) => engineLabels[part] ?? part.replace(/_/g, " "))
		.join(" + ");

// Only documents whose OCR output is searchable may widen an answer; a
// page still in the pipeline would add noise without evidence.
const USABLE_STATUSES = [
	"indexed",
	"completed",
	"processed",
	"needs_review",
	"pending_review",
	"needs_revision",
];

const statusLabel = (status: string | null) => {
	const normalized = (status || "").toLowerCase();
	if (normalized === "indexed") return "indexed";
	if (normalized === "completed" || normalized === "processed")
		return "processed";
	if (normalized === "needs_review" || normalized === "pending_review")
		return "needs review";
	return normalized.replace(/_/g, " ") || "unknown";
};

// One note per kind of failure: an unreachable API, an expired session, a
// forbidden account and a rejected request each need a different fix, so they
// must never be reported as an empty library.
const describeDocsFailure = (error: unknown) => {
	const status = requestStatus(error);
	if (status === undefined) {
		return "The document service could not be reached. Check that the API is running.";
	}
	if (status === 401) {
		return "Your session has expired, so documents could not be listed. Sign in again.";
	}
	if (status === 403) {
		return "This account may not list documents. Ask an administrator to restore your access.";
	}
	if (status >= 500) {
		return `The document service failed (HTTP ${status}). Try again in a moment.`;
	}
	return `The document list request was rejected (HTTP ${status}).`;
};

// A failed answer must not read like a document that has no evidence. The
// statuses that need a different fix are reported separately.
const describeAskFailure = (error: unknown) => {
	const status = requestStatus(error);
	if (status === undefined) {
		return "The question could not be sent. Check that the API is running, then try again.";
	}
	if (status === 401) {
		return "Your session has expired, so the question was not answered. Sign in again.";
	}
	if (status === 403) {
		return "This account is not allowed to ask questions over the documents you selected.";
	}
	if (status >= 500) {
		return `The question-answering service failed (HTTP ${status}). The documents were not changed; try again in a moment.`;
	}
	return `The question was rejected (HTTP ${status}).`;
};

// A reload or an abort stops the request that was in flight, so the last question
// would otherwise sit unanswered and read as if the documents held nothing.
const repairInterruptedTurn = (messages: Message[]) => {
	const last = messages[messages.length - 1];
	if (!last || last.type !== "user") {
		return messages;
	}
	return [
		...messages,
		{
			type: "ai" as const,
			text: "That answer was interrupted when the page reloaded. Ask again.",
			hasEvidence: false,
		},
	];
};

type Conversation = {
	messages: Message[];
	scopeIds: number[];
};

// The conversation belongs to the running app, not to this route: AskQuestion
// unmounts on every navigation, and a module variable outlives that. sessionStorage
// mirrors it for a page refresh, and both are dropped when the tab closes, so no
// chat text is ever written to the database.
const getChatStorageKey = (userId?: number | null) =>
	userId ? `docsentinel.chat.v1.user_${userId}` : "docsentinel.chat.v1.anon";

const isMessage = (value: unknown): value is Message => {
	const message = value as Partial<Message>;
	return (
		(message?.type === "user" || message?.type === "ai") &&
		typeof message?.text === "string"
	);
};

const emptyConversation = (): Conversation => ({ messages: [], scopeIds: [] });

const readConversation = (userId?: number | null): Conversation => {
	try {
		const key = getChatStorageKey(userId);
		const raw = sessionStorage.getItem(key);
		if (!raw) {
			return emptyConversation();
		}
		const stored = JSON.parse(raw) as Partial<Conversation>;
		const messages = repairInterruptedTurn(
			Array.isArray(stored.messages) ? stored.messages.filter(isMessage) : []
		);
		const scopeIds = Array.isArray(stored.scopeIds)
			? [...new Set(stored.scopeIds.filter((id) => typeof id === "number"))]
			: [];
		return { messages, scopeIds };
	} catch {
		return emptyConversation();
	}
};

let cachedUserId: number | null | undefined = undefined;
let conversation: Conversation = emptyConversation();

const persistConversation = (next: Conversation, userId?: number | null) => {
	conversation = next;
	cachedUserId = userId;
	try {
		const key = getChatStorageKey(userId);
		sessionStorage.setItem(key, JSON.stringify(next));
	} catch {
	}
};

// ChatGPT shows a file card, not a row of text: the name identifies the
// attachment and the type says what kind of document the answer will come from.
const fileTypeLabel = (document: DocumentRecord) => {
	const mime = (document.file_type || "").toLowerCase();
	const extension = (
		document.original_filename.split(".").pop() ?? ""
	).toLowerCase();
	if (mime.includes("pdf") || extension === "pdf") return "PDF";
	if (
		mime.startsWith("image/") ||
		["png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp"].includes(extension)
	)
		return "Image";
	if (
		mime.includes("spreadsheet") ||
		mime.includes("excel") ||
		["xlsx", "xls", "csv"].includes(extension)
	)
		return "Spreadsheet";
	if (mime.includes("word") || ["doc", "docx"].includes(extension)) return "Word";
	if (mime.includes("text") || extension === "txt") return "Text";
	return extension ? extension.toUpperCase() : "File";
};

const AskQuestion = () => {
	const { user: currentUser } = useAuth();
	const currentUserId = currentUser?.id ?? null;
	const prevUserRef = useRef<number | null>(currentUserId);
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const [question, setQuestion] = useState("");

	const [messages, setMessages] = useState<Message[]>(() => {
		if (cachedUserId !== currentUserId) {
			conversation = readConversation(currentUserId);
			cachedUserId = currentUserId;
		}
		return conversation.messages;
	});
	const [isSearching, setIsSearching] = useState(false);
	const processedQuery = useRef<string | null>(null);
	const abortRef = useRef<AbortController | null>(null);
	const composerRef = useRef<HTMLInputElement | null>(null);
	const [pickerOpen, setPickerOpen] = useState(false);
	const [scopeIds, setScopeIds] = useState<number[]>(() => {
		if (cachedUserId !== currentUserId) {
			conversation = readConversation(currentUserId);
			cachedUserId = currentUserId;
		}
		return conversation.scopeIds;
	});

	// Reset state immediately if authenticated user switches
	useEffect(() => {
		if (prevUserRef.current !== currentUserId) {
			prevUserRef.current = currentUserId;
			const userConvo = readConversation(currentUserId);
			conversation = userConvo;
			cachedUserId = currentUserId;
			setMessages(userConvo.messages);
			setScopeIds(userConvo.scopeIds);
			setQuestion("");
		}
	}, [currentUserId]);
	const [docSearch, setDocSearch] = useState("");
	const [documents, setDocuments] = useState<DocumentRecord[]>([]);
	const [docsStatus, setDocsStatus] = useState<
		"loading" | "ready" | "error"
	>("loading");
	const [docsError, setDocsError] = useState("");
	const docsFetched = useRef(false);

	const loadDocuments = async () => {
		setDocsStatus("loading");
		setDocsError("");
		try {
			// Pages stay at the maximum the API accepts and advance by skip until
			// the reported total is covered, so a larger library is not quietly
			// cut short at one page.
			const available = await getAllDocuments();
			setDocuments(
				available.filter((document) =>
					USABLE_STATUSES.includes((document.status || "").toLowerCase())
				)
			);
			// A conversation restored from a refresh can outlive a document: an id
			// this account can no longer list must be detached, not kept as a scope
			// the server will refuse.
			const listed = new Set(available.map((document) => document.id));
			setScopeIds((current) => {
				const kept = [...new Set(current.filter((id) => listed.has(id)))];
				return kept.length === current.length ? current : kept;
			});
			docsFetched.current = true;
			setDocsStatus("ready");
		} catch (error) {
			docsFetched.current = false;
			setDocuments([]);
			setDocsError(describeDocsFailure(error));
			setDocsStatus("error");
		}
	};

	useEffect(() => {
		if (pickerOpen && !docsFetched.current) {
			void loadDocuments();
		}
	}, [pickerOpen]);

	// A restored selection has to show which document it is, so its names are
	// fetched on arrival rather than only when the picker is opened.
	const restoredScope = useRef(
		scopeIds.length > 0 && !docsFetched.current ? scopeIds : null
	);
	useEffect(() => {
		const restored = restoredScope.current;
		if (!restored || docsFetched.current) {
			return;
		}
		if (restored.join() !== scopeIds.join()) {
			restoredScope.current = null;
			return;
		}
		restoredScope.current = null;
		void loadDocuments();
	}, [scopeIds]);

	// Written on every change: switching views keeps the turns and their scope in
	// the module store, and a refresh reads them back from sessionStorage.
	useEffect(() => {
		persistConversation({ messages, scopeIds }, currentUserId);
	}, [messages, scopeIds]);

	const filteredDocuments = useMemo(() => {
		const needle = docSearch.trim().toLowerCase();
		if (needle.length === 0) {
			return documents;
		}
		return documents.filter((document) =>
			document.original_filename.toLowerCase().includes(needle)
		);
	}, [documents, docSearch]);

	const toggleDocument = (id: number) =>
		setScopeIds((current) =>
			current.includes(id)
				? current.filter((value) => value !== id)
				: [...current, id]
		);

	// One document can only be attached once, however often the picker is used,
	// so a scope never carries a duplicate id into a request.
	const selectAllDocuments = () =>
		setScopeIds([...new Set(filteredDocuments.map((document) => document.id))]);

	const clearScope = () => setScopeIds([]);

	const selectedDocuments = documents.filter((document) =>
		scopeIds.includes(document.id)
	);


	// Navigating away cancels an in-flight question instead of leaving the local
	// model generating for a page that is already gone.
	useEffect(() => () => abortRef.current?.abort(), []);

	// The turns the server keeps as wording context. The current question is not
	// included and an answer without evidence is not repeated back as if it were.
	const historyForRequest = (previous: Message[]) =>
		previous
			.filter((message) => message.text.trim().length > 0)
			.slice(-8)
			.map((message) => ({
				role: message.type === "user" ? "user" : "assistant",
				content: message.text,
			}));

	const runAskQuestion = async (query: string) => {
		if (isSearching) {
			return;
		}

		setIsSearching(true);
		const previous = messages;
		setMessages((current) => [...current, { type: "user", text: query }]);
		setQuestion("");

		// A long local generation can outlast the user's patience; leaving this
		// page, or pressing Stop, cancels the request instead of orphaning it.
		const controller = new AbortController();
		abortRef.current = controller;

		try {
			const response = await askQuestion(
				{
					question: query,
					// An empty scope must stay absent so the server searches the whole
					// authorized library instead of nothing.
					document_ids: scopeIds.length > 0 ? scopeIds : undefined,
					history: historyForRequest(previous),
				},
				controller.signal
			);
			const { answer, sources, has_evidence, notes, engine } =
				response.data;
			setMessages((current) => [
				...current,
				{
					type: "ai",
					text: answer,
					sources,
					hasEvidence: has_evidence,
					notes,
					engine,
				},
			]);
		} catch (error) {
			const stopped = isRequestCanceled(error);
			setMessages((current) => [
				...current,
				{
					type: "ai",
					text: stopped
						? "Stopped. Nothing was added to this conversation."
						: describeAskFailure(error),
					hasEvidence: false,
				},
			]);
		} finally {
			abortRef.current = null;
			setIsSearching(false);
		}
	};

	const stopRequest = () => abortRef.current?.abort();

	// The conversation is kept deliberately, so starting a new one is the only
	// way it is cleared - and it also releases the document scope.
	const newConversation = () => {
		abortRef.current?.abort();
		persistConversation(emptyConversation(), currentUserId);
		setMessages([]);
		setScopeIds([]);
		composerRef.current?.focus();
	};

	useEffect(() => {
		const query = searchParams.get("q");
		if (!query || processedQuery.current === query) {
			return;
		}
		processedQuery.current = query;
		void runAskQuestion(query);
	}, [searchParams]);

	const handleSend = () => {
		const trimmedQuestion = question.trim();
		if (!trimmedQuestion || isSearching) {
			return;
		}
		void runAskQuestion(trimmedQuestion);
	};

	return (
		<div className="askQuestionPage">
			<div className="askQuestionHeader" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
				<div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
					<span style={{ fontSize: "0.95rem", color: "#64748b", fontWeight: 500 }}>
						Document Intelligence Chat
					</span>
					{scopeIds.length > 0 && (
						<span style={{
							fontSize: "0.75rem",
							padding: "3px 10px",
							borderRadius: "12px",
							background: "rgba(16, 185, 129, 0.12)",
							color: "#10b981",
							fontWeight: 600,
						}}>
							{scopeIds.length} Document{scopeIds.length > 1 ? "s" : ""} Attached to Context
						</span>
					)}
				</div>
				<button
					type="button"
					className="newConversationButton"
					onClick={newConversation}
					disabled={messages.length === 0}
					title="Clear this conversation and search every document again."
				>
					New conversation
				</button>
			</div>

			<div className="chatContainer">
				<div className="chatArea">
					{messages.length === 0 ? (
						<div className="chatWelcome">
							<div className="aiWelcomeIcon">
								<AutoAwesomeOutlinedIcon />
							</div>
							<h2>Ask DocSentinel</h2>
							<p>
								Search across your documents using natural language.
								<br />
								I'll retrieve the most relevant passages.
							</p>
							<div className="sampleQuestions">
								<span className="sampleQuestionsTitle">Try asking</span>
								<div className="sampleQuestionGrid">
									{sampleQuestions.map((sample) => (
										<button
											key={sample}
											type="button"
											className="sampleQuestion"
											onClick={() => setQuestion(sample)}
										>
											<AutoAwesomeOutlinedIcon />
											<span>{sample}</span>
										</button>
									))}
								</div>
							</div>
						</div>
					) : (
						<div
							className="messagesArea"
							role="log"
							aria-live="polite"
							aria-label="Conversation"
						>
							{messages.map((message, index) => (
								<div
									key={`${message.type}-${index}`}
									className={`messageRow ${message.type}`}
								>
									{message.type === "ai" && (
										<div className="messageAvatar">
											<AutoAwesomeOutlinedIcon />
										</div>
									)}
									<div className="messageColumn">
										<div className="messageBubble" style={{ whiteSpace: "pre-wrap" }}>
											{message.text}
										</div>
										{message.type === "ai" && message.engine && (
											<span className="answerEngine">
												{describeEngine(message.engine)}
											</span>
										)}
										{message.notes && (
											<span className="answerNotes">{message.notes}</span>
										)}
										{message.type === "ai" && message.hasEvidence && (
											<button
												type="button"
												className="followUpButton"
												onClick={() => composerRef.current?.focus()}
												title="Earlier turns stay as context, so this one can refer to them."
											>
												Ask a follow-up
											</button>
										)
										}
										{message.sources && message.sources.length > 0 && (
											<div className="sourceCards">
												{message.sources.map((source, sIdx) => {
													const target = new URLSearchParams({
														preview: String(source.document_id),
													});
													if (source.page_number != null) {
														target.set("page", String(source.page_number));
													}
													// The viewer marks the cited passage itself, not only the
													// page, so a short snippet is enough to land on.
													const needle = source.snippet.trim().slice(0, 120);
													if (needle) {
														target.set("snippet", needle);
													}
													const support = source.support
														? supportLabels[source.support] ?? null
														: null;
													const kind = source.source_kind
														? sourceKindLabels[source.source_kind] ?? null
														: null;
													const warning = source.value_state
														? valueStateLabels[source.value_state] ?? null
														: null;
													return (
														<button
															key={`${source.document_id}-${source.page_number}-${sIdx}`}
															type="button"
															className="sourceCard"
														title={
															source.page_number == null
																? `Open ${source.document_name}`
																: `Open ${source.document_name}, page ${source.page_number}`
															}
															onClick={() =>
																navigate(`/library?${target.toString()}`)
															}
														>
															<div className="sourceCardHeader">
																<strong>{source.document_name}</strong>
															{source.similarity != null && source.similarity > 0 ? (
																<span
																	className="similarityBadge"
																	title="Cosine retrieval similarity (0-1). A relevance ranking signal, not an accuracy percentage."
																>
																	Similarity: {source.similarity.toFixed(2)}
																</span>
																) : (
																<span
																	className="similarityBadge"
																	title="Matched by the wording written on the page, not by a vector score, so there is no similarity to report."
																>
																	Matched by wording
																</span>
															)}
															</div>
															<div className="sourceCardMetaRow">
																{source.page_number != null && (
																	<span className="sourceCardMeta">
																		Page {source.page_number}
																	</span>
																)}
																{kind && <span className="sourceCardKind">{kind}</span>}
																{support && (
																	<span className="sourceCardSupport">{support}</span>
																)}
																{warning && (
																	<span className="sourceCardWarning" role="note">
																		{warning}
																	</span>
																)}
															</div>
															{source.table_columns && source.table_columns.length > 0 && (
																<p className="sourceCardColumns">
																	{source.table_columns.join(" | ")}
																</p>
															)}
															<p>{source.snippet}</p>
														</button>
													);
												})}
											</div>
										)}
									</div>
								</div>
							))}
							{isSearching && (
								<div className="messageRow ai">
									<div className="messageAvatar">
										<AutoAwesomeOutlinedIcon />
									</div>
									<div
										className="messageBubble"
										role="status"
										aria-live="polite"
										aria-atomic="true"
									>
										Searching your documents and checking the evidence...
									</div>
								</div>
							)}
						</div>
					)}
				</div>

				<div className="chatBottom">
					{pickerOpen && (
						<div
							id="docSelector"
							className="docSelector"
							role="dialog"
							aria-label="Choose documents to search"
							onKeyDown={(event) => {
								if (event.key === "Escape") {
									setPickerOpen(false);
								}
							}}
						>
						<div className="docSelectorBar">
							<input
								type="search"
								className="docSelectorSearch"
								value={docSearch}
								onChange={(event) => setDocSearch(event.target.value)}
								placeholder="Search document names"
								aria-label="Search document names"
							/>
							<button
								type="button"
								className="docSelectorAction"
								onClick={selectAllDocuments}
								disabled={filteredDocuments.length === 0}
							>
							Select all
							</button>
							<button
								type="button"
								className="docSelectorAction"
								onClick={clearScope}
								disabled={scopeIds.length === 0}
							>
							Clear
							</button>
						</div>
						<div className="docSelectorList" role="group" aria-label="Authorized documents">
							{docsStatus === "loading" ? (
								<p className="docSelectorNote">Loading documents...</p>
							) : docsStatus === "error" ? (
								<p className="docSelectorNote">
									{docsError}
									<button
										type="button"
										className="docSelectorAction"
										onClick={() => void loadDocuments()}
									>
									Try again
									</button>
								</p>
							) : documents.length === 0 ? (
								<p className="docSelectorNote">
									None of the documents you can access are searchable yet.
								</p>
							) : filteredDocuments.length === 0 ? (
								<p className="docSelectorNote">
									No processed documents match this search.
								</p>
							) : (
								filteredDocuments.map((document) => (
									<label className="docSelectorItem" key={document.id}>
										<input
											type="checkbox"
											checked={scopeIds.includes(document.id)}
											onChange={() => toggleDocument(document.id)}
										/>
										<span
											className="docSelectorName"
											title={document.original_filename}
										>
										{document.original_filename}
										</span>
										<span className="docSelectorMeta">{statusLabel(document.status)}</span>
									</label>
								))
							)}
						</div>
						<div className="docSelectorFooter">
							<span>
								{scopeIds.length === 0
									? "Searching every document you can access."
									: `${scopeIds.length} selected - answers use only these.`}
							</span>
							<button
								type="button"
								className="docSelectorDone"
								onClick={() => setPickerOpen(false)}
							>
							Done
							</button>
						</div>
						</div>
					)}
					{scopeIds.length === 0 ? (
						<div className="selectedDocument">
							<DescriptionOutlinedIcon />
							<span>All documents</span>
						</div>
					) : (
						<div className="attachmentStrip">
							{selectedDocuments.map((document) => (
								<div
									className="attachmentCard"
									key={document.id}
									title={`${document.original_filename} - OCR text, extracted fields and indexed embeddings contribute to RAG`}
								>
									<DescriptionOutlinedIcon />
									<span className="attachmentName">{document.original_filename}</span>
									<span className="attachmentType">{fileTypeLabel(document)}</span>
									{document.department && (
										<span
											className="attachmentType"
											style={{ background: "rgba(59, 130, 246, 0.1)", color: "#2563eb", borderColor: "rgba(59, 130, 246, 0.2)" }}
										>
											{document.department}
										</span>
									)}
									<span className="attachmentStatus">{statusLabel(document.status)}</span>
									<button
										type="button"
										className="attachmentRemove"
										aria-label={`Detach ${document.original_filename} from this conversation`}
										onClick={() => toggleDocument(document.id)}
									>
										<CloseOutlinedIcon />
									</button>
								</div>
							))}
							{scopeIds.length > selectedDocuments.length && (
								<span
									className="attachmentMore"
									title="Attached documents the list could not name yet."
								>
									+{scopeIds.length - selectedDocuments.length}
								</span>
							)}
							<button type="button" className="scopeClear" onClick={clearScope}>
								Clear
							</button>
						</div>
					)}
					<div className="questionInputWrapper">
						<button
							type="button"
							className="attachButton"
							aria-label="Choose documents to search"
							aria-expanded={pickerOpen}
							aria-controls="docSelector"
							title="Limit the search to specific documents"
							onClick={() => setPickerOpen((open) => !open)}
						>
						<AddOutlinedIcon />
						</button>
						<label className="srOnly" htmlFor="questionInput">
							Ask a question about your documents
						</label>
						<input
							id="questionInput"
							ref={composerRef}
							type="text"
							value={question}
							onChange={(event) => setQuestion(event.target.value)}
							onKeyDown={(event) => {
								if (event.key === "Enter") {
									handleSend();
								}
							}}
							placeholder="Ask anything about your documents..."
							aria-label="Ask a question about your documents"
							aria-busy={isSearching}
							aria-describedby="askDisclaimer"
						/>
						{isSearching ? (
							<button
								type="button"
								className="sendButton"
								onClick={stopRequest}
								aria-label="Stop generating the answer"
							>
								<CloseOutlinedIcon />
							</button>
						) : (
							<button
								type="button"
								className="sendButton"
								onClick={handleSend}
								disabled={!question.trim()}
								aria-label="Send question"
							>
								<SendOutlinedIcon />
							</button>
						)}
					</div>
					<span className="aiDisclaimer" id="askDisclaimer">
						Answers are grounded strictly in authorized document passages. Verify important information.
					</span>
				</div>
			</div>
		</div>
	);
};

export default AskQuestion;
