import { useState, useRef, useEffect } from "react";
import "./App.css";
import { FiPaperclip, FiMic, FiSend } from "react-icons/fi";

function App() {
  const [input, setInput] = useState("");
  const [messages, setMessages] = useState([]);
  const chatEndRef = useRef(null);

  const sendMessage = async () => {
    if (!input.trim()) return;

    const userMsg = { type: "user", text: input };

    try {
      const res = await fetch("http://localhost:8000/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ message: input }),
      });

      const data = await res.json();

      const botMsg = {
        type: "bot",
        text: data.response
          ? data.response.split("\n")
          : ["No response"],
      };

      setMessages((prev) => [...prev, userMsg, botMsg]);
    } catch {
      setMessages((prev) => [
        ...prev,
        userMsg,
        { type: "bot", text: ["Error connecting to backend"] },
      ]);
    }

    setInput("");
  };

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="page">
      <div className="centerContent">
        <h1 className="bigTitle">SJSU COPILOT</h1>

        <div className="chatArea">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`message ${msg.type === "user" ? "user" : "bot"}`}
            >
              {Array.isArray(msg.text)
                ? msg.text.map((line, idx) => (
                    <div key={idx}>{line}</div>
                  ))
                : msg.text}
            </div>
          ))}
          <div ref={chatEndRef} />
        </div>
      </div>

      <div className="inputContainer">
        <FiPaperclip className="icon" />

        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Send a message..."
          onKeyDown={(e) => e.key === "Enter" && sendMessage()}
        />

        <FiMic className="icon" />

        <button className="sendBtn" onClick={sendMessage}>
          <FiSend />
        </button>
      </div>
    </div>
  );
}

export default App;