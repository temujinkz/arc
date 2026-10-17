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
  ["M386 206 C386 250 55 244 55 288", 2.6],
  ["M590 206 C590 256 85 238 85 288", 2.8],
  ["M794 206 C794 260 115 236 115 288", 3],
  ["M170 320 H208", 1],
  ["M410 320 H448", 1],
  ["M620 320 H658", 1],
  ["M810 320 H848", 1],
];

export function Pipeline() {
  return (
    <div className="pipe-scroll">
      <svg className="pipe-svg" data-lines viewBox="0 0 1000 380" role="img"
        aria-label="A search goes to a FastAPI service and a Claude Agent SDK agent, which calls three kinds of research tool over MCP. Everything found goes into a hashed evidence ledger. Workers and a critic review claims against exact saved passages, then the graph is projected from accepted findings, saved to Neo4j and served on this site.">
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
        <Node i={2} x={460} y={20} w={260} title="Agent" sub="Claude Agent SDK, asks you who" core />

        <Node i={3} x={296} y={150} w={180} h={56} title="Web search" sub="open web and journals" />
        <Node i={4} x={500} y={150} w={180} h={56} title="Profile lookup" sub="LinkedIn and GitHub" />
        <Node i={5} x={704} y={150} w={180} h={56} title="Page reader" sub="pages and posts" />

        <Node i={6} x={0} y={290} w={170} title="Evidence ledger" sub="saved and hashed" />
        <Node i={7} x={210} y={290} w={200} title="Claim review" sub="workers cite, critic decides" />
        <Node i={8} x={450} y={290} w={170} title="Graph" sub="built from accepted work" />
        <Node i={9} x={660} y={290} w={150} title="Neo4j" sub="the saved graph" core />
        <Node i={10} x={850} y={290} w={150} title="This site" sub="served as a profile" />

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
