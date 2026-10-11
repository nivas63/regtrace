"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ErrorBox } from "@/components/ui";
import { post, saveSession, type User } from "@/lib/api";

const DEMO_USERS = [
  ["analyst@aravali.example", "Compliance analyst"],
  ["reviewer@aravali.example", "Reviewer / approver"],
  ["auditor@aravali.example", "Internal auditor (read-only)"],
  ["admin@aravali.example", "Administrator"],
];

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState(DEMO_USERS[0][0]);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await post<{ access_token: string; user: User }>("/auth/login", { email, password });
      saveSession(res.access_token, res.user);
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-screen place-items-center px-4">
      <form onSubmit={submit} className="card w-full max-w-md p-6">
        <div className="mb-6 flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded-xl bg-accent font-bold text-white">RT</span>
          <div>
            <h1 className="text-lg font-semibold">RegTrace</h1>
            <p className="text-xs text-ink-muted">Agentic regulatory compliance intelligence</p>
          </div>
        </div>
        <label className="label" htmlFor="email">User</label>
        <select id="email" className="input mb-3" value={email} onChange={(e) => setEmail(e.target.value)}>
          {DEMO_USERS.map(([value, label]) => (
            <option key={value} value={value}>{label} — {value}</option>
          ))}
        </select>
        <label className="label" htmlFor="password">Password</label>
        <input id="password" type="password" className="input mb-4" value={password}
          onChange={(e) => setPassword(e.target.value)} placeholder="Seed password (SEED_PASSWORD)" />
        <ErrorBox error={error} />
        <button className="btn-primary mt-3 w-full justify-center" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
        <p className="mt-4 text-[11px] leading-relaxed text-ink-muted">
          Demo environment with a SYNTHETIC bank (Aravali Bank Ltd) and SYNTHETIC regulations attributed to fictional
          authorities. Outputs are AI-assisted and require human review.
        </p>
      </form>
    </div>
  );
}
