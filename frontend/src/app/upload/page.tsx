"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001";

type DocType = "form16" | "bank_statement" | "auto";

interface ParsedDoc {
  doc_type:   DocType;
  filename:   string;
  confidence: number;
  data:       Record<string, unknown>;
  warnings:   string[];
  session_id: string;
}

// ── File card ──────────────────────────────────────────────────────────────

function FileCard({
  doc,
  onRemove,
}: {
  doc: ParsedDoc;
  onRemove: () => void;
}) {
  const conf    = Math.round(doc.confidence * 100);
  const confColor =
    conf >= 80 ? "text-success-600 bg-success-50" : conf >= 50 ? "text-amber-600 bg-amber-50" : "text-red-600 bg-red-50";

  return (
    <div className="glass-card rounded-2xl p-5 relative animate-fade-in group">
      <button
        onClick={onRemove}
        className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-full bg-gray-50 text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors opacity-0 group-hover:opacity-100"
      >
        ×
      </button>
      <div className="flex items-start gap-4">
        <div className="text-3xl p-3 bg-white rounded-xl shadow-sm border border-gray-100">
          {doc.doc_type === "form16" ? "📋" : "🏦"}
        </div>
        <div className="flex-1 min-w-0 pt-1">
          <div className="font-semibold text-gray-900 text-sm truncate pr-8">{doc.filename}</div>
          <div className="text-xs text-gray-500 mt-1 capitalize font-medium">
            {doc.doc_type.replace("_", " ")}
          </div>
          
          <div className="mt-3 flex items-center gap-2">
             <div className="flex-1 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                <div className={`h-full ${conf >= 80 ? 'bg-success-500' : conf >= 50 ? 'bg-amber-400' : 'bg-red-500'}`} style={{ width: `${conf}%` }} />
             </div>
             <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-full ${confColor}`}>
                {conf}% match
             </span>
          </div>

          {doc.warnings.length > 0 && (
            <div className="mt-3 space-y-1.5">
              {doc.warnings.map((w, i) => (
                <div key={i} className="text-[11px] text-amber-700 bg-amber-50/80 rounded-md px-2.5 py-1.5 border border-amber-100/50">
                  <span className="mr-1">⚠️</span> {w}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Drop zone ──────────────────────────────────────────────────────────────

function DropZone({
  label,
  docType,
  onParsed,
}: {
  label:    string;
  docType:  DocType;
  onParsed: (doc: ParsedDoc) => void;
}) {
  const [dragging,   setDragging]   = useState(false);
  const [uploading,  setUploading]  = useState(false);
  const [error,      setError]      = useState("");

  const upload = useCallback(
    async (file: File) => {
      setUploading(true);
      setError("");
      try {
        const fd = new FormData();
        fd.append("file", file);
        fd.append("hint", docType);

        const resp = await fetch(`${API}/api/upload/${docType}`, {
          method: "POST",
          body:   fd,
        });
        const data = await resp.json();
        if (!resp.ok || !data.success) throw new Error(data.error || "Upload failed");

        onParsed({ ...data, filename: file.name });
      } catch (e: unknown) {
        setError(e instanceof Error ? e.message : "Upload failed");
      } finally {
        setUploading(false);
      }
    },
    [docType, onParsed]
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragging(false);
      const file = e.dataTransfer.files[0];
      if (file) upload(file);
    },
    [upload]
  );

  return (
    <label
      className={`block rounded-2xl p-10 text-center cursor-pointer transition-all duration-300 relative overflow-hidden group
        ${dragging   ? "border-brand-400 bg-brand-50/80 shadow-glow scale-[1.02]"  : "bg-white/50 border border-gray-200/60 hover:bg-white hover:shadow-soft hover:border-brand-200"}
        ${uploading  ? "opacity-70 pointer-events-none" : ""}`}
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      <div className="absolute inset-0 bg-gradient-to-br from-brand-50/40 to-transparent opacity-0 group-hover:opacity-100 transition-opacity" />
      
      <input
        type="file"
        className="hidden"
        accept=".pdf,image/jpeg,image/png"
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
      />
      
      <div className="relative z-10">
        <div className={`w-16 h-16 mx-auto mb-4 rounded-full flex items-center justify-center text-2xl transition-transform duration-500
          ${uploading ? "animate-pulse bg-brand-100" : "bg-brand-50 group-hover:-translate-y-1 group-hover:shadow-md"}
        `}>
          {uploading ? "⏳" : "📂"}
        </div>
        <div className="font-semibold text-gray-800 text-lg">{label}</div>
        <div className="text-sm text-gray-500 mt-2 font-medium">
          {uploading ? "Extracting intelligence…" : "Drag & drop or click to browse"}
        </div>
        <div className="text-[11px] uppercase tracking-wider text-gray-400 mt-3 font-semibold">
          PDF, JPG, PNG — max 20MB
        </div>
        
        {error && (
          <div className="mt-4 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-4 py-2.5 animate-fade-in inline-block">
            {error}
          </div>
        )}
      </div>
    </label>
  );
}


// ── Main page ──────────────────────────────────────────────────────────────

export default function UploadPage() {
  const router = useRouter();
  const [docs,     setDocs]     = useState<ParsedDoc[]>([]);
  const [running,  setRunning]  = useState(false);
  const [error,    setError]    = useState("");

  const addDoc = (doc: ParsedDoc) => {
    setDocs((prev) => [...prev, doc]);
  };
  const removeDoc = (i: number) => setDocs((prev) => prev.filter((_, j) => j !== i));

  const runPipeline = async () => {
    if (docs.length === 0) return;
    setRunning(true);
    setError("");

    try {
      const resp = await fetch(`${API}/api/pipeline/run`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          parsed_documents: docs.map((d) => ({ doc_type: d.doc_type, data: d.data })),
          session_id:       docs[0]?.session_id,
          ay:               "AY2026-27",
        }),
      });
      const result = await resp.json();
      if (!resp.ok || !result.success) throw new Error(result.error || "Pipeline failed");

      router.push(`/form?session=${result.session_id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Pipeline failed");
    } finally {
      setRunning(false);
    }
  };

  const hasForm16 = docs.some((d) => d.doc_type === "form16");

  return (
    <div className="min-h-screen relative pt-12 pb-24">
      {/* Abstract background shapes */}
      <div className="fixed top-[-10%] left-[-10%] w-[40%] h-[40%] rounded-full bg-brand-200/20 blur-3xl mix-blend-multiply pointer-events-none" />
      <div className="fixed top-[20%] right-[-5%] w-[30%] h-[50%] rounded-full bg-purple-200/20 blur-3xl mix-blend-multiply pointer-events-none" />

      <div className="max-w-3xl mx-auto px-4 relative z-10 animate-slide-up">
        {/* Header */}
        <div className="mb-12 text-center">
          <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-brand-50 border border-brand-100/50 text-brand-700 text-xs font-semibold mb-6">
            <span className="w-1.5 h-1.5 rounded-full bg-brand-500 animate-pulse-slow" />
            AI-POWERED TAX ENGINE
          </div>
          <h1 className="text-4xl md:text-5xl font-bold tracking-tight text-gray-900 mb-4">
            ITR-1 <span className="text-gradient">Auto-Fill</span>
          </h1>
          <p className="text-lg text-gray-500 max-w-xl mx-auto font-medium">
            Upload your documents and let our AI instantly construct your AY 2026-27 tax return with precision.
          </p>
        </div>

        {/* Upload zones */}
        <div className="space-y-6">
          <div className="animate-fade-in" style={{ animationDelay: '0.1s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">
                Primary Document <span className="text-red-500">*</span>
              </div>
              <div className="text-xs font-medium text-gray-400">Required</div>
            </div>
            <DropZone label="Upload Form 16 (Part A + B)" docType="form16" onParsed={addDoc} />
          </div>

          <div className="animate-fade-in" style={{ animationDelay: '0.2s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">
                Supporting Documents
              </div>
              <div className="text-xs font-medium text-gray-400">Optional (80TTA, FD)</div>
            </div>
            <DropZone label="Upload Bank Statement(s)" docType="bank_statement" onParsed={addDoc} />
          </div>
        </div>

        {/* Parsed docs */}
        {docs.length > 0 && (
          <div className="mt-10 space-y-4">
            <div className="text-sm font-semibold text-gray-800 flex items-center gap-2">
              <div className="w-4 h-0.5 bg-brand-400 rounded-full" />
              Prepared Documents
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              {docs.map((doc, i) => (
                <FileCard key={i} doc={doc} onRemove={() => removeDoc(i)} />
              ))}
            </div>
          </div>
        )}

        {/* CTA */}
        <div className="mt-12 animate-fade-in" style={{ animationDelay: '0.3s' }}>
          {error && (
            <div className="mb-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl px-5 py-4 font-medium flex items-center gap-3">
              <span className="text-xl">⚠️</span> {error}
            </div>
          )}

          <button
            onClick={runPipeline}
            disabled={!hasForm16 || running}
            className="w-full py-4 rounded-2xl bg-gradient-to-r from-brand-600 to-brand-500 text-white font-semibold text-lg
              hover:from-brand-500 hover:to-brand-400 hover:shadow-glow disabled:opacity-50 disabled:cursor-not-allowed 
              transition-all duration-300 transform active:scale-[0.98] shadow-lg flex items-center justify-center gap-3"
          >
            {running ? (
               <>
                 <span className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                 Processing Documents…
               </>
            ) : (
               <>
                 Construct My ITR-1 <span className="text-xl font-normal group-hover:translate-x-1 transition-transform">→</span>
               </>
            )}
          </button>

          {!hasForm16 && docs.length > 0 && (
            <p className="text-sm text-center text-amber-600 font-medium mt-4 bg-amber-50/50 py-2 rounded-lg">
              Form 16 is required to proceed.
            </p>
          )}
        </div>

        {/* What happens next */}
        <div className="mt-16 glass-panel rounded-2xl p-6 md:p-8 animate-fade-in" style={{ animationDelay: '0.4s' }}>
          <div className="flex items-center gap-3 mb-6">
            <div className="w-8 h-8 rounded-full bg-brand-100 flex items-center justify-center text-brand-600 text-lg font-bold">
              ?
            </div>
            <h3 className="text-base font-semibold text-gray-900">How our AI Engine works</h3>
          </div>
          
          <div className="grid sm:grid-cols-2 gap-x-8 gap-y-4">
            {[
              "Extracts salary, TDS, HRA, and deductions with high precision",
              "Identifies interest income directly from bank statements",
              "Runs comparative analysis for Old vs New tax regimes",
              "Autofills all ITR-1 schedules with source citations",
              "Flags anomalies and low-confidence fields for your review",
              "Explains every computed value in plain English",
            ].map((step, i) => (
              <div key={i} className="flex gap-3 items-start">
                <div className="shrink-0 w-6 h-6 rounded-full bg-gray-100 text-gray-500 flex items-center justify-center text-xs font-bold mt-0.5">
                  {i + 1}
                </div>
                <span className="text-sm text-gray-600 font-medium leading-relaxed">{step}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
