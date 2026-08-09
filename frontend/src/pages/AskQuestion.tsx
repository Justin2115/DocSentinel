import { useState } from "react";
import SendOutlinedIcon from "@mui/icons-material/SendOutlined";
import AutoAwesomeOutlinedIcon from "@mui/icons-material/AutoAwesomeOutlined";
import DescriptionOutlinedIcon from "@mui/icons-material/DescriptionOutlined";
import AddOutlinedIcon from "@mui/icons-material/AddOutlined";

const sampleQuestions = [
  "What are the key points in my documents?",
  "Summarize the latest financial report",
  "Find all invoices from July",
  "Which documents need review?",
];

const AskQuestion = () => {
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<
    { type: "user" | "ai"; text: string }[]
  >([]);

  const handleSampleQuestion = (sample: string) => {
    setQuestion(sample);
  };

  const handleSend = () => {
    const trimmedQuestion = question.trim();

    if (!trimmedQuestion) return;

    setMessages((previous) => [
      ...previous,
      {
        type: "user",
        text: trimmedQuestion,
      },
    ]);

    setQuestion("");
  };

  return (
    <div className="askQuestionPage">
      <div className="askQuestionHeader">
        <div>
          <h1>Ask a Question</h1>
          <p>
            Ask questions about your documents and get AI-powered answers.
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
                I'll help you find the information you need.
              </p>

              <div className="sampleQuestions">
                <span className="sampleQuestionsTitle">
                  Try asking
                </span>

                <div className="sampleQuestionGrid">
                  {sampleQuestions.map((sample) => (
                    <button
                      key={sample}
                      type="button"
                      className="sampleQuestion"
                      onClick={() => handleSampleQuestion(sample)}
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

                  <div className="messageBubble">
                    {message.text}
                  </div>
                </div>
              ))}
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
              disabled={!question.trim()}
              aria-label="Send question"
            >
              <SendOutlinedIcon />
            </button>
          </div>

          <span className="aiDisclaimer">
            AI-generated answers may contain inaccuracies. Verify important
            information.
          </span>
        </div>
      </div>
    </div>
  );
};

export default AskQuestion;