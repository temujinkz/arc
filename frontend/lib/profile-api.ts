import { API_BASE } from "./use-investigation";

export async function profileRequest(path: string, body?: unknown, method?: string) {
  // Keep loopback hostnames aligned so the owner cookie remains same-site.
  const base = new URL(API_BASE);
  if (typeof window !== "undefined" && ["localhost", "127.0.0.1"].includes(window.location.hostname)
      && ["localhost", "127.0.0.1"].includes(base.hostname)) base.hostname = window.location.hostname;
  const response = await fetch(`${base.toString().replace(/\/$/, "")}${path}`, {
    method: method || (body === undefined ? "GET" : "POST"), credentials: "include", cache: "no-store",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status}).`);
  return data;
}
