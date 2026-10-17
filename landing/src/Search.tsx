import { forwardRef, useMemo, useState } from "react";
import { network } from "./data";
import { fullName } from "./live";

type Props = {
  onPick: (id: string) => void;
  onAdd?: (name: string) => void; // starts a live search for a name that is not in the record yet
  stage?: boolean;
};

export const Search = forwardRef<HTMLInputElement, Props>(function Search({ onPick, onAdd, stage }, ref) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const results = useMemo(() => {
    const s = q.trim().toLowerCase();
    return s ? network.nodes.filter((n) => n.label.toLowerCase().includes(s)).slice(0, 6) : [];
  }, [q]);
  const name = fullName(q);
  const canAdd = !!onAdd && results.length === 0 && !!name;

  const pick = (id: string) => { onPick(id); setQ(""); setOpen(false); };
  const add = () => { if (name && onAdd) { onAdd(name); setQ(""); setOpen(false); } };
  // The button and Enter do the same thing: open a match, or look up a new full name.
  const go = () => { if (results[0]) pick(results[0].id); else if (canAdd) add(); else setOpen(true); };

  return (
    <div className={`search${stage ? " in-stage" : ""}`}>
      <div className="search-row">
        <input
          ref={ref}
          value={q}
          placeholder={stage ? "Search a name, or add a new one" : "Search a name"}
          onChange={(e) => { setQ(e.target.value); setOpen(true); }}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 120)}
          onKeyDown={(e) => {
            if (e.key === "Enter") go();
            if (e.key === "Escape") (e.target as HTMLInputElement).blur();
          }}
          aria-label="Search people"
        />
        {stage && <button className="search-go" onMouseDown={(e) => e.preventDefault()} onClick={go}>Search</button>}
        {!stage && <kbd>/</kbd>}
      </div>
      {open && q.trim() && (
        <ul>
          {results.map((p) => (
            <li key={p.id}>
              <button onMouseDown={() => pick(p.id)}>
                <span>{p.label}</span>
                <small>{p.sub}</small>
              </button>
            </li>
          ))}
          {results.length === 0 && canAdd && (
            <li>
              <button className="add" onMouseDown={add}>
                <span>Look up {name}</span>
                <small>Starts a live search of public sources</small>
              </button>
            </li>
          )}
          {results.length === 0 && !canAdd && (
            <li className="empty">{onAdd ? "Nobody by that name yet. Type a first and last name to look them up." : "Nobody by that name yet."}</li>
          )}
        </ul>
      )}
    </div>
  );
});
