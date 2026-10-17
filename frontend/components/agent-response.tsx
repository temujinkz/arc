"use client";

import { memo, useEffect, useState } from "react";
import { Check, Copy, LoaderCircle, Sparkles } from "lucide-react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeSourceUrl } from "@/lib/investigation";
import "./agent-response.css";

const plugins = [remarkGfm];
const components: Components = {
  // Response content is text, never executable HTML or remotely loaded media.
  img: ({ alt }) => alt ? <span className="arc-message-imageLabel">{alt}</span> : null,
  a: ({ href, children }) => {
    if (href?.startsWith("#")) return <a href={href}>{children}</a>;
    const url = safeSourceUrl(href);
    return url ? <a href={url} target="_blank" rel="noopener noreferrer">{children}</a> : <span>{children}</span>;
  },
  table: ({ children }) => <div className="arc-message-tableScroll" tabIndex={0} role="region" aria-label="Response table"><table>{children}</table></div>,
  pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
  // Keep heading levels inside the workspace's heading hierarchy.
  h1: ({ children }) => <h3 className="arc-message-title">{children}</h3>,
  h2: ({ children }) => <h3>{children}</h3>,
  h3: ({ children }) => <h4>{children}</h4>,
};

export const AgentResponse = memo(function AgentResponse({ text, label = "ARC", streaming = false }: {
  text: string; label?: string; streaming?: boolean;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  useEffect(() => {
    if (copyState === "idle") return;
    const timer = setTimeout(() => setCopyState("idle"), 2000);
    return () => clearTimeout(timer);
  }, [copyState]);
  if (!text.trim()) return null;
  return <article className="arc-message-response" aria-label={`${label} response`}>
    <div className="arc-message-meta">
      <Sparkles size={13} aria-hidden="true" /><span>{label}</span>
      {streaming && <span className="arc-message-writing"><LoaderCircle size={11} className="activity-spin" aria-hidden="true" />Writing</span>}
      <button type="button" className="arc-message-copy" disabled={streaming} aria-label={copyState === "copied" ? "Response copied" : "Copy response"}
        onClick={async () => { try { await navigator.clipboard.writeText(text); setCopyState("copied"); } catch { setCopyState("failed"); } }}>
        {copyState === "copied" ? <Check size={12} /> : <Copy size={12} />}<span>{copyState === "copied" ? "Copied" : copyState === "failed" ? "Couldn't copy" : "Copy"}</span>
      </button>
    </div>
    <div className="arc-message-prose"><Markdown remarkPlugins={plugins} components={components} skipHtml>{text}</Markdown></div>
  </article>;
});
