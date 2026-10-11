"use client";

import { useState, type FormEvent } from "react";

import { Badge, Card, Empty, ErrorBox, Json, PageHeader } from "@/components/ui";
import { post } from "@/lib/api";

type Answer = { answer: string; insufficient_evidence: boolean; claims: { text: string; citations: string[]; support: number }[];
  sources: { id: string; kind: string; section_path: string; document_title?: string; header?: string; rerank_score?: number; text?: string }[];
  query_type: string; rewritten_query: string; dropped_claims: number; retrieval: unknown[]; latency_ms: number; disclaimer: string };

const EXAMPLES = [
  "How often must the risk categorisation of customers be reviewed?",
  "Is the bank compliant with the privileged access MFA requirement?",
  "What is the deadline to report a cyber security incident?",
];

export default function AskPage() {
  const [question, setQuestion] = useState(EXAMPLES[0]);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function ask(event?: FormEvent) {
    event?.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setAnswer(await post<Answer>("/query", { question }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <PageHeader title="Ask RegTrace"
        subtitle="Hybrid retrieval (dense + lexical + graph, fused and reranked) with a relevance threshold. Every claim must cite a source; unsupported claims are removed, and the system answers 'Insufficient evidence' rather than guessing." />
      <form onSubmit={ask} className="card p-4">
        <input className="input" value={question} onChange={(e) => setQuestion(e.target.value)} />
        <div className="mt-2 flex flex-wrap gap-2">
          <button className="btn-primary" disabled={busy}>{busy ? "Thinking…" : "Ask"}</button>
          {EXAMPLES.map((e) => <button type="button" key={e} className="btn-ghost text-xs" onClick={() => setQuestion(e)}>{e}</button>)}
        </div>
      </form>
      <div className="mt-3"><ErrorBox error={error} /></div>
      {!answer ? <div className="mt-4"><Empty>Ask about regulations or the bank&apos;s compliance status.</Empty></div> : (
        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <Card title={<span>Answer {answer.insufficient_evidence ? <Badge value="INSUFFICIENT EVIDENCE" tone="purple" /> : <Badge value="GROUNDED" tone="green" />}</span>} className="lg:col-span-2">
            <p className="text-sm leading-relaxed">{answer.answer}</p>
            <ul className="mt-3 space-y-2">
              {answer.claims.map((c, i) => (
                <li key={i} className="rounded-lg border border-line p-2 text-sm">{c.text}
                  <div className="mt-1 flex gap-1">{c.citations.map((id) => <Badge key={id} value={id} tone="blue" />)}<span className="text-[10px] text-ink-muted">support {c.support}</span></div>
                </li>
              ))}
            </ul>
            <p className="mt-3 text-[11px] text-ink-muted">{answer.disclaimer} · {answer.query_type} · rewritten: “{answer.rewritten_query}” · {Math.round(answer.latency_ms)} ms{answer.dropped_claims ? ` · ${answer.dropped_claims} unsupported claim(s) removed` : ""}</p>
          </Card>
          <Card title="Sources">
            {answer.sources.map((s) => (
              <div key={s.id} className="mb-2 border-b border-line pb-2 text-xs last:border-0">
                <Badge value={s.id} tone="blue" /> {s.kind === "regulation" ? `${s.document_title} · para ${s.section_path}` : `Bank record · ${s.section_path}`}
                {s.rerank_score !== undefined ? <span className="ml-1 text-ink-muted">rerank {s.rerank_score}</span> : null}
                {s.header ? <div className="text-ink-muted">{s.header}</div> : null}
              </div>
            ))}
            <details className="text-xs"><summary className="cursor-pointer text-ink-muted">Retrieval trace</summary><Json value={answer.retrieval} /></details>
          </Card>
        </div>
      )}
    </>
  );
}
