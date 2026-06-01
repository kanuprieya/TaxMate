"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:3001";

// ── Types ──────────────────────────────────────────────────────────────────

interface FieldConf {
  value:       number | string;
  confidence:  number;
  source:      string;
  explanation: string;
  flagged:     boolean;
  citation?:   string;
}

interface ITRData {
  itr1_form:        Record<string, unknown>;
  confidence_scores: Record<string, FieldConf>;
  validation_flags:  Array<{ field: string; severity: string; message: string; suggestion?: string }>;
  explanations:      Record<string, string>;
  regime_analysis:   Record<string, unknown>;
  integrity_score:   number;
}

// ── Circular Progress ──────────────────────────────────────────────────────

function CircularProgress({ percentage, colorClass, size = 48, strokeWidth = 4 }: { percentage: number, colorClass: string, size?: number, strokeWidth?: number }) {
  const radius = (size - strokeWidth) / 2;
  const circumference = radius * 2 * Math.PI;
  const offset = circumference - (percentage / 100) * circumference;
  
  return (
    <div className="relative inline-flex items-center justify-center" style={{ width: size, height: size }}>
      <svg className="transform -rotate-90 w-full h-full">
        <circle cx={size/2} cy={size/2} r={radius} className="stroke-gray-100" strokeWidth={strokeWidth} fill="transparent" />
        <circle cx={size/2} cy={size/2} r={radius} className={`transition-all duration-1000 ease-out ${colorClass}`} strokeWidth={strokeWidth} strokeDasharray={circumference} strokeDashoffset={offset} strokeLinecap="round" fill="transparent" />
      </svg>
      <div className="absolute flex items-center justify-center text-[10px] font-bold text-gray-700">
        {Math.round(percentage)}%
      </div>
    </div>
  );
}

// ── Field row ──────────────────────────────────────────────────────────────

function FieldRow({
  label,
  fieldPath,
  value,
  conf,
  onEdit,
}: {
  label:    string;
  fieldPath: string;
  value:    string | number;
  conf?:    FieldConf;
  onEdit:   (field: string, label: string, val: string | number) => void;
}) {
  const confidence = conf?.confidence ?? 1;
  const pct        = Math.round(confidence * 100);
  const isAmount   = typeof value === "number";
  const display    = isAmount
    ? value === 0 ? "—" : `₹${Number(value).toLocaleString("en-IN")}`
    : String(value || "—");

  const confColor =
    pct >= 80 ? "stroke-success-500"
    : pct >= 50 ? "stroke-amber-400"
    : "stroke-red-500";

  const sourceLabel: Record<string, string> = {
    form16:          "Form 16",
    bank_statement:  "Bank stmt",
    computed:        "Computed",
    rag_inference:   "AI inferred",
    manual:          "Manual",
    missing:         "Missing",
  };

  return (
    <div className={`flex items-start gap-4 py-4 border-b border-gray-100/60 last:border-0 hover:bg-gray-50/50 transition-colors px-4 -mx-4 rounded-xl
      ${conf?.flagged ? "bg-red-50/40 hover:bg-red-50/60" : ""}`}>
      
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium text-gray-700 truncate">{label}</span>
          {conf?.flagged && (
            <span className="text-[10px] font-bold text-red-600 bg-red-100/80 rounded px-1.5 py-0.5 shrink-0 uppercase tracking-wide border border-red-200">
              Review
            </span>
          )}
          {conf?.source && (
            <span className="text-[10px] font-medium text-gray-400 bg-gray-100 rounded px-1.5 py-0.5 hidden sm:inline-block border border-gray-200">
              {sourceLabel[conf.source] || conf.source}
            </span>
          )}
        </div>
        {conf?.explanation && (
          <div className="text-xs text-gray-500 leading-relaxed max-w-2xl">{conf.explanation}</div>
        )}
      </div>

      <div className="flex items-center gap-5 shrink-0 mt-1">
        {conf && (
          <div className="hidden sm:block" title={`Confidence: ${pct}%`}>
            <CircularProgress percentage={pct} colorClass={confColor} size={28} strokeWidth={3} />
          </div>
        )}

        <div className={`text-sm font-semibold w-28 text-right font-mono tracking-tight
          ${!value || value === 0 ? "text-gray-300" : "text-gray-900"}`}>
          {display}
        </div>

        <button
          onClick={() => onEdit(fieldPath, label, value)}
          className="text-gray-300 hover:text-brand-500 text-sm w-6 h-6 rounded-md hover:bg-brand-50 flex items-center justify-center transition-colors"
          title="Edit Value"
        >
          ✎
        </button>
      </div>
    </div>
  );
}

// ── Section card ──────────────────────────────────────────────────────────

function SectionCard({
  title,
  emoji,
  children,
  total,
  className = ""
}: {
  title:    string;
  emoji:    string;
  children: React.ReactNode;
  total?:   { label: string; value: number };
  className?: string;
}) {
  return (
    <div className={`glass-card rounded-2xl overflow-hidden animate-slide-up ${className}`}>
      <div className="px-6 py-4 bg-white/40 border-b border-gray-100 flex items-center gap-3">
        <span className="text-xl">{emoji}</span>
        <span className="font-semibold text-gray-800 text-sm tracking-wide">{title}</span>
      </div>
      <div className="px-6 py-2">{children}</div>
      {total && (
        <div className="px-6 py-4 bg-brand-50/50 border-t border-brand-100/30 flex justify-between items-center backdrop-blur-sm">
          <span className="text-sm font-semibold text-brand-800">{total.label}</span>
          <span className="text-base font-bold text-brand-900 font-mono tracking-tight">
            ₹{total.value.toLocaleString("en-IN")}
          </span>
        </div>
      )}
    </div>
  );
}

// ── Edit modal ─────────────────────────────────────────────────────────────

function EditModal({
  field,
  label,
  value,
  sessionId,
  onClose,
  onSaved,
}: {
  field:     string;
  label:     string;
  value:     string | number;
  sessionId: string;
  onClose:   () => void;
  onSaved:   () => void;
}) {
  const [val,    setVal]    = useState(String(value));
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    await fetch(`${API}/api/pipeline/update-field`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ session_id: sessionId, field_path: field, value: Number(val) || val, reason }),
    });
    setSaving(false);
    onSaved();
    onClose();
  };

  return (
    <div className="fixed inset-0 bg-gray-900/40 backdrop-blur-sm flex items-center justify-center z-50 p-4 animate-fade-in">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm p-6 transform transition-all">
        <div className="font-semibold text-gray-900 mb-1">{label}</div>
        <div className="text-[10px] uppercase tracking-wider font-bold text-gray-400 mb-5 font-mono">{field}</div>
        
        <div className="space-y-4">
          <div>
            <label className="text-xs font-semibold text-gray-600 mb-1 block">New Value</label>
            <input
              type="text"
              value={val}
              onChange={(e) => setVal(e.target.value)}
              className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:border-brand-500 transition-colors"
            />
          </div>
          <div>
            <label className="text-xs font-semibold text-gray-600 mb-1 block">Reason (Optional)</label>
            <input
              type="text"
              placeholder="e.g. Corrected typo in Form 16"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="w-full border border-gray-200 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500/50 focus:border-brand-500 transition-colors"
            />
          </div>
        </div>
        
        <div className="flex gap-3 mt-8">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-sm font-semibold text-gray-600 hover:bg-gray-50 transition-colors">
            Cancel
          </button>
          <button onClick={save} disabled={saving} className="flex-1 py-2.5 rounded-xl bg-brand-600 text-white text-sm font-semibold hover:bg-brand-700 disabled:opacity-50 transition-colors shadow-sm">
            {saving ? "Saving…" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}


// ── Main Form Viewer ───────────────────────────────────────────────────────

function FormPageInner() {
  const params    = useSearchParams();
  const sessionId = params.get("session") || "";

  const [data,    setData]    = useState<ITRData | null>(null);
  const [loading, setLoading] = useState(true);
  const [editTarget, setEditTarget] = useState<{ field: string; label: string; value: string | number } | null>(null);

  const loadData = async () => {
    if (!sessionId) return;
    setLoading(true);
    try {
      const resp = await fetch(`${API}/api/pipeline/${sessionId}`);
      const json = await resp.json();
      setData(json);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadData(); }, [sessionId]);

  if (loading) return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="flex flex-col items-center gap-4">
        <div className="w-10 h-10 border-4 border-brand-200 border-t-brand-600 rounded-full animate-spin" />
        <div className="text-gray-500 font-medium text-sm">Constructing your ITR-1…</div>
      </div>
    </div>
  );
  if (!data) return (
    <div className="min-h-screen flex items-center justify-center text-red-500 font-medium">
      Session not found. Please start again.
    </div>
  );

  const form   = data.itr1_form as Record<string, Record<string, number | string>>;
  const conf   = data.confidence_scores;
  const flags  = data.validation_flags;
  const tc     = form.tax_computation as Record<string, number | string>;
  const sal    = form.salary_income as Record<string, number | string>;
  const ded    = form.deductions as Record<string, number | string>;
  const os     = form.other_sources as Record<string, number | string>;
  const regime = String(tc?.regime || "new");

  const F = (path: string) => conf[path];
  
  const refundAmt = Number(tc?.refund || 0);
  const isRefund = refundAmt > 0;

  return (
    <div className="min-h-screen relative pb-24">
       {/* Background */}
       <div className="fixed inset-0 pointer-events-none overflow-hidden z-[-1]">
         <div className="absolute top-[10%] left-[-10%] w-[50%] h-[50%] rounded-full bg-brand-200/20 blur-[100px]" />
         <div className="absolute bottom-[-10%] right-[-5%] w-[40%] h-[60%] rounded-full bg-success-50/40 blur-[100px]" />
       </div>

      <div className="max-w-3xl mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8 flex flex-col md:flex-row md:items-end justify-between gap-4 animate-fade-in">
          <div>
            <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white border border-gray-200 text-gray-500 text-xs font-semibold mb-3 shadow-sm">
              <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
              AY 2026-27 DRAFT READY
            </div>
            <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-gray-900">Your ITR-1 (Sahaj)</h1>
          </div>
          
          <div className="flex items-center gap-6 glass-panel px-5 py-3 rounded-2xl">
            {data.integrity_score !== undefined && (
              <div className="flex items-center gap-3 border-r pr-5">
                <CircularProgress 
                  percentage={data.integrity_score * 100} 
                  colorClass={data.integrity_score > 0.8 ? "stroke-success-500" : data.integrity_score > 0.5 ? "stroke-amber-500" : "stroke-red-500"} 
                  size={42} 
                />
                <div>
                  <div className="text-[10px] uppercase font-bold text-gray-400">Data Integrity</div>
                  <div className="text-xs font-medium text-gray-700">Based on source matching</div>
                </div>
              </div>
            )}
            <a
              href={`${API}/api/pipeline/export/${sessionId}`}
              target="_blank"
              className="text-sm font-semibold text-brand-600 hover:text-brand-800 transition-colors flex items-center gap-1"
            >
              Export JSON <span className="text-lg">↓</span>
            </a>
          </div>
        </div>

        {/* Validation flags */}
        {flags.length > 0 && (
          <div className="mb-6 space-y-3 animate-fade-in" style={{ animationDelay: '0.1s' }}>
            {flags.map((f, i) => (
              <div key={i} className={`rounded-xl px-5 py-4 text-sm border shadow-sm flex items-start gap-3
                ${f.severity === "error"   ? "bg-red-50 border-red-200 text-red-900"
                : f.severity === "warning" ? "bg-amber-50 border-amber-200 text-amber-900"
                : "bg-brand-50 border-brand-200 text-brand-900"}`}>
                <span className="text-lg mt-0.5">
                  {f.severity === "error" ? "🚨" : f.severity === "warning" ? "⚠️" : "ℹ️"}
                </span>
                <div>
                  <span className="font-bold capitalize">{f.severity}:</span> {f.message}
                  {f.suggestion && <div className="text-xs mt-1.5 font-medium opacity-80 bg-white/50 px-2 py-1 rounded inline-block">↳ {f.suggestion}</div>}
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Missing AIS Warning */}
        {!Object.values(conf).some(c => c.source === "ais" || c.source === "26as") && (
          <div className="mb-8 glass-panel rounded-2xl px-6 py-5 flex items-start sm:items-center gap-4 border-amber-200/60 bg-amber-50/30 animate-fade-in" style={{ animationDelay: '0.2s' }}>
            <div className="text-3xl">🏦</div>
            <div className="flex-1">
              <div className="text-sm font-bold text-amber-900">Complete Your Tax Profile</div>
              <p className="text-xs text-amber-800/80 mt-1 font-medium">
                We only have salary data. Upload your <strong>Form 26AS or AIS</strong> to capture FD interest, savings interest, and other tax credits.
              </p>
            </div>
            <button 
              onClick={() => window.location.href = "/"}
              className="shrink-0 text-xs font-bold bg-amber-100 text-amber-800 px-4 py-2 rounded-lg hover:bg-amber-200 transition-colors border border-amber-200"
            >
              Add AIS
            </button>
          </div>
        )}

        <div className="space-y-6">
          {/* Salary Income */}
          <SectionCard title="Salary Income (Schedule S)" emoji="💼" className="!delay-[300ms]"
            total={{ label: "Taxable Salary", value: Number(sal?.taxable_salary || 0) }}>
            <FieldRow label="Gross Salary" fieldPath="salary_income.gross_salary"
              value={Number(sal?.gross_salary || 0)} conf={F("salary_income.gross_salary")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="HRA Exemption [10(13A)]" fieldPath="salary_income.allowances_exempt_10_13a"
              value={Number(sal?.allowances_exempt_10_13a || 0)} conf={F("salary_income.allowances_exempt_10_13a")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Standard Deduction [16(ia)]" fieldPath="salary_income.standard_deduction_16ia"
              value={Number(sal?.standard_deduction_16ia || 0)} conf={F("salary_income.standard_deduction_16ia")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Professional Tax [16(iii)]" fieldPath="salary_income.professional_tax_16iii"
              value={Number(sal?.professional_tax_16iii || 0)} conf={F("salary_income.professional_tax_16iii")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
          </SectionCard>

          {/* Other Sources */}
          <SectionCard title="Other Sources (Schedule OS)" emoji="💸" className="!delay-[400ms]"
            total={{ label: "Total Other Sources", value: Number(os?.total_other_sources || 0) }}>
            <FieldRow label="Savings Bank Interest" fieldPath="other_sources.savings_bank_interest"
              value={Number(os?.savings_bank_interest || 0)} conf={F("other_sources.savings_bank_interest")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="FD Interest" fieldPath="other_sources.fd_interest"
              value={Number(os?.fd_interest || 0)} conf={F("other_sources.fd_interest")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
          </SectionCard>

          {/* Deductions */}
          <SectionCard title="Deductions (Chapter VI-A)" emoji="🛡️" className="!delay-[500ms]"
            total={{ label: "Total Eligible Deductions", value: Number(ded?.total_deductions || 0) }}>
            {regime === "new" && (
              <div className="bg-gray-100/50 text-gray-500 text-xs px-4 py-3 rounded-lg mb-4 font-medium border border-gray-200">
                Most deductions are not applicable under the New Regime (except 80CCD(2)). Values shown are analyzed for Old Regime comparison.
              </div>
            )}
            <div className={regime === "new" ? "opacity-50 grayscale-[30%] pointer-events-none" : ""}>
              <FieldRow label="Section 80C (LIC, PPF, ELSS…)" fieldPath="deductions.sec_80c"
                value={Number(ded?.sec_80c || 0)} conf={F("deductions.sec_80c")}
                onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
              <FieldRow label="Section 80D (Health Insurance)" fieldPath="deductions.sec_80d"
                value={Number(ded?.sec_80d || 0)} conf={F("deductions.sec_80d")}
                onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
              <FieldRow label="Section 80TTA (Savings Interest)" fieldPath="deductions.sec_80tta"
                value={Number(ded?.sec_80tta || 0)} conf={F("deductions.sec_80tta")}
                onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            </div>
            <FieldRow label="Section 80CCD(2) (Employer NPS)" fieldPath="deductions.sec_80ccd_2"
              value={Number(ded?.sec_80ccd_2 || 0)} conf={F("deductions.sec_80ccd_2")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
          </SectionCard>

          {/* Regime recommendation */}
          {data.explanations?.regime_recommendation && (
            <div className="bg-gradient-to-br from-brand-600 to-brand-800 text-white rounded-2xl px-6 py-5 shadow-glow animate-fade-in" style={{ animationDelay: '0.6s' }}>
              <div className="flex items-center gap-2 mb-2 text-brand-200 font-semibold text-sm uppercase tracking-wide">
                <span className="text-xl">✨</span> AI Regime Strategy
              </div>
              <p className="text-sm leading-relaxed font-medium">
                {data.explanations.regime_recommendation}
              </p>
            </div>
          )}

          {/* Tax computation */}
          <SectionCard title={`Final Tax Computation (${regime.toUpperCase()} REGIME)`} emoji="🧮" className="!delay-[700ms] border-2 border-brand-100">
            <FieldRow label="Gross Total Income" fieldPath="tax_computation.gross_total_income"
              value={Number(tc?.gross_total_income || 0)} conf={F("tax_computation.gross_total_income")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Taxable Income" fieldPath="tax_computation.taxable_income"
              value={Number(tc?.taxable_income || 0)} conf={F("tax_computation.taxable_income")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Tax Before Rebate" fieldPath="tax_computation.tax_before_rebate"
              value={Number(tc?.tax_before_rebate || 0)}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Rebate u/s 87A" fieldPath="tax_computation.rebate_87a"
              value={Number(tc?.rebate_87a || 0)} conf={F("tax_computation.rebate_87a")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Health & Education Cess (4%)" fieldPath="tax_computation.health_education_cess"
              value={Number(tc?.health_education_cess || 0)}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="Total Tax Liability" fieldPath="tax_computation.total_tax_liability"
              value={Number(tc?.total_tax_liability || 0)}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
            <FieldRow label="TDS Already Deducted" fieldPath="tds_details.0.tds_deducted"
              value={Number(tc?.tds_deducted || 0)} conf={F("tds_details.0.tds_deducted")}
              onEdit={(f, l, v) => setEditTarget({ field: f, label: l, value: v })} />
          </SectionCard>

          {/* Final result */}
          <div className={`rounded-2xl px-8 py-6 text-center shadow-lg transform transition-all hover:scale-[1.01] animate-fade-in
            ${isRefund
              ? "bg-gradient-to-r from-success-500 to-success-600 text-white"
              : "bg-gradient-to-r from-amber-500 to-orange-500 text-white"}`}
            style={{ animationDelay: '0.8s' }}
          >
            {isRefund ? (
              <>
                <div className="text-sm font-bold uppercase tracking-widest text-success-100 mb-2">Net Result</div>
                <div className="text-4xl font-extrabold font-mono tracking-tight">
                  Refund: ₹{refundAmt.toLocaleString("en-IN")}
                </div>
                <div className="text-sm mt-2 text-success-100 font-medium">Expected refund to your linked bank account</div>
              </>
            ) : (
              <>
                <div className="text-sm font-bold uppercase tracking-widest text-amber-100 mb-2">Net Result</div>
                <div className="text-4xl font-extrabold font-mono tracking-tight">
                  Tax Due: ₹{Number(tc?.tax_payable || 0).toLocaleString("en-IN")}
                </div>
                <div className="text-sm mt-2 text-amber-100 font-medium">Please pay this amount before filing</div>
              </>
            )}
          </div>
        </div>

        {/* Chat Button */}
        <div className="mt-12 text-center animate-fade-in" style={{ animationDelay: '1s' }}>
          <a
            href={`/chat?session=${sessionId}`}
            className="inline-flex items-center gap-3 bg-white px-8 py-4 rounded-full shadow-soft hover:shadow-glow text-gray-800 font-semibold text-sm transition-all hover:-translate-y-1 border border-gray-100"
          >
            <span className="text-brand-500 text-xl">💬</span>
            Discuss this return with AI Assistant
          </a>
        </div>
      </div>

      {/* Edit modal */}
      {editTarget && (
        <EditModal
          {...editTarget}
          sessionId={sessionId}
          onClose={() => setEditTarget(null)}
          onSaved={loadData}
        />
      )}
    </div>
  );
}

export default function FormPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <div className="w-10 h-10 border-4 border-brand-200 border-t-brand-600 rounded-full animate-spin" />
        </div>
      </div>
    }>
      <FormPageInner />
    </Suspense>
  );
}
