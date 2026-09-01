import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";

import { searchSemantic } from "../api/search";
import type { SemanticSearchHit } from "../api/search";

const sampleQuestions = [
	"What are the key points in my documents?",
	"Summarize the latest financial report",
	"Find all invoices from July",
	"Which documents need review?",
];

type Message = {
	type: "user" | "ai";
	text: string;
	hits?: SemanticSearchHit[];
};

const AskQuestion = () => {
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const [question, setQuestion] = useState("");
	const [messages, setMessages] = useState<Message[]>([]);
	const [isSearching, setIsSearching] = useState(false);
	const processedQuery = useRef<string | null>(null);

	const runSemanticSearch = async (query: string) => {
		setIsSearching(true);
		setMessages((previous) => [
			...previous,
			{ type: "user", text: query },
		]);
		setQuestion("");

		try {
			const response = await searchSemantic(query);
			const hits = response.data.items;
			setMessages((previous) => [
				...previous,
				{
					type: "ai",
					text: hits.length
						? `Found ${hits.length} related passage${hits.length === 1 ? "" : "s"} in your documents.`
						: "No semantic matches. Upload and index documents, or try a different question.",
					hits,
				},
			]);
		} catch {
			setMessages((previous) => [
				...previous,
				{
					type: "ai",
					text: "Semantic search is unavailable right now. Make sure the API is running and documents are indexed.",
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
		void runSemanticSearch(query);
	}, [searchParams]);

	const handleSend = () => {
		const trimmedQuestion = question.trim();
		if (!trimmedQuestion || isSearching) {
			return;
		}
		void runSemanticSearch(trimmedQuestion);
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
										<div className="messageBubble">{message.text}</div>
										{message.hits && message.hits.length > 0 && (
											<div className="sourceCards">
												{message.hits.map((hit) => (
													<button
														key={`${hit.document_id}-${hit.page_number}-${hit.chunk_index}`}
														type="button"
														className="sourceCard"
														onClick={() =>
															navigate(
																`/library?preview=${hit.document_id}`
															)
														}
													>
														<div className="sourceCardHeader">
															<strong>{hit.document_name}</strong>
															<span className="similarityBadge">
																{Math.round(hit.similarity * 100)}%
															</span>
														</div>
														<span className="sourceCardMeta">
															Page {hit.page_number}
														</span>
														<p>{hit.snippet}</p>
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
						Results are retrieved passages, not generated answers. Verify
						important information.
					</span>
				</div>
			</div>
		</div>
	);
};

export default AskQuestion;
