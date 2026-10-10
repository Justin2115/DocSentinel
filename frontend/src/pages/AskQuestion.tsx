import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";

import { askQuestion } from "../api/query";
import type { SourceCitation } from "../api/query";

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
};

const AskQuestion = () => {
	const [searchParams] = useSearchParams();
	const navigate = useNavigate();
	const [question, setQuestion] = useState("");
	const [messages, setMessages] = useState<Message[]>([]);
	const [isSearching, setIsSearching] = useState(false);
	const processedQuery = useRef<string | null>(null);

	const runAskQuestion = async (query: string) => {
		setIsSearching(true);
		setMessages((previous) => [
			...previous,
			{ type: "user", text: query },
		]);
		setQuestion("");

		try {
			const response = await askQuestion({ question: query });
			const { answer, sources, has_evidence, notes } = response.data;
			setMessages((previous) => [
				...previous,
				{
					type: "ai",
					text: answer,
					sources,
					hasEvidence: has_evidence,
					notes,
				},
			]);
		} catch {
			setMessages((previous) => [
				...previous,
				{
					type: "ai",
					text: "Question answering is unavailable right now. Make sure the API is running and documents are indexed.",
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
										<div className="messageBubble" style={{ whiteSpace: "pre-wrap" }}>
											{message.text}
										</div>
										{message.notes && (
											<span style={{ fontSize: "0.75rem", color: "#6b7280", marginTop: "4px" }}>
												{message.notes}
											</span>
										)}
										{message.sources && message.sources.length > 0 && (
											<div className="sourceCards">
												{message.sources.map((source, sIdx) => (
													<button
														key={`${source.document_id}-${source.page_number}-${sIdx}`}
														type="button"
														className="sourceCard"
														onClick={() =>
															navigate(
																`/library?preview=${source.document_id}`
															)
														}
													>
														<div className="sourceCardHeader">
															<strong>{source.document_name}</strong>
															<span className="similarityBadge">
																Score: {source.similarity.toFixed(2)}
															</span>
														</div>
														{source.page_number != null && (
															<span className="sourceCardMeta">
																Page {source.page_number}
															</span>
														)}
														<p>{source.snippet}</p>
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
									<div className="messageBubble">Searching and synthesizing answer from documents...</div>
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
						Answers are grounded strictly in authorized document passages. Verify important information.
					</span>
				</div>
			</div>
		</div>
	);
};

export default AskQuestion;
