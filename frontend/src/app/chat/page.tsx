"use client";

import { useState, useRef, useEffect, useCallback, Suspense } from "react";
import { useSearchParams } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001";

// ── Types ──────────────────────────────────────────────────────────────────

interface Citation {
  source:  string;
  url:     string;
  section: string;
}

type Role = "user" | "assistant" | "system";

interface Message {
  id:        string;
  role:      Role;
  content:   string;
  citations?: Citation[];
  loading?:  boolean;
}

// ── Suggested questions ─────────────────────────────────────────────────────

const SUGGESTIONS = [
  "What is the 80C deduction limit for AY 2026-27?",
  "Which tax regime is better for me?",
  "How is HRA exemption calculated?",
  "Can I file ITR-1 if I have FD income?",
];

// ── Citation card ──────────────────────────────────────────────────────────

function CitationList({ citations }: { citations: Citation[] }) {
  if (!citations.length) return null;
  return (
    <div className="mt-4 pt-4 border-t border-gray-200/50 space-y-2">
      <div className="text-[10px] uppercase font-bold text-gray-400 tracking-wider">Sources & Context</div>
      <div className="grid gap-2">
        {citations.map((c, i) => {
          let resolvedUrl = c.url;
          const isPdf = c.url?.startsWith("/api/pdfs/");
          if (isPdf) {
            resolvedUrl = `${API}${c.url}`;
          }
          const isValidUrl = resolvedUrl && resolvedUrl !== "#" && resolvedUrl !== "internal";
          
          if (isValidUrl) {
            return (
              <a
                key={i}
                href={resolvedUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 text-xs bg-gray-50/50 hover:bg-white p-2.5 rounded-lg border border-gray-100 hover:border-brand-200 transition-colors group"
              >
                <div className="shrink-0 w-6 h-6 rounded flex items-center justify-center bg-brand-50 text-brand-600 group-hover:bg-brand-100 transition-colors">
                  {isPdf ? "📄" : "↗"}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-gray-700 group-hover:text-brand-700 truncate">{c.source}</div>
                  {c.section && <div className="text-[10px] text-gray-500 truncate">{c.section}</div>}
                </div>
              </a>
            );
          } else {
            return (
              <div key={i} className="flex items-center gap-3 text-xs bg-gray-50/50 p-2.5 rounded-lg border border-gray-100 opacity-80">
                <div className="shrink-0 w-6 h-6 rounded flex items-center justify-center bg-gray-100 text-gray-400">
                  📄
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-gray-700 truncate">{c.source}</div>
                  {c.section && <div className="text-[10px] text-gray-500 truncate">{c.section}</div>}
                </div>
              </div>
            );
          }
        })}
      </div>
    </div>
  );
}

// ── Message bubble ─────────────────────────────────────────────────────────

function MessageBubble({ msg }: { msg: Message }) {
  const isUser = msg.role === "user";

  if (msg.role === "system") {
    return (
      <div className="flex justify-center my-6">
        <div className="bg-gray-100 text-gray-500 text-xs font-medium px-4 py-1.5 rounded-full">
          {msg.content}
        </div>
      </div>
    );
  }

  return (
    <div className={`flex w-full ${isUser ? "justify-end" : "justify-start"} animate-slide-up`} style={{ animationDuration: '0.4s' }}>
      <div className={`flex max-w-[85%] md:max-w-[75%] gap-4 ${isUser ? "flex-row-reverse" : "flex-row"}`}>
        
        {/* Avatar */}
        <div className="shrink-0 mt-1">
          {isUser ? (
            <div className="w-8 h-8 rounded-full bg-gradient-to-tr from-brand-600 to-brand-400 flex items-center justify-center text-white text-xs font-bold shadow-sm">
              YOU
            </div>
          ) : (
            <div className="w-8 h-8 rounded-full bg-white border border-gray-200 flex items-center justify-center text-xl shadow-sm z-10 relative">
              🤖
            </div>
          )}
        </div>

        {/* Bubble */}
        <div className={`flex-1 rounded-2xl px-5 py-4 text-[15px] leading-relaxed shadow-sm
          ${isUser
            ? "bg-brand-600 text-white rounded-tr-sm font-medium"
            : "bg-white border border-gray-100 text-gray-800 rounded-tl-sm"}`}>

          {msg.loading ? (
            <div className="flex gap-1.5 items-center py-2 px-1">
              <span className="w-2 h-2 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
              <span className="w-2 h-2 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
              <span className="w-2 h-2 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
            </div>
          ) : (
            <div className="flex flex-col">
              <div className="whitespace-pre-wrap">{msg.content}</div>
              {!isUser && msg.citations && msg.citations.length > 0 && (
                <CitationList citations={msg.citations} />
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Regime mini-card (shown when form session active) ─────────────────────

function RegimeCard({ sessionId }: { sessionId: string }) {
  const [regime, setRegime] = useState<{
    recommended: string; old_tax: number; new_tax: number; saving: number
  } | null>(null);

  useEffect(() => {
    fetch(`${API}/api/pipeline/${sessionId}`)
      .then(r => r.json())
      .then(d => {
        const form = d?.itr1_form;
        if (!form) return;
        setRegime({
          recommended: form.regime_recommendation || "new",
          old_tax:     form.regime_tax_old || 0,
          new_tax:     form.regime_tax_new || 0,
          saving:      Math.abs((form.regime_tax_old || 0) - (form.regime_tax_new || 0)),
        });
      })
      .catch(() => {});
  }, [sessionId]);

  if (!regime) return null;

  return (
    <div className="mx-auto max-w-2xl mb-6 glass-panel rounded-2xl p-5 border-t-4 border-t-brand-500 shadow-soft animate-fade-in">
      <div className="flex items-center justify-between mb-4">
        <div className="text-xs font-bold text-gray-500 uppercase tracking-wider">Your Tax Summary</div>
        <div className="text-[10px] font-bold bg-success-50 text-success-700 px-2.5 py-1 rounded-full uppercase">
          {regime.recommended} Regime Recommended
        </div>
      </div>
      
      <div className="grid grid-cols-3 gap-4">
        <div className="bg-gray-50 rounded-xl p-3 text-center border border-gray-100">
          <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wide mb-1">Old Regime</div>
          <div className="text-sm font-semibold text-gray-800 font-mono">₹{regime.old_tax.toLocaleString("en-IN")}</div>
        </div>
        <div className="bg-gray-50 rounded-xl p-3 text-center border border-gray-100">
          <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wide mb-1">New Regime</div>
          <div className="text-sm font-semibold text-gray-800 font-mono">₹{regime.new_tax.toLocaleString("en-IN")}</div>
        </div>
        <div className="bg-success-500 text-white rounded-xl p-3 text-center shadow-sm">
          <div className="text-[10px] font-bold text-success-100 uppercase tracking-wide mb-1">Total Savings</div>
          <div className="text-sm font-bold font-mono">₹{regime.saving.toLocaleString("en-IN")}</div>
        </div>
      </div>
    </div>
  );
}

// ── Main chat page ─────────────────────────────────────────────────────────

function ChatPageInner() {
  const params    = useSearchParams();
  const sessionId = params.get("session") || "";

  const [messages, setMessages] = useState<Message[]>([
    {
      id:      "welcome",
      role:    "assistant",
      content: sessionId
        ? "Hi! I have loaded your ITR-1 draft context. Feel free to ask me any questions about your return, specific deductions, or tax rules for AY 2026-27."
        : "Welcome to the AI Tax Engine! I'm specialized in Indian Income Tax for AY 2026-27. Ask me anything about filing your return, standard deductions, HRA, or regime choices.",
    },
  ]);
  const [input,   setInput]   = useState("");
  const [loading, setLoading] = useState(false);
  const bottomRef             = useRef<HTMLDivElement>(null);
  const inputRef              = useRef<HTMLTextAreaElement>(null);

  // Auto-scroll
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-resize textarea
  useEffect(() => {
    if (!inputRef.current) return;
    inputRef.current.style.height = "auto";
    inputRef.current.style.height = `${Math.min(inputRef.current.scrollHeight, 120)}px`;
  }, [input]);

  const send = useCallback(async (question: string) => {
    if (!question.trim() || loading) return;

    const userMsg: Message = {
      id:      Date.now().toString(),
      role:    "user",
      content: question.trim(),
    };
    const loadingMsg: Message = {
      id:      "loading",
      role:    "assistant",
      content: "",
      loading: true,
    };

    setMessages(prev => [...prev, userMsg, loadingMsg]);
    setInput("");
    setLoading(true);

    try {
      const resp = await fetch(`${API}/api/chat`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          question,
          session_id:           sessionId || undefined,
          ay:                   "AY2026-27",
          include_form_context: !!sessionId,
        }),
      });
      const data = await resp.json();

      const aiMsg: Message = {
        id:        Date.now().toString() + "_ai",
        role:      "assistant",
        content:   data.answer || "I encountered an error processing your query. Please try again.",
        citations: data.citations || [],
      };

      setMessages(prev => prev.filter(m => m.id !== "loading").concat(aiMsg));
    } catch {
      setMessages(prev =>
        prev.filter(m => m.id !== "loading").concat({
          id:      "err",
          role:    "assistant",
          content: "Network connection error. Ensure the backend engine is running.",
        })
      );
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }, [loading, sessionId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  };

  return (
    <div className="h-screen flex flex-col relative overflow-hidden bg-[#f4f7f9]">
      {/* Abstract background */}
      <div className="absolute top-[-10%] right-[-5%] w-[40%] h-[40%] rounded-full bg-brand-200/30 blur-[120px] pointer-events-none" />
      <div className="absolute bottom-[-10%] left-[-10%] w-[50%] h-[50%] rounded-full bg-purple-200/30 blur-[120px] pointer-events-none" />

      {/* Nav */}
      <div className="bg-white/80 backdrop-blur-lg border-b border-gray-200 px-6 py-4 flex items-center justify-between z-20 shadow-sm">
        <div className="flex items-center gap-4">
          {sessionId && (
            <a href={`/form?session=${sessionId}`}
               className="w-8 h-8 flex items-center justify-center rounded-full bg-gray-100 text-gray-500 hover:bg-brand-50 hover:text-brand-600 transition-colors">
              ←
            </a>
          )}
          <div>
            <div className="font-bold text-gray-900 text-sm tracking-wide">AI Tax Expert</div>
            <div className="text-[10px] uppercase font-bold text-brand-600 tracking-wider">ITR-1 · AY 2026-27</div>
          </div>
        </div>
        <div className="hidden sm:flex items-center gap-2 px-3 py-1.5 rounded-full bg-gray-100/80 border border-gray-200 text-xs font-semibold text-gray-500">
          <span className="w-1.5 h-1.5 rounded-full bg-success-500 animate-pulse-slow" />
          Grounded in Official CBDT Rules
        </div>
      </div>

      {/* Main Scrollable Area */}
      <div className="flex-1 overflow-y-auto chat-scroll z-10">
        <div className="max-w-4xl mx-auto px-4 py-8 flex flex-col min-h-full">
          
          {/* Regime card (if session active) */}
          {sessionId && <RegimeCard sessionId={sessionId} />}

          {/* Messages */}
          <div className="space-y-6 pb-4 flex-1">
            {messages.map(msg => (
              <MessageBubble key={msg.id} msg={msg} />
            ))}
            <div ref={bottomRef} />
          </div>

          {/* Suggestions (shown when only welcome message) */}
          {messages.length <= 1 && (
            <div className="mt-auto pt-8 pb-4 animate-fade-in" style={{ animationDelay: '0.2s' }}>
              <div className="text-[11px] uppercase font-bold text-gray-400 mb-3 tracking-wider text-center">Suggested Topics</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl mx-auto">
                {SUGGESTIONS.map((s, i) => (
                  <button
                    key={i}
                    onClick={() => send(s)}
                    className="text-left text-sm border border-gray-200 rounded-xl px-4 py-3 bg-white/60 hover:bg-white text-gray-700
                      hover:border-brand-300 hover:shadow-md hover:-translate-y-0.5 transition-all duration-300 font-medium"
                  >
                    {s}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Input Area */}
      <div className="z-20 bg-gradient-to-t from-[#f4f7f9] via-[#f4f7f9] to-transparent pt-6 pb-6 px-4">
        <div className="max-w-4xl mx-auto">
          <div className="glass-panel rounded-2xl p-2 flex items-end gap-2 focus-within:ring-2 focus-within:ring-brand-400/50 focus-within:border-brand-400 transition-all shadow-lg">
            <textarea
              ref={inputRef}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your deductions, HRA, or regime choices…"
              rows={1}
              className="flex-1 resize-none bg-transparent px-4 py-3 text-[15px] font-medium text-gray-800
                focus:outline-none placeholder-gray-400 min-h-[52px] max-h-[150px] overflow-y-auto leading-relaxed"
            />
            <button
              onClick={() => send(input)}
              disabled={!input.trim() || loading}
              className="shrink-0 w-12 h-12 mb-0.5 mr-0.5 rounded-xl bg-brand-600 text-white flex items-center justify-center
                hover:bg-brand-500 hover:shadow-glow disabled:opacity-40 disabled:cursor-not-allowed transition-all active:scale-95"
            >
              {loading ? (
                <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : (
                <svg className="w-5 h-5 -rotate-90 translate-y-[1px] -translate-x-[1px]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                </svg>
              )}
            </button>
          </div>
          <div className="text-center text-[10px] font-semibold text-gray-400 uppercase tracking-wider mt-3">
            Press <kbd className="font-sans px-1 py-0.5 bg-gray-200/50 rounded text-gray-500 mx-0.5">Enter</kbd> to send · <kbd className="font-sans px-1 py-0.5 bg-gray-200/50 rounded text-gray-500 mx-0.5">Shift</kbd> + <kbd className="font-sans px-1 py-0.5 bg-gray-200/50 rounded text-gray-500 mx-0.5">Enter</kbd> for new line
          </div>
        </div>
      </div>
    </div>
  );
}

export default function ChatPage() {
  return (
    <Suspense fallback={
      <div className="h-screen flex items-center justify-center">
        <div className="w-8 h-8 border-4 border-brand-200 border-t-brand-600 rounded-full animate-spin" />
      </div>
    }>
      <ChatPageInner />
    </Suspense>
  );
}
