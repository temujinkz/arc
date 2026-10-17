"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { applyEvent, initialState, type ArcEvent } from "./investigation";

export const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");
const STORAGE_KEY = "arc.current-investigation";
export type InvestigationSummary = { session_id: string; target: string; active: boolean; created_at: number; updated_at: number };

async function request(path: string, body?: unknown) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : `Request failed (${response.status}).`);
  return data;
}

export function useInvestigation() {
  const [state, setState] = useState(initialState);
  const [history, setHistory] = useState<InvestigationSummary[]>([]);
  const [historyError, setHistoryError] = useState("");
  const stream = useRef<EventSource | null>(null);
  const active = useRef(false);
  const mounted = useRef(false);
  const pendingStart = useRef<{ target: string; refreshSources: boolean; requestId: string } | null>(null);
  const pendingChat = useRef<{ sessionId: string; text: string; requestId: string; offset: number } | null>(null);
  const pendingCase = useRef<{ target: string; requestId: string } | null>(null);
  const pendingSource = useRef<{ sessionId: string; signature: string; requestId: string; offset: number } | null>(null);
  const lastEventId = useRef(0);

  const refreshHistory = useCallback(async () => {
    try {
      const data = await request("/api/investigations");
      if (mounted.current) { setHistory(data.investigations); setHistoryError(""); }
    } catch { if (mounted.current) setHistoryError("History is unavailable. Check the backend connection."); }
  }, []);

  const connect = useCallback((sessionId: string, after = 0) => {
    stream.current?.close();
    const source = new EventSource(`${API_BASE}/api/investigate/stream/${encodeURIComponent(sessionId)}?after=${after}`);
    stream.current = source;
    lastEventId.current = after;
    source.onopen = () => { if (stream.current === source) setState((s) => ({ ...s, connection: "connected" })); };
    source.onmessage = (message) => {
      if (stream.current !== source) return;
      try {
        const event: ArcEvent = JSON.parse(message.data);
        if (typeof event.type !== "string") throw new Error("Invalid event.");
        // A historical completion can precede newer chat turns. Only the server's
        // end marker closes replay, never a completion encountered in its history.
        if (event.type === "stream.end") {
          source.close(); active.current = false; void refreshHistory(); return;
        }
        const id = Number(message.lastEventId);
        if (id && id <= lastEventId.current) return;
        if (id) lastEventId.current = id;
        setState((s) => applyEvent(s, event));
        if (["case.ready", "source.complete", "source.failed", "source.cancelled", "investigation.complete", "investigation.failed", "investigation.cancelled", "conversation.complete", "conversation.failed", "conversation.cancelled"].includes(event.type)) {
          active.current = false;
        } else if (["investigation.started", "conversation.started", "source.started"].includes(event.type)) active.current = true;
      } catch { setState((s) => ({ ...s, error: "An unreadable stream event was received." })); }
    };
    source.onerror = () => {
      if (stream.current !== source) return;
      if (source.readyState === EventSource.CLOSED) {
        active.current = false;
        setState((s) => ({ ...s, status: "failed", prompts: [],
          error: "This conversation is unavailable. The server may have restarted; no paid operation was restarted." }));
      } else setState((s) => ({ ...s, connection: "reconnecting" }));
    };
  }, [refreshHistory]);

  useEffect(() => {
    mounted.current = true;
    const saved = sessionStorage.getItem(STORAGE_KEY);
    queueMicrotask(() => {
      if (!mounted.current) return;
      void refreshHistory();
      if (saved) { active.current = true; setState({ ...initialState, sessionId: saved, status: "running" }); connect(saved); }
    });
    return () => { mounted.current = false; stream.current?.close(); };
  }, [connect, refreshHistory]);

  const open = (item: InvestigationSummary) => {
    if (state.status === "starting" || item.session_id === state.sessionId) return;
    active.current = item.active;
    sessionStorage.setItem(STORAGE_KEY, item.session_id);
    setState({ ...initialState, sessionId: item.session_id, target: item.target, status: "running" });
    connect(item.session_id);
  };
  const newInvestigation = () => {
    if (state.status === "starting") return;
    stream.current?.close(); stream.current = null; active.current = false;
    sessionStorage.removeItem(STORAGE_KEY); setState(initialState); void refreshHistory();
  };

  const start = async (target: string, refreshSources = false) => {
    target = target.trim();
    if (!target || active.current) return false;
    active.current = true; stream.current?.close();
    const attempt = pendingStart.current?.target === target && pendingStart.current.refreshSources === refreshSources ? pendingStart.current : { target, refreshSources, requestId: crypto.randomUUID() };
    pendingStart.current = attempt;
    setState({ ...initialState, target, status: "starting" });
    try {
      const result = await request("/api/investigations", { target, request_id: attempt.requestId, refresh_sources: refreshSources });
      sessionStorage.setItem(STORAGE_KEY, result.session_id); pendingStart.current = null;
      if (!mounted.current) return true;
      setState((s) => ({ ...s, sessionId: result.session_id, status: "running" }));
      connect(result.session_id); void refreshHistory(); return true;
    } catch (error) {
      active.current = false;
      if (mounted.current) setState((s) => ({ ...s, status: "failed", error: String(error) }));
      return false;
    }
  };

  const send = async (text: string) => {
    text = text.trim();
    if (!text || active.current || !state.sessionId) return false;
    active.current = true; stream.current?.close();
    const previous = pendingChat.current;
    const attempt = previous?.sessionId === state.sessionId && previous.text === text ? previous
      : { sessionId: state.sessionId, text, requestId: crypto.randomUUID(), offset: lastEventId.current };
    pendingChat.current = attempt;
    setState((s) => ({ ...s, status: "starting", error: "", phase: "conversation" }));
    try {
      await request(`/api/investigate/${encodeURIComponent(state.sessionId)}/messages`, { message: text, request_id: attempt.requestId });
      pendingChat.current = null;
      connect(state.sessionId, attempt.offset); void refreshHistory(); return true;
    } catch (error) {
      active.current = false;
      setState((s) => ({ ...s, status: "failed", error: String(error) })); return false;
    }
  };
  const answer = async (requestId: string, selected: string[], text: string) => {
    await request(`/api/investigate/${encodeURIComponent(state.sessionId)}/human-reply`, { request_id: requestId, selected, text });
  };
  const cancel = async () => {
    try { await request(`/api/investigate/${encodeURIComponent(state.sessionId)}/cancel`, {}); }
    catch (error) { setState((s) => ({ ...s, error: String(error) })); }
  };
  const evidence = (id: string) => request(`/api/investigate/${encodeURIComponent(state.sessionId)}/evidence/${encodeURIComponent(id)}`);
  const createCase = async (target: string) => {
    target = target.trim();
    if (!target || active.current) return false;
    active.current = true;
    const attempt = pendingCase.current?.target === target ? pendingCase.current : { target, requestId: crypto.randomUUID() };
    pendingCase.current = attempt;
    setState({ ...initialState, target, status: "starting", collectionMode: "sources" });
    try {
      const result = await request("/api/cases", { target, request_id: attempt.requestId });
      pendingCase.current = null;
      sessionStorage.setItem(STORAGE_KEY, result.session_id);
      setState((s) => ({ ...s, sessionId: result.session_id }));
      connect(result.session_id); void refreshHistory(); return true;
    } catch (error) {
      active.current = false;
      setState((s) => ({ ...s, status: "failed", error: String(error) })); return false;
    }
  };
  const collectSource = async (tool: string, args: Record<string, unknown>, freshSources: boolean) => {
    if (!state.sessionId || active.current) return false;
    active.current = true;
    const signature = JSON.stringify([tool, args, freshSources]);
    const previous = pendingSource.current;
    const attempt = previous?.sessionId === state.sessionId && previous.signature === signature ? previous
      : { sessionId: state.sessionId, signature, requestId: crypto.randomUUID(), offset: lastEventId.current };
    pendingSource.current = attempt;
    setState((s) => ({ ...s, status: "starting", phase: "source", error: "" }));
    try {
      await request(`/api/investigate/${encodeURIComponent(state.sessionId)}/sources`, {
        tool, arguments: args, fresh_sources: freshSources, request_id: attempt.requestId,
      });
      pendingSource.current = null;
      connect(state.sessionId, attempt.offset); void refreshHistory(); return true;
    } catch (error) {
      active.current = false;
      setState((s) => ({ ...s, status: "failed", phase: "source", error: String(error) })); return false;
    }
  };
  return { state, start, send, answer, cancel, evidence, history, historyError, refreshHistory, open, newInvestigation, createCase, collectSource };
}
