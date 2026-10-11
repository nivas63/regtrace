"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { clearSession, getUser, type User } from "@/lib/api";

const NAV: { section: string; items: [string, string][] }[] = [
  { section: "Overview", items: [["Executive dashboard", "/"], ["Ask RegTrace", "/ask"]] },
  { section: "Regulatory intelligence", items: [["Regulations", "/regulations"], ["Obligations", "/obligations"], ["Change impact", "/impact"], ["What-if simulation", "/simulation"], ["Cross-regulation & conflicts", "/conflicts"]] },
  { section: "Bank controls", items: [["Controls & evidence", "/controls"], ["Obligation → control map", "/mappings"], ["Documents & upload", "/documents"]] },
  { section: "Compliance", items: [["Findings & risk", "/findings"], ["Remediation", "/remediation"], ["Review queue", "/reviews"]] },
  { section: "Trust & operations", items: [["Agent runs", "/runs"], ["Reliability & cost", "/reliability"], ["Audit log", "/audit"]] },
];

export function Shell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const current = getUser();
    if (!current && pathname !== "/login") router.replace("/login");
    setUser(current);
    setReady(true);
  }, [pathname, router]);

  if (pathname === "/login") return <>{children}</>;
  if (!ready || !user) return null;

  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 overflow-y-auto border-r border-line bg-white px-3 py-4 lg:block">
        <Link href="/" className="mb-5 flex items-center gap-2 px-2">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-accent text-sm font-bold text-white">RT</span>
          <span>
            <span className="block text-sm font-semibold">RegTrace</span>
            <span className="block text-[11px] text-ink-muted">Compliance intelligence</span>
          </span>
        </Link>
        {NAV.map((group) => (
          <div key={group.section} className="mb-4">
            <div className="px-2 pb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">{group.section}</div>
            {group.items.map(([label, href]) => {
              const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
              return (
                <Link key={href} href={href}
                  className={`block rounded-md px-2 py-1.5 text-sm ${active ? "bg-accent-soft font-medium text-accent" : "text-ink-soft hover:bg-slate-50"}`}>
                  {label}
                </Link>
              );
            })}
          </div>
        ))}
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-10 flex items-center justify-between gap-3 border-b border-line bg-white/90 px-4 py-2.5 backdrop-blur sm:px-6">
          <div className="text-xs text-ink-muted">
            <span className="font-semibold text-ink">{user.bank}</span>
            <span className="ml-2 rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-semibold text-amber-800 ring-1 ring-amber-200">SYNTHETIC DATA</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span className="text-ink-soft">{user.name} · <span className="uppercase text-ink-muted">{user.role}</span></span>
            <button className="btn-ghost" onClick={() => { clearSession(); router.replace("/login"); }}>Sign out</button>
          </div>
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">{children}</main>
        <footer className="border-t border-line px-6 py-3 text-center text-[11px] text-ink-muted">
          AI-assisted compliance assessment. Not legal advice — every high-impact conclusion requires human review.
        </footer>
      </div>
    </div>
  );
}
