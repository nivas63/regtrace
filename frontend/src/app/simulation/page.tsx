"use client";

import { useState } from "react";

import { A, Badge, Card, Empty, ErrorBox, Loading, PageHeader, Quote, Stat } from "@/components/ui";
import { fmtDateTime, post, useApi } from "@/lib/api";

type Target = { section_path: string; source_quote: string; old_period: string | null; new_period: string; coverage_before: number;
  coverage_after: number; gaps_before: string[]; gaps_after: string[]; new_gaps: string[];
  new_risks: { score: number; priority: string; reason: string }[];
  controls: { control_key: string; control_frequency: string; before: { rating: string; score: number }; after: { rating: string; score: number; reasons: string[] };
    extra_executions_per_year: number; extra_hours_per_year: number }[] };
type Sim = { id: string; run_id: string | null; scenario: string; status: string; created_at: string;
  result: { interpretation?: string; supported?: boolean; message?: string; summary?: string; targets?: Target[]; workload_hours_per_year?: number; impacted_business_units?: string[] } };

const EXAMPLES = [
  "What if disaster recovery drills must be performed every quarter instead of every six months?",
  "What if the risk categorisation of customers must be reviewed every month?",
  "What if KYC training for frontline staff must happen every six months?",
];

export default function SimulationPage() {
  const [scenario, setScenario] = useState(EXAMPLES[0]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const list = useApi<Sim[]>("/simulation", 4000);
  const current = list.data?.find((s) => s.id === (selected ?? list.data?.[0]?.id));

  async function run() {
    setError(null);
    try {
      const res = await post<{ simulation_id: string }>("/simulation/run", { scenario });
      setSelected(res.simulation_id);
      await list.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  const r = current?.result;
  return (
    <>
      <PageHeader title="What-if simulation"
        subtitle="The LLM only parses the question. Propagation reuses the exact deterministic engines of the real assessment on an in-memory copy — the baseline is never modified." />
      <Card>
        <label className="label">Scenario</label>
        <textarea className="input h-20" value={scenario} onChange={(e) => setScenario(e.target.value)} />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button className="btn-primary" onClick={run}>Simulate</button>
          {EXAMPLES.map((e) => <button key={e} className="btn-ghost text-xs" onClick={() => setScenario(e)}>{e.slice(8, 60)}…</button>)}
        </div>
        <ErrorBox error={error} />
      </Card>
      <div className="mt-4 grid gap-4 lg:grid-cols-4">
        <Card title="History">
          {list.data?.map((s) => (
            <button key={s.id} onClick={() => setSelected(s.id)} className={`mb-1 block w-full rounded p-2 text-left text-xs ${current?.id === s.id ? "bg-accent-soft" : "hover:bg-slate-50"}`}>
              <Badge value={s.status} /> {s.scenario.slice(0, 70)}<div className="text-ink-muted">{fmtDateTime(s.created_at)}</div>
            </button>
          )) ?? <Loading />}
        </Card>
        <div className="space-y-4 lg:col-span-3">
          {!current ? <Empty>Run a scenario.</Empty> : current.status === "PENDING" ? <Loading what="Simulating" /> : (
            <>
              <Card title="Result" actions={current.run_id ? <A href={`/runs/${current.run_id}`}>Trace</A> : null}>
                <p className="text-sm"><span className="font-semibold">Interpretation:</span> {r?.interpretation}</p>
                {r?.supported === false ? <p className="mt-2 rounded bg-amber-50 p-2 text-sm text-amber-800">{r.message}</p> : <p className="mt-2 text-sm">{r?.summary}</p>}
              </Card>
              {r?.supported ? (
                <div className="grid grid-cols-3 gap-3">
                  <Stat label="Obligations affected" value={r.targets?.length ?? 0} />
                  <Stat label="Extra effort" value={`${r.workload_hours_per_year ?? 0} h/yr`} />
                  <Stat label="Departments" value={r.impacted_business_units?.length ?? 0} hint={r.impacted_business_units?.join(", ")} />
                </div>
              ) : null}
              {r?.targets?.map((t) => (
                <Card key={t.section_path} title={<span>Para {t.section_path}: <span className="font-mono">{t.old_period ?? "—"} → {t.new_period}</span></span>}>
                  <Quote>{t.source_quote}</Quote>
                  <p className="mt-2 text-xs text-ink-muted">Coverage {Math.round(t.coverage_before * 100)}% → {Math.round(t.coverage_after * 100)}% · new gaps: {t.new_gaps.join(", ") || "none"}</p>
                  <table className="table mt-2"><thead><tr><th>Control</th><th>Runs</th><th>Before</th><th>After</th><th>Extra runs/yr</th><th>Extra hours/yr</th></tr></thead>
                    <tbody>{t.controls.map((c) => <tr key={c.control_key}><td>{c.control_key}</td><td className="font-mono text-xs">{c.control_frequency}</td>
                      <td><Badge value={c.before.rating} /></td><td><Badge value={c.after.rating} /><div className="text-[10px] text-ink-muted">{c.after.reasons[0]}</div></td>
                      <td className="tabular-nums">{c.extra_executions_per_year}</td><td className="tabular-nums">{c.extra_hours_per_year}</td></tr>)}</tbody></table>
                  {t.new_risks.map((nr, i) => <p key={i} className="mt-1 text-xs"><Badge value={nr.priority} /> {nr.score} — {nr.reason}</p>)}
                </Card>
              ))}
            </>
          )}
        </div>
      </div>
    </>
  );
}
