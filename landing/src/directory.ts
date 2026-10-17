import { useCallback, useEffect, useRef, useState } from "react";

// The people directory (Apollo through Monid), served by the backend. Looking someone up is free and returns a role
// and employer. Revealing a profile and work email, or a phone number, costs money and always needs a confirmed click.

export type DirProfile = {
  title: string | null;
  headline: string | null;
  seniority: string | null;
  location: string | null;
  linkedin_url: string | null;
  github_url: string | null;
  twitter_url: string | null;
  history: { company: string | null; title: string | null; start: string | null; end: string | null; current: boolean }[];
  company: { name: string | null; website: string | null; industry: string | null; employees: number | null; founded: number | null; stage: string | null } | null;
};
export type DirView = {
  key: string;
  looked_up: boolean;
  found: boolean;
  preview: { apollo_id: string; title: string | null; company: string | null; has_email: boolean; has_phone: boolean } | null;
  profile: DirProfile | null;
  contact: { emails: { value: string; status: string }[]; personal_emails: string[]; phones: { value: string; type: string }[]; phone_pending: string | null } | null;
};
export type DirStatus = { configured: boolean; spent_usd: number; cap_usd: number; price_usd: { profile_and_email: number; phone_extra: number } };
export type Who = { name: string; company: string | null };

const API = ((import.meta.env.VITE_API_URL as string | undefined) ?? "").replace(/\/$/, "");

// Must match the backend's person_key.
export const dirKey = (w: Who) => `${norm(w.name)}|${norm(w.company)}`;
const norm = (s: string | null | undefined) => (s ?? "").trim().toLowerCase().replace(/\s+/g, " ");

async function call(path: string, body?: unknown): Promise<unknown> {
  const res = await fetch(API + path, body === undefined ? undefined : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  let data: { detail?: unknown } | null = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error(typeof data?.detail === "string" ? data.detail : `The directory answered ${res.status}.`);
  return data;
}

export function useDirectory(people: Who[]) {
  const [status, setStatus] = useState<DirStatus | null>(null);
  const [views, setViews] = useState<Record<string, DirView>>({});
  const [busy, setBusy] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const asked = useRef(new Set<string>());

  const refreshStatus = useCallback(async () => {
    try { setStatus((await call("/api/directory/status")) as DirStatus); } catch { setStatus(null); }
  }, []);
  useEffect(() => { void refreshStatus(); }, [refreshStatus]);

  const put = useCallback((v: DirView) => setViews((vs) => ({ ...vs, [v.key]: v })), []);

  // Free lookups for everyone shown, three at a time. The backend remembers answers, so reloading costs nothing.
  const wanted = people.map(dirKey).join("|");
  useEffect(() => {
    if (!status?.configured) return;
    const todo = people.filter((p) => !asked.current.has(dirKey(p)));
    todo.forEach((p) => asked.current.add(dirKey(p)));
    let cancelled = false;
    (async () => {
      for (let i = 0; i < todo.length; i += 3) {
        await Promise.all(todo.slice(i, i + 3).map(async (p) => {
          try { const v = (await call("/api/directory/lookup", { name: p.name, company: p.company })) as DirView; if (!cancelled) put(v); }
          catch { asked.current.delete(dirKey(p)); }
        }));
      }
    })();
    return () => { cancelled = true; };
  }, [status?.configured, wanted]); // eslint-disable-line react-hooks/exhaustive-deps

  // Apollo delivers phone numbers minutes later, so a pending one is checked every few seconds (checking is free).
  useEffect(() => {
    const pending = Object.values(views).filter((v) => v.contact?.phone_pending);
    if (!pending.length) return;
    const timer = setInterval(async () => {
      for (const v of pending) {
        try { put((await call(`/api/directory/phone/${encodeURIComponent(v.contact!.phone_pending!)}`)) as DirView); void refreshStatus(); } catch { /* try again next time */ }
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [views, put, refreshStatus]);

  const reveal = useCallback(async (who: Who, phone: boolean) => {
    const key = dirKey(who);
    setBusy((b) => ({ ...b, [key]: phone ? "phone" : "email" }));
    setErrors((e) => ({ ...e, [key]: "" }));
    try {
      put((await call("/api/directory/reveal", { name: who.name, company: who.company, phone, confirm: true })) as DirView);
      void refreshStatus();
    } catch (error) {
      setErrors((e) => ({ ...e, [key]: error instanceof Error ? error.message : String(error) }));
    } finally {
      setBusy((b) => { const { [key]: _gone, ...rest } = b; return rest; });
    }
  }, [put, refreshStatus]);

  return { status, views, busy, errors, reveal };
}

export type Directory = ReturnType<typeof useDirectory>;
