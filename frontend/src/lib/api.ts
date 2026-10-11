"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
const TOKEN_KEY = "regtrace.token";
const USER_KEY = "regtrace.user";

export type User = { id: string; email: string; name: string; role: string; bank: string; bank_id: string };

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(message);
  }
}

function safeStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function getToken(): string | null {
  return safeStorage()?.getItem(TOKEN_KEY) ?? null;
}

export function getUser(): User | null {
  const raw = safeStorage()?.getItem(USER_KEY);
  try {
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function saveSession(token: string, user: User): void {
  const storage = safeStorage();
  storage?.setItem(TOKEN_KEY, token);
  storage?.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession(): void {
  const storage = safeStorage();
  storage?.removeItem(TOKEN_KEY);
  storage?.removeItem(USER_KEY);
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const token = getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (response.status === 401 && typeof window !== "undefined" && !path.startsWith("/auth/login")) {
    clearSession();
    window.location.href = "/login";
  }
  if (!response.ok) {
    let body: { detail?: string; title?: string; code?: string; request_id?: string } = {};
    try {
      body = await response.json();
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(response.status, body.code ?? "error", body.detail ?? body.title ?? response.statusText, body.request_id);
  }
  return (await response.json()) as T;
}

export function post<T>(path: string, body?: unknown): Promise<T> {
  return api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
}

/** Fetch on mount (and optionally poll). Returns data, error, loading and a reload function. */
export function useApi<T>(path: string | null, pollMs?: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(path));
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api<T>(path);
      if (alive.current) {
        setData(result);
        setError(null);
      }
    } catch (err) {
      if (alive.current) setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(Boolean(path));
    void load();
    if (!pollMs) return () => void (alive.current = false);
    const timer = window.setInterval(() => void load(), pollMs);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [load, path, pollMs]);

  return { data, error, loading, reload: load };
}

export function fmtDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function fmtDateTime(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

export function pct(value?: number | null): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}
