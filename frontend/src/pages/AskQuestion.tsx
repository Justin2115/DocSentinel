import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";
import CloseOutlinedIcon from "@mui/icons-material/CloseOutlined";

import {
	createConversation,
	deleteConversation,
	getConversation,
	getProviderStatus,
	listConversations,
	renameConversation,
	sourceKindLabels,
	streamConversationMessage,
	supportLabels,
	valueStateLabels,
} from "../api/query";
import type { ConversationSummary, SourceCitation, StoredChatMessage } from "../api/query";
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

// Filename and type only: the card identifies what was in scope, never OCR text.
type MessageAttachment = {
	id: number;
	name: string;
	typeLabel: string;
};

type Message = {
	type: "user" | "ai";
	text: string;
	attachments?: MessageAttachment[];
	sources?: SourceCitation[];
	hasEvidence?: boolean;
	notes?: string;
	engine?: string;
	status?: "streaming" | "failed" | "interrupted";
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

const fromStoredMessage = (message: StoredChatMessage): Message => ({
	type: message.role === "user" ? "user" : "ai",
	text: message.status === "failed"
		? message.error_metadata?.message || "The answer could not be generated. Try again."
		: message.status === "interrupted"
			? "This answer was interrupted. You can ask again."
			: message.content,
	sources: message.citation_metadata || undefined,
	hasEvidence: message.usage_metadata?.has_evidence,
	engine: message.model_id || undefined,
	notes: message.usage_metadata?.notes,
	status: message.status === "completed" ? undefined : message.status,
});

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

const DocumentFileCard = ({
	name,
	typeLabel,
	status,
	department,
	onRemove,
	removeLabel,
	title,
}: {
	name: string;
	typeLabel: string;
	status?: string;
	department?: string | null;
	onRemove?: () => void;
	removeLabel?: string;
	title?: string;
}) => (
	<div
		className={onRemove ? "attachmentCard" : "messageFileCard"}
		title={title ?? name}
	>
		<span className={onRemove ? undefined : "messageFileCardIcon"} aria-hidden="true">
			<DescriptionOutlinedIcon />
		</span>
		{onRemove ? (
			<>
				<span className="attachmentName">{name}</span>
				<span className="attachmentType">{typeLabel}</span>
				{department && (
					<span className="attachmentType attachmentDepartment">{department}</span>
				)}
				{status && <span className="attachmentStatus">{status}</span>}
				<button
					type="button"
					className="attachmentRemove"
					aria-label={removeLabel ?? `Detach ${name} from this conversation`}
					onClick={onRemove}
				>
					<CloseOutlinedIcon />
				</button>
			</>
		) : (
			<span className="messageFileCardBody">
				<span className="messageFileCardName">{name}</span>
				<span className="messageFileCardType">{typeLabel}</span>
			</span>
		)}
	</div>
);

const AskQuestion = () => {
	const { user: currentUser } = useAuth();
	const currentUserId = currentUser?.id ?? null;
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const [question, setQuestion] = useState("");
	const [chatMode, setChatMode] = useState<"documents" | "general">("documents");
	const [messages, setMessages] = useState<Message[]>([]);
	const [conversations, setConversations] = useState<ConversationSummary[]>([]);
	const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
	const [nextBeforeId, setNextBeforeId] = useState<number | null>(null);
	const [historyLoading, setHistoryLoading] = useState(true);
	const [historyError, setHistoryError] = useState("");
	const [providerReady, setProviderReady] = useState<boolean | null>(null);
	const [isSearching, setIsSearching] = useState(false);
	const [copyFeedback, setCopyFeedback] = useState<{ index: number; text: string } | null>(null);
	const processedQuery = useRef<string | null>(null);
	const abortRef = useRef<AbortController | null>(null);
	const composerRef = useRef<HTMLInputElement | null>(null);
	const messagesListRef = useRef<HTMLDivElement | null>(null);
	const followLatestRef = useRef(true);
	const [pickerOpen, setPickerOpen] = useState(false);
	const [scopeIds, setScopeIds] = useState<number[]>([]);
	const [docSearch, setDocSearch] = useState("");
	const [documents, setDocuments] = useState<DocumentRecord[]>([]);
	const [docsStatus, setDocsStatus] = useState<
		"loading" | "ready" | "error"
	>("loading");
	const [docsError, setDocsError] = useState("");
	const docsFetched = useRef(false);

	const openConversation = async (id: string) => {
		abortRef.current?.abort();
		setIsSearching(false);
		setActiveConversationId(id);
		setMessages([]);
		setHistoryError("");
		try {
			const response = await getConversation(id);
			setMessages(response.data.messages.map(fromStoredMessage));
			setNextBeforeId(response.data.next_before_id);
			setChatMode(response.data.chat_mode);
			const latestPrompt = [...response.data.messages].reverse().find((message) => message.role === "user");
			setScopeIds(latestPrompt?.request_metadata?.document_ids ?? []);
			setConversations((current) => [
				response.data,
				...current.filter((item) => item.id !== id),
			]);
		} catch {
			setHistoryError("This conversation could not be loaded. Refresh the history and try again.");
			setActiveConversationId(null);
		}
	};

	const loadOlderMessages = async () => {
		if (!activeConversationId || nextBeforeId == null) return;
		try {
			const { data } = await getConversation(activeConversationId, 50, nextBeforeId);
			setMessages((current) => [...data.messages.map(fromStoredMessage), ...current]);
			setNextBeforeId(data.next_before_id);
		} catch {
			setHistoryError("Older messages could not be loaded.");
		}
	};

	useEffect(() => {
		let cancelled = false;
		setHistoryLoading(true);
		setHistoryError("");
		void listConversations().then(async ({ data }) => {
			if (cancelled) return;
			setConversations(data);
			if (data[0]) await openConversation(data[0].id);
		}).catch(() => {
			if (!cancelled) setHistoryError("Conversation history is unavailable. Try refreshing.");
		}).finally(() => {
			if (!cancelled) setHistoryLoading(false);
		});
		return () => { cancelled = true; };
	}, [currentUserId]);

	useEffect(() => {
		let cancelled = false;
		void getProviderStatus().then(({ data }) => {
			if (!cancelled) setProviderReady(data.enabled && data.reachable && data.model_installed);
		}).catch(() => {
			if (!cancelled) setProviderReady(false);
		});
		return () => { cancelled = true; };
	}, [currentUserId]);

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

	useEffect(() => {
		if (followLatestRef.current && messagesListRef.current) {
			messagesListRef.current.scrollTop = messagesListRef.current.scrollHeight;
		}
	}, [messages]);


	// Navigating away cancels an in-flight question instead of leaving the local
	// model generating for a page that is already gone.
	useEffect(() => () => abortRef.current?.abort(), []);

	const runAskQuestion = async (query: string) => {
		if (isSearching) {
			return;
		}

		setIsSearching(true);
		let targetConversationId = activeConversationId;
		if (!targetConversationId) {
			try {
				const created = await createConversation(undefined, chatMode);
				targetConversationId = created.data.id;
				setActiveConversationId(created.data.id);
				setConversations((current) => [created.data, ...current]);
			} catch {
				setHistoryError("A new conversation could not be created. Try again.");
				setIsSearching(false);
				return;
			}
		}
		const namedById = new Map<number, MessageAttachment>();
		for (const message of messages) {
			for (const attachment of message.attachments ?? []) {
				namedById.set(attachment.id, attachment);
			}
		}
		for (const document of documents) {
			namedById.set(document.id, {
				id: document.id,
				name: document.original_filename,
				typeLabel: fileTypeLabel(document),
			});
		}
		const attachments = scopeIds.map(
			(id) =>
				namedById.get(id) ?? {
					id,
					name: `Document ${id}`,
					typeLabel: "File",
				}
		);
		setMessages((current) => [
			...current,
			{
				type: "user",
				text: query,
				attachments: attachments.length > 0 ? attachments : undefined,
			},
			{ type: "ai", text: "", hasEvidence: false, status: "streaming" },
		]);
		setQuestion("");

		// A long local generation can outlast the user's patience; leaving this
		// page, or pressing Stop, cancels the request instead of orphaning it.
		const controller = new AbortController();
		abortRef.current = controller;
		let streamReportedError = false;

		try {
			await streamConversationMessage(
				targetConversationId,
				query,
				scopeIds.length > 0 ? scopeIds : undefined,
				crypto.randomUUID(),
				chatMode,
				controller.signal,
				(event) => {
					if (event.type === "delta") {
						const delta = String(event.data.text ?? "");
						setMessages((current) => current.map((message, index) =>
							index === current.length - 1 && message.status === "streaming"
								? { ...message, text: message.text + delta }
								: message
						));
					} else if (event.type === "complete") {
						const stored = event.data.message as StoredChatMessage;
						const completed = fromStoredMessage(stored);
						completed.notes = typeof event.data.notes === "string" ? event.data.notes : undefined;
						setMessages((current) => current.map((message, index) =>
							index === current.length - 1 ? completed : message
						));
						setConversations((current) => current.map((item) =>
							item.id === targetConversationId
								? { ...item, title: item.title === "New conversation" ? query.slice(0, 120) : item.title, updated_at: new Date().toISOString() }
								: item
						));
					} else if (event.type === "error") {
						streamReportedError = true;
						const messageText = String(event.data.message ?? "The answer could not be generated. Try again.");
						setMessages((current) => current.map((message, index) =>
							index === current.length - 1 ? { ...message, text: messageText, status: "failed" } : message
						));
					}
				},
			);
		} catch (error) {
			if (!streamReportedError) {
				const stopped = isRequestCanceled(error);
				setMessages((current) => current.map((message, index) =>
					index === current.length - 1
						? { ...message, text: stopped ? "Generation stopped." : describeAskFailure(error), status: stopped ? "interrupted" : "failed" }
						: message
				));
			}
		} finally {
			abortRef.current = null;
			setIsSearching(false);
		}
	};

	const copyAnswer = async (text: string, index: number) => {
		try {
			if (!navigator.clipboard?.writeText) {
				throw new Error("Clipboard access is unavailable");
			}
			await navigator.clipboard.writeText(text);
			setCopyFeedback({ index, text: "Copied" });
		} catch {
			setCopyFeedback({ index, text: "Clipboard access unavailable" });
		}
	};

	const stopRequest = () => abortRef.current?.abort();

	// The conversation is kept deliberately, so starting a new one is the only
	// way it is cleared - and it also releases the document scope.
	const newConversation = () => {
		abortRef.current?.abort();
		setIsSearching(false);
		void createConversation(undefined, chatMode).then(({ data }) => {
			setActiveConversationId(data.id);
			setChatMode(data.chat_mode);
			setConversations((current) => [data, ...current]);
			setMessages([]);
			setScopeIds([]);
		setNextBeforeId(null);
			setHistoryError("");
			composerRef.current?.focus();
		}).catch(() => setHistoryError("A new conversation could not be created. Try again."));
	};

	const renameSelectedConversation = async (item: ConversationSummary) => {
		const title = window.prompt("Rename conversation", item.title);
		if (title == null || !title.trim()) return;
		try {
			const { data } = await renameConversation(item.id, title.trim());
			setConversations((current) => current.map((entry) => entry.id === data.id ? data : entry));
		} catch {
			setHistoryError("The conversation title could not be saved.");
		}
	};

	const removeConversation = async (item: ConversationSummary) => {
		if (!window.confirm(`Delete “${item.title}” and its messages? This cannot be undone.`)) return;
		try {
			await deleteConversation(item.id);
			const remaining = conversations.filter((entry) => entry.id !== item.id);
			setConversations(remaining);
			if (activeConversationId === item.id) {
				setActiveConversationId(null);
				setMessages([]);
				setScopeIds([]);
				setNextBeforeId(null);
				if (remaining[0]) void openConversation(remaining[0].id);
			}
		} catch {
			setHistoryError("The conversation could not be deleted.");
		}
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
					<strong style={{ fontSize: "1rem", fontWeight: 600 }}>Ask DocSentinel</strong>
					<div className="chatModeToggle" role="group" aria-label="Answer mode">
						<button type="button" className={chatMode === "documents" ? "active" : ""} aria-pressed={chatMode === "documents"} onClick={() => setChatMode("documents")}>Your documents</button>
						<button type="button" className={chatMode === "general" ? "active" : ""} aria-pressed={chatMode === "general"} onClick={() => setChatMode("general")}>General</button>
					</div>
					{chatMode === "documents" && scopeIds.length > 0 && (
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
				<span className={providerReady === true ? "providerStatus ready" : "providerStatus"} role="status" title={providerReady === true ? "The configured local model is available." : "The configured local model is currently unavailable."}>
					{providerReady === null ? "Checking model…" : providerReady ? "Local model ready" : "Local model unavailable"}
				</span>
				<button
					type="button"
					className="newConversationButton"
					onClick={newConversation}
					disabled={isSearching}
					title="Start a new conversation."
				>
					New conversation
				</button>
			</div>

			<div className="chatContainer">
				<aside className="conversationSidebar" aria-label="Conversation history">
					<div className="conversationSidebarHeader">
						<strong>Recent conversations</strong>
						<button type="button" onClick={newConversation} aria-label="Start a new conversation" title="Start a new conversation">+</button>
					</div>
					{historyError && <p className="conversationHistoryError" role="alert">{historyError}</p>}
					{historyLoading ? (
						<p className="conversationHistoryEmpty" role="status">Loading conversations…</p>
					) : conversations.length === 0 ? (
						<p className="conversationHistoryEmpty">Your conversations will appear here.</p>
					) : (
						<ul className="conversationList">
							{conversations.map((item) => (
								<li key={item.id} className={item.id === activeConversationId ? "conversationListItem active" : "conversationListItem"}>
										<button type="button" className="conversationSelect" aria-label={item.title} onClick={() => void openConversation(item.id)} aria-current={item.id === activeConversationId ? "page" : undefined}>
										<span>{item.title}</span>
										<small>{new Date(item.updated_at).toLocaleDateString()}</small>
									</button>
									<div className="conversationActions">
										<button type="button" onClick={() => void renameSelectedConversation(item)} aria-label={`Rename ${item.title}`} title="Rename">Rename</button>
										<button type="button" onClick={() => void removeConversation(item)} aria-label={`Delete ${item.title}`} title="Delete">Delete</button>
									</div>
								</li>
							))}
						</ul>
					)}
				</aside>
				<div className="chatMain">
				<div className="chatArea">
					{messages.length === 0 ? (
						<div className="chatWelcome">
							<div className="aiWelcomeIcon">
								<AutoAwesomeOutlinedIcon />
							</div>
							<h2>Ask DocSentinel</h2>
							<p>{chatMode === "documents" ? "Ask about documents you can access. Answers are grounded in retrieved passages and link back to their sources." : "Ask a general question. This mode does not search your document library."}</p>
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
							ref={messagesListRef}
							onScroll={(event) => {
								const node = event.currentTarget;
								followLatestRef.current = node.scrollHeight - node.scrollTop - node.clientHeight < 100;
							}}
							role="log"
							aria-live="polite"
							aria-label="Conversation"
						>
							{nextBeforeId != null && <button type="button" className="loadOlderMessages" onClick={() => void loadOlderMessages()}>Load earlier messages</button>}
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
										{message.type === "user" &&
											message.attachments &&
											message.attachments.length > 0 && (
												<div
													className="messageFileCards"
													aria-label="Documents attached to this question"
												>
													{message.attachments.map((attachment) => (
														<DocumentFileCard
															key={attachment.id}
															name={attachment.name}
															typeLabel={attachment.typeLabel}
														/>
													))}
												</div>
											)}
						<div className="messageBubble" style={{ whiteSpace: "pre-wrap" }}>
																{message.text || (message.status === "streaming" ? (chatMode === "documents" ? "Retrieving authorized passages and waiting for verified text…" : "Waiting for the local model…") : "")}
										</div>
										{message.type === "ai" && message.engine && (
											<span className="answerEngine">
												{describeEngine(message.engine)}
											</span>
										)}
										{message.notes && (
											<span className="answerNotes">{message.notes}</span>
										)}
										{message.type === "ai" && message.status !== "streaming" && message.status !== "failed" && message.text && (
												<>
													<button type="button" className="messageAction" onClick={() => void copyAnswer(message.text, index)} aria-label="Copy answer">
														{copyFeedback?.index === index && copyFeedback.text === "Copied" ? "Copied" : "Copy"}
													</button>
													{copyFeedback?.index === index && copyFeedback.text !== "Copied" && (
														<span role="status" className="answerNotes">{copyFeedback.text}</span>
													)}
												</>
										)}
										{message.type === "ai" && message.status === "failed" && (
											<button type="button" className="messageAction" onClick={() => {
												const previousQuestion = [...messages.slice(0, index)].reverse().find((entry) => entry.type === "user")?.text;
												if (previousQuestion) void runAskQuestion(previousQuestion);
											}} disabled={isSearching}>Try again</button>
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
						</div>
					)}
				</div>

				<div className="chatBottom">
					{chatMode === "documents" && pickerOpen && (
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
					{chatMode === "general" ? (
						<div className="selectedDocument"><span>General question · documents are not searched</span></div>
					) : scopeIds.length === 0 ? (
						<div className="selectedDocument">
							<DescriptionOutlinedIcon />
							<span>All documents</span>
						</div>
					) : (
						<div className="attachmentStrip">
							{selectedDocuments.map((document) => (
								<DocumentFileCard
									key={document.id}
									name={document.original_filename}
									typeLabel={fileTypeLabel(document)}
									status={statusLabel(document.status)}
									department={document.department}
									title={`${document.original_filename} - OCR text, extracted fields and indexed embeddings contribute to RAG`}
									removeLabel={`Detach ${document.original_filename} from this conversation`}
									onRemove={() => toggleDocument(document.id)}
								/>
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
						{chatMode === "documents" && <button
							type="button"
							className="attachButton"
							aria-label="Choose documents to search"
							aria-expanded={pickerOpen}
							aria-controls="docSelector"
							title="Limit the search to specific documents"
							onClick={() => setPickerOpen((open) => !open)}
						>
						<AddOutlinedIcon />
						</button>}
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
							placeholder={chatMode === "documents" ? "Ask anything about your documents..." : "Ask a general question..."}
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
						{chatMode === "documents" ? "Answers use authorized document evidence. Check cited sources before acting." : "General answers are generated by the configured local model and may need verification."}
					</span>
				</div>
				</div>
			</div>
		</div>
	);
};

export default AskQuestion;
