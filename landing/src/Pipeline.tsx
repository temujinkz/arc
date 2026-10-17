// The path a search takes through arc-engine. Wires are single smooth curves from one box to the
// next, with a small arrowhead only where they enter a box. A red dot travels along each wire.

type NodeProps = { x: number; y: number; w: number; h?: number; title: string; sub: string; i: number; core?: boolean };

function Node({ x, y, w, h = 60, title, sub, i, core }: NodeProps) {
  return (
    <g className="n" style={{ ["--i" as string]: i }}>
      <rect x={x} y={y} width={w} height={h} rx={14} className={core ? "node core" : "node"} />
      <text x={x + w / 2} y={y + h / 2 - 3} className="t">{title}</text>
      <text x={x + w / 2} y={y + h / 2 + 15} className="s">{sub}</text>
    </g>
  );
}

// [path, seconds for a dot to travel it]
const wires: [string, number][] = [
  ["M190 50 H236", 1.2],
  ["M410 50 H456", 1.2],
  ["M590 80 C590 118 386 112 386 148", 2],
  ["M590 80 V148", 1.6],
  ["M590 80 C590 118 794 112 794 148", 2],
  ["M386 206 C386 250 85 244 85 288", 2.6],
  ["M590 206 C590 256 115 238 115 288", 2.8],
  ["M794 206 C794 260 145 236 145 288", 3],
  ["M230 320 H286", 1.2],
  ["M500 320 H556", 1.2],
  ["M760 320 H816", 1.2],
];

export function Pipeline() {
  return (
    <div className="pipe-scroll">
      <svg className="pipe-svg" data-lines viewBox="0 0 1000 380" role="img"
        aria-label="A search goes to a FastAPI service and a Claude Agent SDK agent, which calls three research tools over MCP. Results go to an evidence scratchpad, then a graph architect, then Neo4j, and are served on this site.">
        <defs>
          <marker id="head" viewBox="0 0 10 10" refX="8.5" refY="5" markerWidth="8" markerHeight="8" orient="auto">
            <path d="M2 2 L8 5 L2 8" className="arrow" />
          </marker>
        </defs>

        {wires.map(([d], i) => <path key={"w" + i} d={d} className="wire" markerEnd="url(#head)" />)}

        <g className="mcp">
          <rect x={562} y={103} width={56} height={22} rx={11} className="pill" />
          <text x={590} y={118} className="tag">MCP</text>
        </g>

        <Node i={0} x={0} y={20} w={190} title={'"Connor Guerrero"'} sub="search" />
        <Node i={1} x={240} y={20} w={170} title="FastAPI" sub="service" />
        <Node i={2} x={460} y={20} w={260} title="Agent" sub="Claude Agent SDK" core />

        <Node i={3} x={296} y={150} w={180} h={56} title="Web search" sub="web and publications" />
        <Node i={4} x={500} y={150} w={180} h={56} title="Identity lookup" sub="usernames and accounts" />
        <Node i={5} x={704} y={150} w={180} h={56} title="Profile scrape" sub="LinkedIn and GitHub" />

        <Node i={6} x={0} y={290} w={230} title="Evidence scratchpad" sub="saved with a SHA-256 hash" />
        <Node i={7} x={290} y={290} w={210} title="Graph architect" sub="schema-checked links" />
        <Node i={8} x={560} y={290} w={200} title="Neo4j" sub="the saved graph" core />
        <Node i={9} x={820} y={290} w={180} title="This site" sub="served as a profile" />

        {/* Dots ride the wires once the diagram is on screen. */}
        <g className="dots">
          {wires.map(([d, dur], i) => (
            <circle key={"d" + i} r={2.8} className="dot">
              <animateMotion path={d} dur={`${dur}s`} begin={`${(i * 0.37).toFixed(2)}s`} repeatCount="indefinite" />
              <animate attributeName="opacity" values="0;1;1;0" keyTimes="0;0.15;0.8;1" dur={`${dur}s`} begin={`${(i * 0.37).toFixed(2)}s`} repeatCount="indefinite" />
            </circle>
          ))}
        </g>
      </svg>
    </div>
  );
}
