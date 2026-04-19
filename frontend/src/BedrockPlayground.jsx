import { useState, useEffect, useRef, useCallback } from "react";
import { bedrockChat, fetchBedrockModels, bedrockCompare, bedrockBatchAnalyze, bedrockSuggestHeight } from "./api";

/* ── lightweight Markdown → HTML (bold, headers, bullets, code, tables) ── */
function renderMarkdown(text) {
  if (!text) return "";
  let html = text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    // code blocks
    .replace(/```([\s\S]*?)```/g, '<pre class="md-code-block">$1</pre>')
    // inline code
    .replace(/`([^`]+)`/g, '<code class="md-inline-code">$1</code>')
    // headers
    .replace(/^#### (.+)$/gm, '<strong class="md-h4">$1</strong>')
    .replace(/^### (.+)$/gm, '<strong class="md-h3">$1</strong>')
    .replace(/^## (.+)$/gm, '<strong class="md-h2">$1</strong>')
    // bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // italic
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    // bullet lists
    .replace(/^[-•] (.+)$/gm, '<li class="md-li">$1</li>')
    // numbered lists
    .replace(/^\d+\.\s(.+)$/gm, '<li class="md-li-num">$1</li>')
    // horizontal rule
    .replace(/^---+$/gm, '<hr class="md-hr"/>')
    // line breaks
    .replace(/\n/g, "<br/>");
  return html;
}

const MODE_CHAT = "chat";
const MODE_COMPARE = "compare";
const MODE_BATCH = "batch";

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
  const [mode, setMode] = useState(MODE_CHAT);
  const [targetClearance, setTargetClearance] = useState(0.6);
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
          { model_id: "amazon.nova-micro-v1:0", provider: "Amazon", name: "Nova Micro" },
          { model_id: "amazon.nova-lite-v1:0", provider: "Amazon", name: "Nova Lite" },
          { model_id: "anthropic.claude-haiku-4-5-20251001-v1:0", provider: "Anthropic", name: "Claude Haiku 4.5" },
        ]);
        if (!selectedModel) setSelectedModel("amazon.nova-micro-v1:0");
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

  /* ── build two frequency scenarios from current analysis ── */
  const buildFrequencyScenarios = useCallback(() => {
    if (!analysisResult || !selectedTower) return null;
    const base = { ...analysisResult };
    const tower = { ...selectedTower };
    const freq = tower.frequency_mhz || 700;
    const altFreq = freq < 2000 ? 3500 : 700;
    return [
      { label: `${freq} MHz (current)`, frequency_mhz: freq, ...base },
      { label: `${altFreq} MHz (alternative)`, frequency_mhz: altFreq, ...base,
        signal_dbm: base.signal_dbm + (freq < altFreq ? -8 : 8),
        fresnel_clearance: base.fresnel_clearance * (freq < altFreq ? 1.3 : 0.75),
      },
    ];
  }, [analysisResult, selectedTower]);

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

  async function handleCompareScenarios() {
    const scenarios = buildFrequencyScenarios();
    if (!scenarios) {
      setError("Run a link analysis first to compare frequency scenarios.");
      return;
    }
    setLoading(true);
    setError(null);
    const userMsg = { role: "user", content: `Compare scenarios: ${scenarios.map(s => s.label).join(" vs ")}` };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const result = await bedrockCompare({
        scenarios,
        question: prompt.trim() || undefined,
        model_id: selectedModel || undefined,
        max_tokens: maxTokens,
        temperature,
      });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.response,
          model_id: result.model_id,
          tokens: { input: result.input_tokens, output: result.output_tokens },
          type: "comparison",
        },
      ]);
      setPrompt("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSuggestHeight() {
    if (!analysisResult || !selectedTower) {
      setError("Run a link analysis first to get antenna height suggestions.");
      return;
    }
    setLoading(true);
    setError(null);
    const userMsg = { role: "user", content: `Suggest antenna height for ${(targetClearance * 100).toFixed(0)}% Fresnel clearance` };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const result = await bedrockSuggestHeight({
        analysis: analysisResult,
        tower: selectedTower,
        target_clearance: targetClearance,
        model_id: selectedModel || undefined,
      });
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: result.response,
          model_id: result.model_id,
          tokens: { input: result.input_tokens, output: result.output_tokens },
          type: "suggestion",
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
      if (mode === MODE_COMPARE) handleCompareScenarios();
      else handleSend();
    }
  }

  const quickPrompts = [
    { label: "Explain this link analysis", prompt: "Explain this link analysis result in detail. Is the signal adequate? How is the Fresnel zone clearance?" },
    { label: "Is signal adequate for FWA?", prompt: "Is the signal strength adequate for Fixed Wireless Access (FWA)? What throughput can I expect?" },
    { label: "Improve Fresnel clearance", prompt: "What are the most cost-effective ways to improve the Fresnel zone clearance for this link?" },
    { label: "Optimal antenna height", prompt: "What antenna height would I need to achieve reliable Fresnel zone clearance for this link? Consider Earth curvature." },
    { label: "700 MHz vs 3500 MHz", prompt: "Compare 700 MHz vs 3500 MHz for this specific link. Consider Fresnel zone size, path loss, capacity, and practical deployment." },
    { label: "Repeater chain needed?", prompt: "Does this link need a repeater chain? If so, how many hops and where should repeaters be placed?" },
    { label: "Rain fade margin", prompt: "What rain fade margin should I add for 99.99% availability on this link? Consider the frequency and distance." },
    { label: "Link budget breakdown", prompt: "Provide a complete link budget breakdown for this link: EIRP, FSPL, gains, losses, and margin." },
  ];

  const hasContext = analysisResult || selectedTower;

  return (
    <div className="bedrock-playground">
      <div className="bedrock-sidebar">
        <section className="panel">
          <h3>AI Playground</h3>
          <p className="bedrock-desc">
            Powered by Amazon Bedrock foundation models with RAG-enhanced domain knowledge.
            Ask questions about RF analysis, get engineering recommendations, compare scenarios.
          </p>
        </section>

        {/* Mode selector */}
        <section className="panel">
          <h3>Mode</h3>
          <div className="bedrock-modes">
            <button className={`mode-btn ${mode === MODE_CHAT ? "active" : ""}`} onClick={() => setMode(MODE_CHAT)}>
              Chat
            </button>
            <button className={`mode-btn ${mode === MODE_COMPARE ? "active" : ""}`} onClick={() => setMode(MODE_COMPARE)}>
              Compare
            </button>
            <button className={`mode-btn ${mode === MODE_BATCH ? "active" : ""}`} onClick={() => setMode(MODE_BATCH)}>
              Batch AI
            </button>
          </div>
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
          {includeContext && hasContext && (
            <div className="context-summary">
              {selectedTower && (
                <>
                  <span>Tower: {selectedTower.id || selectedTower.tower_id}</span>
                  <span>Operator: {selectedTower.operator}</span>
                  {selectedTower.frequency_mhz && <span>Freq: {selectedTower.frequency_mhz} MHz</span>}
                  {selectedTower.height_m && <span>Height: {selectedTower.height_m} m</span>}
                </>
              )}
              {analysisResult && (
                <>
                  <span>Signal: {analysisResult.signal_dbm?.toFixed(1)} dBm</span>
                  <span>Fresnel: {(analysisResult.fresnel_clearance * 100)?.toFixed(0)}%</span>
                  <span>Distance: {analysisResult.distance_km?.toFixed(2)} km</span>
                  <span>LOS: {analysisResult.los_ok ? "Clear" : "Obstructed"}</span>
                  <span className={analysisResult.feasible ? "ctx-feasible" : "ctx-unfeasible"}>
                    {analysisResult.feasible ? "✓ Feasible" : "✗ Not Feasible"}
                  </span>
                </>
              )}
            </div>
          )}
          {includeContext && !hasContext && (
            <p className="muted">No analysis context — run a link analysis first</p>
          )}
        </section>

        {/* Mode-specific controls */}
        {mode === MODE_COMPARE && (
          <section className="panel">
            <h3>Scenario Comparison</h3>
            <p className="bedrock-desc">
              Compares RF scenarios (e.g. 700 MHz vs 3500 MHz) using AI reasoning
              over Fresnel zone, path loss, and capacity trade-offs.
            </p>
            {hasContext ? (
              <button className="btn primary" onClick={handleCompareScenarios} disabled={loading}>
                {loading ? "Analyzing…" : "Compare Frequencies"}
              </button>
            ) : (
              <p className="muted">Run a link analysis to enable comparison</p>
            )}
          </section>
        )}

        {mode === MODE_COMPARE && (
          <section className="panel">
            <h3>Antenna Height Advisor</h3>
            <div className="field">
              <label>Target Fresnel Clearance</label>
              <input
                type="range"
                min={0.4}
                max={1.0}
                step={0.05}
                value={targetClearance}
                onChange={(e) => setTargetClearance(Number(e.target.value))}
              />
              <span className="param-val">{(targetClearance * 100).toFixed(0)}%</span>
            </div>
            {hasContext ? (
              <button className="btn primary" onClick={handleSuggestHeight} disabled={loading}>
                {loading ? "Calculating…" : "Suggest Height"}
              </button>
            ) : (
              <p className="muted">Run a link analysis first</p>
            )}
          </section>
        )}

        {mode === MODE_BATCH && (
          <section className="panel">
            <h3>Batch AI Analysis</h3>
            <p className="bedrock-desc">
              Process batch link analysis results through AI for consolidated
              coverage assessment and prioritized remediation advice.
              Upload batch results via the API or use the batch reports feature first.
            </p>
            <p className="muted">
              Use POST /bedrock/batch-analyze with up to 500 link results.
              The AI will classify coverage, identify worst links, and recommend fixes.
            </p>
          </section>
        )}

        <section className="panel">
          <h3>Quick Prompts</h3>
          <div className="quick-prompts">
            {quickPrompts.map((qp) => (
              <button
                key={qp.label}
                className="quick-prompt-btn"
                onClick={() => setPrompt(qp.prompt)}
                title={qp.prompt}
              >
                {qp.label}
              </button>
            ))}
          </div>
        </section>

        <section className="panel">
          <button className="btn-clear" onClick={() => { setMessages([]); setError(null); }}>
            Clear Conversation
          </button>
        </section>
      </div>

      <div className="bedrock-chat">
        <div className="bedrock-messages">
          {messages.length === 0 && (
            <div className="bedrock-empty">
              <h2>Amazon Bedrock AI Playground</h2>
              <p>RF engineering intelligence powered by generative AI.</p>
              <div className="bedrock-features">
                <div className="feature-card">
                  <strong>RAG-Enhanced Chat</strong>
                  <span>Domain knowledge about Fresnel zones, propagation models, signal interpretation, and band characteristics is automatically injected into every query.</span>
                </div>
                <div className="feature-card">
                  <strong>Scenario Comparison</strong>
                  <span>Compare 700 MHz vs 3500 MHz, different antenna heights, or any RF scenario with AI-driven engineering trade-off analysis.</span>
                </div>
                <div className="feature-card">
                  <strong>Batch Intelligence</strong>
                  <span>Process hundreds of link analysis results at once. Get coverage classification, worst-link identification, and prioritized remediation.</span>
                </div>
                <div className="feature-card">
                  <strong>Height Advisor</strong>
                  <span>AI calculates the optimal antenna height for your desired Fresnel zone clearance, considering terrain, Earth curvature, and cost.</span>
                </div>
              </div>
              <p className="muted">PRO or ENTERPRISE tier required.</p>
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} className={`bedrock-msg bedrock-msg--${msg.role}${msg.type ? ` bedrock-msg--${msg.type}` : ""}`}>
              <div className="msg-role">
                {msg.role === "user" ? "You" : "AI"}
                {msg.type === "comparison" && <span className="msg-badge badge-compare">Comparison</span>}
                {msg.type === "suggestion" && <span className="msg-badge badge-suggest">Height Advisor</span>}
              </div>
              {msg.role === "assistant" ? (
                <div className="msg-content md-rendered" dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }} />
              ) : (
                <div className="msg-content">{msg.content}</div>
              )}
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
              <div className="msg-content typing">
                <span className="typing-dots"><span>.</span><span>.</span><span>.</span></span>
                Analyzing with Bedrock…
              </div>
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
            placeholder={
              mode === MODE_COMPARE
                ? "Ask a specific comparison question, or leave blank for default analysis…"
                : "Ask about RF analysis, link budgets, antenna planning…"
            }
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={handleKeyDown}
            rows={2}
            disabled={loading}
          />
          <button
            className="btn primary bedrock-send"
            disabled={(!prompt.trim() && mode === MODE_CHAT) || loading}
            onClick={mode === MODE_COMPARE ? handleCompareScenarios : handleSend}
          >
            {loading ? "…" : mode === MODE_COMPARE ? "Compare" : "Send"}
          </button>
        </div>
      </div>
    </div>
  );
}
