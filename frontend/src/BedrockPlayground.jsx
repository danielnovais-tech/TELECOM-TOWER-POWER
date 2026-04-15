import { useState, useEffect, useRef, useCallback } from "react";
import { bedrockChat, fetchBedrockModels } from "./api";

export default function BedrockPlayground({ analysisResult, selectedTower }) {
  const [models, setModels] = useState([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(1024);
  const [prompt, setPrompt] = useState("");
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [includeContext, setIncludeContext] = useState(true);
  const messagesEndRef = useRef(null);

  useEffect(() => {
    fetchBedrockModels()
      .then((data) => {
        const list = data?.models || [];
        setModels(list);
        if (list.length > 0 && !selectedModel) {
          setSelectedModel(list[0].model_id);
        }
      })
      .catch(() => {
        setModels([
          { model_id: "amazon.titan-text-express-v1", provider: "Amazon", name: "Titan Text Express" },
          { model_id: "amazon.titan-text-lite-v1", provider: "Amazon", name: "Titan Text Lite" },
          { model_id: "anthropic.claude-3-haiku-20240307-v1:0", provider: "Anthropic", name: "Claude 3 Haiku" },
        ]);
        if (!selectedModel) setSelectedModel("amazon.titan-text-express-v1");
      });
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const contextJson = useCallback(() => {
    if (!includeContext) return undefined;
    const ctx = {};
    if (analysisResult) ctx.analysis = analysisResult;
    if (selectedTower) ctx.tower = selectedTower;
    return Object.keys(ctx).length > 0 ? JSON.stringify(ctx) : undefined;
  }, [includeContext, analysisResult, selectedTower]);

  async function handleSend() {
    const text = prompt.trim();
    if (!text || loading) return;

    const userMsg = { role: "user", content: text };
    setMessages((prev) => [...prev, userMsg]);
    setPrompt("");
    setLoading(true);
    setError(null);

    try {
      const result = await bedrockChat({
        prompt: text,
        model_id: selectedModel || undefined,
        max_tokens: maxTokens,
        temperature,
        context: contextJson(),
      });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.response,
          model_id: result.model_id,
          tokens: { input: result.input_tokens, output: result.output_tokens },
        },
      ]);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  const quickPrompts = [
    "Explain this link analysis result",
    "Is the signal strength adequate for FWA?",
    "Suggest improvements for Fresnel clearance",
    "What antenna height would improve this link?",
    "Compare 700MHz vs 3500MHz for this distance",
  ];

  return (
    <div className="bedrock-playground">
      <div className="bedrock-sidebar">
        <section className="panel">
          <h3>AI Playground</h3>
          <p className="bedrock-desc">
            Powered by Amazon Bedrock base foundation models.
            Ask questions about RF analysis, link budgets, and telecom planning.
          </p>
        </section>

        <section className="panel">
          <h3>Model</h3>
          <select
            className="bedrock-select"
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
          >
            {models.map((m) => (
              <option key={m.model_id} value={m.model_id}>
                {m.provider} — {m.name}
              </option>
            ))}
          </select>
        </section>

        <section className="panel">
          <h3>Parameters</h3>
          <div className="field">
            <label>Temperature</label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={temperature}
              onChange={(e) => setTemperature(Number(e.target.value))}
            />
            <span className="param-val">{temperature.toFixed(2)}</span>
          </div>
          <div className="field">
            <label>Max Tokens</label>
            <input
              type="number"
              min={64}
              max={4096}
              step={64}
              value={maxTokens}
              onChange={(e) => setMaxTokens(Number(e.target.value))}
            />
          </div>
        </section>

        <section className="panel">
          <h3>Context</h3>
          <label className="bedrock-toggle">
            <input
              type="checkbox"
              checked={includeContext}
              onChange={(e) => setIncludeContext(e.target.checked)}
            />
            <span>Include current analysis</span>
          </label>
          {includeContext && (analysisResult || selectedTower) && (
            <div className="context-summary">
              {selectedTower && <span>Tower: {selectedTower.id}</span>}
              {analysisResult && (
                <span>Signal: {analysisResult.signal_dbm?.toFixed(1)} dBm</span>
              )}
            </div>
          )}
          {includeContext && !analysisResult && !selectedTower && (
            <p className="muted">No analysis context — run a link analysis first</p>
          )}
        </section>

        <section className="panel">
          <h3>Quick Prompts</h3>
          <div className="quick-prompts">
            {quickPrompts.map((qp) => (
              <button
                key={qp}
                className="quick-prompt-btn"
                onClick={() => setPrompt(qp)}
              >
                {qp}
              </button>
            ))}
          </div>
        </section>
      </div>

      <div className="bedrock-chat">
        <div className="bedrock-messages">
          {messages.length === 0 && (
            <div className="bedrock-empty">
              <h2>Amazon Bedrock Playground</h2>
              <p>Select a foundation model and start asking telecom engineering questions.</p>
              <p className="muted">PRO or ENTERPRISE tier required.</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`bedrock-msg bedrock-msg--${msg.role}`}>
              <div className="msg-role">{msg.role === "user" ? "You" : "AI"}</div>
              <div className="msg-content">{msg.content}</div>
              {msg.tokens && (
                <div className="msg-meta">
                  {msg.model_id} · {msg.tokens.input + msg.tokens.output} tokens
                </div>
              )}
            </div>
          ))}
          {loading && (
            <div className="bedrock-msg bedrock-msg--assistant">
              <div className="msg-role">AI</div>
              <div className="msg-content typing">Thinking…</div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {error && (
          <div className="bedrock-error">
            {error}
          </div>
        )}

        <div className="bedrock-input-bar">
          <textarea
            className="bedrock-input"
            placeholder="Ask about RF analysis, link budgets, antenna planning…"
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            disabled={loading}
          />
          <button
            className="btn primary bedrock-send"
            disabled={!prompt.trim() || loading}
            onClick={handleSend}
          >
            {loading ? "…" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
