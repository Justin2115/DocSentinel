import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Checkbox, FormControlLabel } from "@mui/material";

import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";

import { askDocuments } from "../api/search";
import type { AnswerCitation } from "../api/search";

const sampleQuestions = [
	"What are the key points in my documents?",
	"Summarize the latest financial report",
	"Find all invoices from July",
	"Which documents need review?",
];

type Message = {
	type: "user" | "ai";
	text: string;
	citations?: AnswerCitation[];
	evidenceStatus?: string;
};

const AskQuestion = () => {
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const [question, setQuestion] = useState("");
	const [messages, setMessages] = useState<Message[]>([]);
	const [isSearching, setIsSearching] = useState(false);
	const [includeCitations, setIncludeCitations] = useState(true);
	const processedQuery = useRef<string | null>(null);

	const runQuestion = async (query: string) => {
		setIsSearching(true);
		setMessages((previous) => [
			...previous,
			{ type: "user", text: query },
		]);
		setQuestion("");

		try {
			const response = await askDocuments(query, includeCitations);
			setMessages((previous) => [
				...previous,
				{
					type: "ai",
					text: response.data.answer,
					citations: response.data.citations,
					evidenceStatus: response.data.evidence_status,
				},
			]);
		} catch {
			setMessages((previous) => [
				...previous,
				{
					type: "ai",
					text: "Document search is unavailable right now. Check the connection and try again.",
				},
			]);
		} finally {
			setIsSearching(false);
		}
	};

	useEffect(() => {
		const query = searchParams.get("q");
		if (!query || processedQuery.current === query) {
			return;
		}
		processedQuery.current = query;
		void runQuestion(query);
	}, [searchParams]);

	const handleSend = () => {
		const trimmedQuestion = question.trim();
		if (!trimmedQuestion || isSearching) {
			return;
		}
		void runQuestion(trimmedQuestion);
	};

	return (
		<div className="askQuestionPage">
			<div className="askQuestionHeader">
				<div>
					<h1>Ask a Question</h1>
					<p>
						Search across your documents using meaning, not just keywords.
					</p>
				</div>
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
						<div className="messagesArea">
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
										<div className="messageBubble">
											{message.type === "ai"
												? message.text.split(/(\[S\d+\])/g).map((part, partIndex) => {
													const citation = message.citations?.find((item) => `[${item.source_id}]` === part);
													return citation ? (
														<button key={`${part}-${partIndex}`} className="inlineCitation" type="button" onClick={() => navigate(citation.href)}>{part}</button>
													) : <span key={`${part}-${partIndex}`}>{part}</span>;
												})
												: message.text}
										</div>
										{message.evidenceStatus === "retrieved_only" && <span className="aiDisclaimer">Answer generation is not configured; these are retrieved source passages.</span>}
										{message.citations && message.citations.length > 0 && (
											<div className="sourceCards">
												{message.citations.map((citation) => (
													<button
														key={citation.source_id}
														type="button"
														className="sourceCard"
														onClick={() => navigate(citation.href)}
													>
														<div className="sourceCardHeader">
															<strong>{citation.document_title}</strong>
															<span className="similarityBadge">{citation.source_id}</span>
														</div>
														<span className="sourceCardMeta">
															{citation.page_number ? `Page ${citation.page_number}` : "Document"}
														</span>
														<p>{citation.excerpt}</p>
													</button>
												))}
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
									<div className="messageBubble">Searching documents...</div>
								</div>
							)}
						</div>
					)}
				</div>

				<div className="chatBottom">
					<FormControlLabel
						control={<Checkbox checked={includeCitations} onChange={(event) => setIncludeCitations(event.target.checked)} size="small" />}
						label="Include source citations"
					/>
					<div className="selectedDocument">
						<DescriptionOutlinedIcon />
						<span>All documents</span>
					</div>
					<div className="questionInputWrapper">
						<button
							type="button"
							className="attachButton"
							aria-label="Attach document"
						>
							<AddOutlinedIcon />
						</button>
						<input
							type="text"
							value={question}
							onChange={(event) => setQuestion(event.target.value)}
							onKeyDown={(event) => {
								if (event.key === "Enter") {
									handleSend();
								}
							}}
							placeholder="Ask anything about your documents..."
						/>
						<button
							type="button"
							className="sendButton"
							onClick={handleSend}
							disabled={!question.trim() || isSearching}
							aria-label="Send question"
						>
							<SendOutlinedIcon />
						</button>
					</div>
					<span className="aiDisclaimer">
						Answers use retrieved passages when a chat model is configured. Verify important information in the linked sources.
					</span>
				</div>
			</div>
		</div>
	);
};

export default AskQuestion;
