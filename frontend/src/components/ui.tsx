"use client";

import Link from "next/link";
import type { ReactNode } from "react";

const TONES: Record<string, string> = {
  green: "bg-emerald-50 text-emerald-700 ring-emerald-200",
  amber: "bg-amber-50 text-amber-800 ring-amber-200",
  red: "bg-rose-50 text-rose-700 ring-rose-200",
  blue: "bg-indigo-50 text-indigo-700 ring-indigo-200",
  gray: "bg-slate-100 text-slate-700 ring-slate-200",
  purple: "bg-violet-50 text-violet-700 ring-violet-200",
};

const VALUE_TONE: Record<string, keyof typeof TONES> = {
  EFFECTIVE: "green", PARTIALLY_EFFECTIVE: "amber", INEFFECTIVE: "red", INSUFFICIENT_EVIDENCE: "purple",
  P1: "red", P2: "amber", P3: "blue", P4: "gray",
  FULL: "green", PARTIAL: "amber", NONE: "gray",
  SUCCEEDED: "green", RUNNING: "blue", QUEUED: "gray", FAILED: "red", WAITING_REVIEW: "amber", FALLBACK: "amber",
  APPROVED: "green", REJECTED: "red", PENDING: "amber", PROPOSED: "blue", MODIFIED: "purple", STALE: "amber",
  OPEN: "red", RESOLVED: "green", SUPERSEDED: "gray", DONE: "green", IN_PROGRESS: "blue",
  MET: "green", NOT_MET: "red", INSUFFICIENT: "purple",
  APPLICABLE: "green", NOT_APPLICABLE: "gray", UNCERTAIN: "amber",
  ADDED: "blue", REMOVED: "red", UNCHANGED: "gray",
  MUST: "blue", MUST_NOT: "red", SHOULD: "gray", MAY: "gray",
  HIGH: "red", NORMAL: "gray",
};

export function Badge({ value, tone }: { value?: string | null; tone?: keyof typeof TONES }) {
  if (!value) return <span className="text-ink-muted">—</span>;
  const cls = TONES[tone ?? VALUE_TONE[value] ?? "gray"];
  return (
    <span className={`inline-flex items-center rounded-md px-1.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset ${cls}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle ? <p className="mt-1 max-w-3xl text-sm text-ink-muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="flex gap-2">{actions}</div> : null}
    </div>
  );
}

export function Card({ title, children, actions, className = "" }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title ? (
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      ) : null}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "red" | "amber" | "green" }) {
  const color = tone === "red" ? "text-rose-600" : tone === "amber" ? "text-amber-600" : tone === "green" ? "text-emerald-600" : "text-ink";
  return (
    <div className="card px-4 py-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-ink-muted">{label}</div>
      <div className={`mt-1 text-2xl font-semibold tabular-nums ${color}`}>{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-ink-muted">{hint}</div> : null}
    </div>
  );
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return <div className="py-10 text-center text-sm text-ink-muted">{what}…</div>;
}

export function ErrorBox({ error }: { error: string | null }) {
  if (!error) return null;
  return <div className="rounded-lg border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-line px-4 py-8 text-center text-sm text-ink-muted">{children}</div>;
}

export function Score({ value }: { value?: number | null }) {
  if (value === null || value === undefined) return <span className="text-ink-muted">—</span>;
  const width = Math.max(2, Math.min(100, Math.round(value * 100)));
  const color = value >= 0.75 ? "bg-emerald-500" : value >= 0.5 ? "bg-amber-500" : "bg-rose-500";
  return (
    <div className="flex items-center gap-2">
      <div className="h-1.5 w-16 rounded-full bg-slate-200">
        <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${width}%` }} />
      </div>
      <span className="text-xs tabular-nums text-ink-soft">{value.toFixed(2)}</span>
    </div>
  );
}

export function Quote({ children, source }: { children: ReactNode; source?: ReactNode }) {
  return (
    <figure>
      <blockquote className="quote">“{children}”</blockquote>
      {source ? <figcaption className="mt-1 text-xs text-ink-muted">{source}</figcaption> : null}
    </figure>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="max-h-80 overflow-auto rounded-lg bg-slate-900 p-3 text-[11px] leading-relaxed text-slate-100">{JSON.stringify(value, null, 2)}</pre>;
}

export function A({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link href={href} className="font-medium text-accent hover:underline">
      {children}
    </Link>
  );
}

export function Tabs({ tabs, active, onChange }: { tabs: string[]; active: string; onChange: (t: string) => void }) {
  return (
    <div className="mb-4 flex gap-1 border-b border-line">
      {tabs.map((t) => (
        <button key={t} onClick={() => onChange(t)}
          className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${active === t ? "border-accent text-accent" : "border-transparent text-ink-muted hover:text-ink"}`}>
          {t}
        </button>
      ))}
    </div>
  );
}

/** Highlights [start, end) of `text` - used to show the verified source quote in context. */
export function Highlight({ text, start, end }: { text: string; start: number; end: number }) {
  if (start < 0 || end <= start || end > text.length) return <span className="whitespace-pre-wrap">{text}</span>;
  return (
    <span className="whitespace-pre-wrap">
      {text.slice(0, start)}
      <mark className="rounded bg-yellow-200 px-0.5">{text.slice(start, end)}</mark>
      {text.slice(end)}
    </span>
  );
}
