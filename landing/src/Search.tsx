import { forwardRef, useEffect, useId, useMemo, useRef, useState } from "react";
import type { GNode } from "./data";
import { fullName } from "./live";

type Props = {
  nodes: GNode[];                       // everything that can be found: the record plus anyone added
  onPick: (id: string) => void;
  onAdd?: (name: string) => void;       // starts a live search for a name that is not in the record yet
  stage?: boolean;
};

export const Search = forwardRef<HTMLInputElement, Props>(function Search({ nodes, onPick, onAdd, stage }, ref) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const inner = useRef<HTMLInputElement | null>(null);
  const listId = useId();

  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? nodes.filter((n) => (n.kind === "person" || n.kind === "company") && n.label.toLowerCase().includes(s)).slice(0, 7) : [];
  }, [q, nodes]);
  useEffect(() => setActive(0), [q]);

  const name = fullName(q);
  // Find only appears when the name matches nobody, so it can never start a duplicate search.
  const canFind = !!onAdd && results.length === 0 && !!name;

  const pick = (id: string) => { onPick(id); setQ(""); setOpen(false); };
  const find = () => { if (name && onAdd) { onAdd(name); setQ(""); setOpen(false); } };
  const search = () => { if (results[active]) pick(results[active].id); else if (q.trim()) setOpen(true); else inner.current?.focus(); };

  const setRef = (el: HTMLInputElement | null) => {
    inner.current = el;
    if (typeof ref === "function") ref(el); else if (ref) ref.current = el;
  };

  return (
    <div className={`search${stage ? " in-stage" : ""}`}>
      <div className="search-row">
        <input
          ref={setRef}
          role="combobox"
          aria-expanded={open && !!q.trim()}
          aria-controls={listId}
          aria-activedescendant={results[active] ? `${listId}-${active}` : undefined}
          aria-autocomplete="list"
          value={q}
          placeholder="Search a name"
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown" && results.length) { e.preventDefault(); setActive((a) => (a + 1) % results.length); }
            else if (e.key === "ArrowUp" && results.length) { e.preventDefault(); setActive((a) => (a - 1 + results.length) % results.length); }
            else if (e.key === "Enter") { e.preventDefault(); if (results.length) search(); else if (canFind) find(); }
            else if (e.key === "Escape") (e.target as HTMLInputElement).blur();
          }}
        />
        {stage && canFind && <button className="search-go find" onMouseDown={(e) => e.preventDefault()} onClick={find}>Find</button>}
        {!stage && <kbd>/</kbd>}
      </div>
      {open && q.trim() && (
        <ul id={listId} role="listbox" aria-label="Matches">
          {results.map((p, i) => (
            <li key={p.id} id={`${listId}-${i}`} role="option" aria-selected={i === active}>
              <button className={i === active ? "on" : ""} onMouseDown={() => pick(p.id)} onMouseEnter={() => setActive(i)}>
                <span>{p.label}</span>
                <small>{p.sub}</small>
              </button>
            </li>
          ))}
          {results.length === 0 && canFind && (
            <li role="option" aria-selected="false">
              <button className="add" onMouseDown={find}>
                <span>Find {name}</span>
                <small>Starts a live search of public sources</small>
              </button>
            </li>
          )}
          {results.length === 0 && !canFind && (
            <li className="empty">{onAdd ? "Nobody by that name yet. Type a first and last name to find them." : "Nobody by that name yet."}</li>
          )}
        </ul>
      )}
    </div>
  );
});
