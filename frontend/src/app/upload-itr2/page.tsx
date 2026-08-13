"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001";

type DocType =
  | "form16" | "bank_statement" | "capital_gains" | "property" | "foreign_income"
  | "health_insurance" | "life_insurance" | "home_loan" | "other_sources_income"
  | "residential_status" | "reference_document" | "auto";

interface ParsedDoc {
  doc_type:   DocType;
  filename:   string;
  confidence: number;
  data:       Record<string, unknown>;
  warnings:   string[];
  session_id: string;
}

const DOC_LABELS: Record<DocType, string> = {
  form16:                "Form 16",
  bank_statement:        "Bank Statement",
  capital_gains:         "Capital Gains Statement",
  property:              "Property / Home Loan Certificate",
  foreign_income:        "Foreign Income / Assets",
  health_insurance:      "Health Insurance Premium",
  life_insurance:        "Life Insurance Premium",
  home_loan:             "Home Loan Statement",
  other_sources_income:  "Dividend / Interest Summary",
  residential_status:    "Residential Status",
  reference_document:    "Reference Document",
  auto:                  "Document",
};

const DOC_ICONS: Record<DocType, string> = {
  form16:                "📋",
  bank_statement:        "🏦",
  capital_gains:         "📈",
  property:              "🏠",
  foreign_income:        "🌍",
  health_insurance:      "🩺",
  life_insurance:        "🛡️",
  home_loan:             "🏦",
  other_sources_income:  "💰",
  residential_status:    "🧭",
  reference_document:    "📎",
  auto:                  "📄",
};

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
          {DOC_ICONS[doc.doc_type] || "📄"}
        </div>
        <div className="flex-1 min-w-0 pt-1">
          <div className="font-semibold text-gray-900 text-sm truncate pr-8">{doc.filename}</div>
          <div className="text-xs text-gray-500 mt-1 font-medium">
            {DOC_LABELS[doc.doc_type] || doc.doc_type.replace("_", " ")}
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
  hint,
  docType,
  accept = ".pdf,image/jpeg,image/png,.xlsx,.xlsm,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel.sheet.macroEnabled.12",
  onParsed,
}: {
  label:    string;
  hint?:    string;
  docType:  DocType;
  accept?:  string;
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
      className={`block rounded-2xl p-8 text-center cursor-pointer transition-all duration-300 relative overflow-hidden group
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
        accept={accept}
        onChange={(e) => e.target.files?.[0] && upload(e.target.files[0])}
      />

      <div className="relative z-10">
        <div className={`w-12 h-12 mx-auto mb-3 rounded-full flex items-center justify-center text-xl transition-transform duration-500
          ${uploading ? "animate-pulse bg-brand-100" : "bg-brand-50 group-hover:-translate-y-1 group-hover:shadow-md"}
        `}>
          {uploading ? "⏳" : DOC_ICONS[docType]}
        </div>
        <div className="font-semibold text-gray-800 text-base">{label}</div>
        {hint && <div className="text-xs text-gray-400 mt-1 font-medium">{hint}</div>}
        <div className="text-sm text-gray-500 mt-2 font-medium">
          {uploading ? "Extracting intelligence…" : "Drag & drop or click to browse"}
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

type ResidentialStatusAnswer = "resident" | "rnor" | "non_resident" | "unsure";

export default function UploadItr2Page() {
  const router = useRouter();
  const [docs,     setDocs]     = useState<ParsedDoc[]>([]);
  const [running,  setRunning]  = useState(false);
  const [error,    setError]    = useState("");
  const [residentialStatus, setResidentialStatus] = useState<ResidentialStatusAnswer>("unsure");

  const addDoc = (doc: ParsedDoc) => {
    setDocs((prev) => [...prev, doc]);
  };
  const removeDoc = (i: number) => setDocs((prev) => prev.filter((_, j) => j !== i));

  const runPipeline = async () => {
    if (docs.length === 0) return;
    setRunning(true);
    setError("");

    try {
      const parsedDocuments = docs.map((d) => ({ doc_type: d.doc_type, data: d.data }));

      // A direct answer here is more reliable than making the user find and
      // upload a worksheet just to state something they already know — but
      // an actually-uploaded residential_status document (real evidence,
      // e.g. a multi-year status history) takes precedence over a quick
      // self-reported answer if both are present, so this only fills the
      // gap when nothing was uploaded.
      const hasUploadedResidentialStatusDoc = docs.some((d) => d.doc_type === "residential_status");
      if (residentialStatus !== "unsure" && !hasUploadedResidentialStatusDoc) {
        parsedDocuments.push({
          doc_type: "residential_status",
          data: { status: residentialStatus, days_in_india_current_year: null },
        });
      }

      const resp = await fetch(`${API}/api/pipeline/run`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({
          parsed_documents: parsedDocuments,
          session_id:       docs[0]?.session_id,
          ay:               "AY2026-27",
        }),
      });
      const result = await resp.json();
      if (!resp.ok || !result.success) throw new Error(result.message || result.error || "Pipeline failed");

      router.push(`/form-itr2?session=${result.session_id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Pipeline failed");
    } finally {
      setRunning(false);
    }
  };

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
            ITR-2 <span className="text-gradient">Auto-Fill</span>
          </h1>
          <p className="text-lg text-gray-500 max-w-xl mx-auto font-medium">
            Upload salary, capital gains, and house property documents — our AI handles
            multiple properties, equity/other capital gains, and regime comparison for AY 2026-27.
          </p>
        </div>

        {/* Residential status — asked upfront, not inferred */}
        <div className="mb-10 glass-panel rounded-2xl p-6 border-2 border-brand-100 animate-fade-in">
          <div className="flex items-start gap-3 mb-4">
            <span className="text-2xl">🧭</span>
            <div>
              <div className="font-semibold text-gray-900">What's your residential status for FY 2025-26 (AY 2026-27)?</div>
              <p className="text-sm text-gray-500 mt-1">
                This determines whether this tool can compute your return at all. Foreign income for a
                Non-Resident or RNOR filer is often not taxable in India — a wrong assumption here can change
                the final answer entirely, not just a field.
              </p>
            </div>
          </div>
          <div className="grid sm:grid-cols-2 gap-3">
            {([
              { value: "resident",     label: "Resident (Ordinarily Resident)", hint: "Lived in India, no significant time abroad" },
              { value: "rnor",         label: "RNOR", hint: "Recently returned to India after years abroad" },
              { value: "non_resident", label: "Non-Resident", hint: "Living/working outside India this year" },
              { value: "unsure",       label: "Not sure", hint: "Let me upload a worksheet, or determine this with a professional" },
            ] as const).map((opt) => (
              <label
                key={opt.value}
                className={`flex items-start gap-3 rounded-xl p-3.5 cursor-pointer transition-all border-2
                  ${residentialStatus === opt.value
                    ? "border-brand-400 bg-brand-50/80 shadow-sm"
                    : "border-gray-100 bg-white/50 hover:border-brand-200"}`}
              >
                <input
                  type="radio"
                  name="residential-status"
                  className="mt-1"
                  checked={residentialStatus === opt.value}
                  onChange={() => setResidentialStatus(opt.value)}
                />
                <div>
                  <div className="text-sm font-semibold text-gray-800">{opt.label}</div>
                  <div className="text-xs text-gray-500 mt-0.5">{opt.hint}</div>
                </div>
              </label>
            ))}
          </div>
          {(residentialStatus === "non_resident" || residentialStatus === "rnor") && (
            <div className="mt-4 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
              ⚠️ This tool only computes returns for Residents (Ordinarily Resident). Submitting with this
              answer will flag your filing as out of scope and redirect you to a tax professional, rather
              than compute a result that doesn't apply to you.
            </div>
          )}
        </div>

        {/* Upload zones */}
        <div className="space-y-6">
          <div className="animate-fade-in" style={{ animationDelay: '0.1s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">Salary Income</div>
              <div className="text-xs font-medium text-gray-400">Optional</div>
            </div>
            <DropZone
              label="Upload Form 16 (Part A + B)"
              hint="PDF or Excel"
              docType="form16"
              onParsed={addDoc}
            />
          </div>

          <div className="animate-fade-in" style={{ animationDelay: '0.15s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">Capital Gains</div>
              <div className="text-xs font-medium text-gray-400">Optional</div>
            </div>
            <DropZone
              label="Upload Capital Gains Statement"
              hint="Broker contract notes, mutual fund realized-gain statements — PDF or Excel"
              docType="capital_gains"
              onParsed={addDoc}
            />
          </div>

          <div className="animate-fade-in" style={{ animationDelay: '0.2s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">House Property</div>
              <div className="text-xs font-medium text-gray-400">Optional — add once per property</div>
            </div>
            <DropZone
              label="Upload Property / Home Loan Interest Certificate"
              hint="Municipal tax receipts, Sec 24(b) interest certificates — PDF or Excel"
              docType="property"
              onParsed={addDoc}
            />
          </div>

          <div className="animate-fade-in" style={{ animationDelay: '0.25s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">Supporting Documents</div>
              <div className="text-xs font-medium text-gray-400">Optional (80TTA, FD)</div>
            </div>
            <DropZone
              label="Upload Bank Statement(s)"
              hint="PDF or Excel"
              docType="bank_statement"
              onParsed={addDoc}
            />
          </div>

          <div className="animate-fade-in" style={{ animationDelay: '0.3s' }}>
            <div className="flex items-baseline justify-between mb-3">
              <div className="text-sm font-semibold text-gray-800">Foreign Income / Assets</div>
              <div className="text-xs font-medium text-gray-400">Optional — computes Schedule FSI/FA</div>
            </div>
            <DropZone
              label="Upload Foreign Income / Asset Statement"
              hint="PDF or Excel — foreign income is taxed at slab rate with Sec 90/91 credit for foreign tax paid; requires Resident status"
              docType="foreign_income"
              onParsed={addDoc}
            />
          </div>
        </div>

        {/* Other Inputs, Deductions & Disclosures */}
        <div className="mt-14">
          <div className="mb-6 text-center">
            <h2 className="text-lg font-bold text-gray-900">Other Inputs, Deductions &amp; Disclosures</h2>
            <p className="text-sm text-gray-500 mt-1">Chapter VI-A deductions, other domestic income, and residency/reference documents.</p>
          </div>

          <div className="space-y-6">
            <div className="animate-fade-in" style={{ animationDelay: '0.32s' }}>
              <div className="flex items-baseline justify-between mb-3">
                <div className="text-sm font-semibold text-gray-800">Health Insurance Premium</div>
                <div className="text-xs font-medium text-gray-400">Optional — Sec 80D</div>
              </div>
              <DropZone
                label="Upload Health Insurance Premium Receipt"
                hint="PDF or Excel — add one per policy (self/family and parents count separately)"
                docType="health_insurance"
                onParsed={addDoc}
              />
            </div>

            <div className="animate-fade-in" style={{ animationDelay: '0.34s' }}>
              <div className="flex items-baseline justify-between mb-3">
                <div className="text-sm font-semibold text-gray-800">Life Insurance Premium</div>
                <div className="text-xs font-medium text-gray-400">Optional — Sec 80C</div>
              </div>
              <DropZone
                label="Upload Life Insurance Premium Receipt"
                hint="PDF or Excel"
                docType="life_insurance"
                onParsed={addDoc}
              />
            </div>

            <div className="animate-fade-in" style={{ animationDelay: '0.36s' }}>
              <div className="flex items-baseline justify-between mb-3">
                <div className="text-sm font-semibold text-gray-800">Home Loan Statement</div>
                <div className="text-xs font-medium text-gray-400">Optional — principal repaid counts under Sec 80C</div>
              </div>
              <DropZone
                label="Upload Home Loan Repayment Statement"
                hint="PDF or Excel — interest is read for reference only; it's claimed via the Property upload above, not here, to avoid double counting"
                docType="home_loan"
                onParsed={addDoc}
              />
            </div>

            <div className="animate-fade-in" style={{ animationDelay: '0.38s' }}>
              <div className="flex items-baseline justify-between mb-3">
                <div className="text-sm font-semibold text-gray-800">Dividend / Interest Summary</div>
                <div className="text-xs font-medium text-gray-400">Optional — Schedule OS</div>
              </div>
              <DropZone
                label="Upload Dividend or Savings/FD Interest Summary"
                hint="PDF or Excel — foreign-currency documents are automatically detected and computed as Schedule FSI foreign income instead"
                docType="other_sources_income"
                onParsed={addDoc}
              />
            </div>

            <div className="animate-fade-in" style={{ animationDelay: '0.4s' }}>
              <div className="flex items-baseline justify-between mb-3">
                <div className="text-sm font-semibold text-gray-800">Residential Status</div>
                <div className="text-xs font-medium text-gray-400">Optional — determines if this app can compute your return</div>
              </div>
              <DropZone
                label="Upload Residential Status Worksheet"
                hint="PDF or Excel — Non-Resident/RNOR filings are flagged out of scope and redirected to a professional, not computed"
                docType="residential_status"
                onParsed={addDoc}
              />
            </div>

            <div className="animate-fade-in" style={{ animationDelay: '0.42s' }}>
              <div className="flex items-baseline justify-between mb-3">
                <div className="text-sm font-semibold text-gray-800">Reference / Supporting Documents</div>
                <div className="text-xs font-medium text-gray-400">Optional — not used in computation</div>
              </div>
              <DropZone
                label="Upload Reference Document"
                hint="PDF or Excel — e.g. your CA firm's information request, SBI TT rate tables. Stored with this filing, not parsed for figures"
                docType="reference_document"
                onParsed={addDoc}
              />
            </div>
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
        <div className="mt-12 animate-fade-in" style={{ animationDelay: '0.35s' }}>
          {error && (
            <div className="mb-4 text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl px-5 py-4 font-medium flex items-center gap-3">
              <span className="text-xl">⚠️</span> {error}
            </div>
          )}

          <button
            onClick={runPipeline}
            disabled={docs.length === 0 || running}
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
                 Construct My ITR-2 <span className="text-xl font-normal group-hover:translate-x-1 transition-transform">→</span>
               </>
            )}
          </button>

          {docs.length === 0 && (
            <p className="text-sm text-center text-amber-600 font-medium mt-4 bg-amber-50/50 py-2 rounded-lg">
              Upload at least one document to continue.
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
              "Aggregates income and loss across up to multiple house properties",
              "Classifies capital gains into STCG/LTCG buckets with correct special rates",
              "Runs comparative analysis for Old vs New tax regimes",
              "Autofills all ITR-2 schedules with source citations",
              "Flags anomalies and low-confidence fields for your review",
              "Computes foreign income (Schedule FSI) with foreign tax credit for Residents",
              "Flags Non-Resident/RNOR filings as out of scope rather than guessing",
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
