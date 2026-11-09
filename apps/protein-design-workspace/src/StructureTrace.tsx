import { useEffect, useMemo, useRef, useState } from "react";
import { servedPdbUrl } from "./manual-attempt-review";
import {
  boundingRadius,
  catmullRom,
  centroid,
  contactAgreement,
  cross,
  heavyAtomContacts,
  normalize,
  parseStructure,
  secondaryStructureShare,
  subtract,
  type Agreement,
  type Contacts,
  type SecondaryStructure,
  type Structure,
  type Vec3,
} from "./structure";

export type RecordedContacts = {
  cutoff_angstrom: number;
  binder_residues_in_contact: number;
  target_residues_in_contact: number;
  residue_pairs: number;
  minimum_heavy_atom_distance_angstrom: number;
  binder_heavy_atoms: number;
  target_heavy_atoms: number;
};

export type TracedModel = {
  id: string;
  pdb_url: string;
  target_chain: string;
  binder_chain: string;
  target_residues: number;
  binder_residues: number;
  contacts: RecordedContacts;
};

type Palette = {
  helix: string;
  sheet: string;
  loop: string;
  edge: string;
};

const TARGET_PALETTE: Palette = {
  helix: "#12807a",
  sheet: "#0b6e6a",
  loop: "#5aa6a2",
  edge: "#064a47",
};

const BINDER_PALETTE: Palette = {
  helix: "#dd9412",
  sheet: "#c47b00",
  loop: "#dfb264",
  edge: "#8a5600",
};

const CONTACT_COLOR = "#b42318";
const HOTSPOT_COLOR = "#3D9BE9";
const SAMPLES_PER_RESIDUE = 5;

type Quad = {
  kind: "quad";
  depth: number;
  color: string;
  edge: string;
  points: { px: number; py: number }[];
};

type Segment = {
  kind: "segment";
  depth: number;
  color: string;
  width: number;
  from: { px: number; py: number };
  to: { px: number; py: number };
};

type Marker = {
  kind: "marker";
  depth: number;
  color: string;
  radius: number;
  at: { px: number; py: number };
  ring: boolean;
};

type Dash = {
  kind: "dash";
  depth: number;
  from: { px: number; py: number };
  to: { px: number; py: number };
};

type Primitive = Quad | Segment | Marker | Dash;

type Run = {
  chain: string;
  kind: SecondaryStructure;
  spine: Vec3[];
};

function runsFor(structure: Structure, chain: string): Run[] {
  const residues = structure.backbone[chain] ?? [];
  const assigned = structure.secondary[chain] ?? [];
  if (residues.length < 2) {
    return [];
  }
  const runs: Run[] = [];
  let start = 0;
  for (let index = 1; index <= residues.length; index += 1) {
    const ended = index === residues.length || assigned[index] !== assigned[start];
    if (!ended) {
      continue;
    }
    const slice = residues.slice(Math.max(start - 1, 0), Math.min(index + 1, residues.length));
    if (slice.length >= 2) {
      runs.push({
        chain,
        kind: assigned[start] ?? "L",
        spine: catmullRom(slice, SAMPLES_PER_RESIDUE),
      });
    }
    start = index;
  }
  return runs;
}

function ribbonWidth(kind: SecondaryStructure, t: number): number {
  if (kind === "H") {
    return 1.9;
  }
  if (kind === "E") {
    if (t > 0.78) {
      const tip = (t - 0.78) / 0.22;
      return 2.9 * (1 - tip);
    }
    return 1.7;
  }
  return 0;
}

function mix(color: string, toward: string, amount: number): string {
  const parse = (value: string) => {
    const hex = value.replace("#", "");
    return [
      parseInt(hex.slice(0, 2), 16),
      parseInt(hex.slice(2, 4), 16),
      parseInt(hex.slice(4, 6), 16),
    ];
  };
  const [r1, g1, b1] = parse(color);
  const [r2, g2, b2] = parse(toward);
  const blend = (left: number, right: number) =>
    Math.round(left + (right - left) * Math.min(Math.max(amount, 0), 1));
  return `rgb(${blend(r1, r2)}, ${blend(g1, g2)}, ${blend(b1, b2)})`;
}

export function StructureTrace({
  model,
  width = 720,
  height = 360,
  controls = false,
  contactsRecorded = true,
}: {
  model: TracedModel;
  width?: number;
  height?: number;
  /** The manual review is a passive record and renders no control. */
  controls?: boolean;
  /** False when the review did not record contact counts. */
  contactsRecorded?: boolean;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const viewRef = useRef({ yaw: 0.7, pitch: 0.35, zoom: 1 });
  const dragRef = useRef<{ x: number; y: number; yaw: number; pitch: number } | null>(null);
  const spinRef = useRef<number | null>(null);
  const [structure, setStructure] = useState<Structure | null>(null);
  const [contacts, setContacts] = useState<Contacts | null>(null);
  const [status, setStatus] = useState("Loading the PDB.");
  const [mismatch, setMismatch] = useState<string | null>(null);
  const [showContacts, setShowContacts] = useState(true);
  const [showInterface, setShowInterface] = useState(true);
  const [cartoon, setCartoon] = useState(true);
  const [spinning, setSpinning] = useState(false);

  const agreement = useMemo<Agreement[]>(
    () => (contacts ? contactAgreement(contacts, model.contacts) : []),
    [contacts, model],
  );

  const runs = useMemo<Run[]>(() => {
    if (!structure) {
      return [];
    }
    return [
      ...runsFor(structure, model.target_chain),
      ...runsFor(structure, model.binder_chain),
    ];
  }, [structure, model]);

  const share = useMemo(() => {
    if (!structure) {
      return null;
    }
    const assigned = structure.secondary[model.binder_chain] ?? [];
    return secondaryStructureShare(assigned);
  }, [structure, model]);

  function draw() {
    const canvas = canvasRef.current;
    if (!canvas || !structure) {
      return;
    }
    let context: CanvasRenderingContext2D | null = null;
    try {
      context = canvas.getContext("2d");
    } catch {
      context = null;
    }
    if (!context) {
      return;
    }
    const background = "#ffffff";
    const fog = background;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = background;
    context.fillRect(0, 0, canvas.width, canvas.height);

    const all = structure.heavyAtoms;
    if (all.length === 0) {
      return;
    }
    const centre = centroid(all);
    const radius = boundingRadius(all, centre);
    const scale = ((Math.min(canvas.width, canvas.height) * 0.44) / radius) * viewRef.current.zoom;
    const { yaw, pitch } = viewRef.current;
    const cosYaw = Math.cos(yaw);
    const sinYaw = Math.sin(yaw);
    const cosPitch = Math.cos(pitch);
    const sinPitch = Math.sin(pitch);

    const rotate = (point: Vec3) => {
      const x = point.x - centre.x;
      const y = point.y - centre.y;
      const z = point.z - centre.z;
      const x1 = x * cosYaw + z * sinYaw;
      const z1 = -x * sinYaw + z * cosYaw;
      const y1 = y * cosPitch - z1 * sinPitch;
      const z2 = y * sinPitch + z1 * cosPitch;
      return { x: x1, y: y1, z: z2 };
    };
    const project = (point: Vec3) => {
      const r = rotate(point);
      return {
        px: canvas.width / 2 + r.x * scale,
        py: canvas.height / 2 - r.y * scale,
        depth: r.z,
      };
    };
    const fogAmount = (depth: number) =>
      Math.min(Math.max((radius - depth) / (radius * 2.6), 0), 0.72);

    const primitives: Primitive[] = [];

    for (const run of runs) {
      const palette = run.chain === model.binder_chain ? BINDER_PALETTE : TARGET_PALETTE;
      const base =
        run.kind === "H" ? palette.helix : run.kind === "E" ? palette.sheet : palette.loop;
      const spine = run.spine;
      const projected = spine.map(project);
      const flat = !cartoon || run.kind === "L";
      if (flat) {
        const lineWidth = cartoon ? 2.4 : 1.8;
        for (let index = 1; index < projected.length; index += 1) {
          const previous = projected[index - 1];
          const current = projected[index];
          const depth = (previous.depth + current.depth) / 2;
          primitives.push({
            kind: "segment",
            depth,
            color: mix(base, fog, fogAmount(depth)),
            width: lineWidth,
            from: { px: previous.px, py: previous.py },
            to: { px: current.px, py: current.py },
          });
        }
        continue;
      }
      let carried: Vec3 = { x: 0, y: 0, z: 1 };
      const edges: { left: Vec3; right: Vec3 }[] = [];
      for (let index = 0; index < spine.length; index += 1) {
        const previous = spine[Math.max(index - 1, 0)];
        const next = spine[Math.min(index + 1, spine.length - 1)];
        const tangent = normalize(subtract(next, previous));
        const curvature = cross(
          subtract(spine[index], previous),
          subtract(next, spine[index]),
        );
        const magnitude = Math.hypot(curvature.x, curvature.y, curvature.z);
        const axis = magnitude > 1e-4 ? normalize(curvature) : carried;
        let side = normalize(cross(tangent, axis));
        if (Math.hypot(side.x, side.y, side.z) < 1e-6) {
          side = normalize(cross(tangent, { x: 0, y: 0, z: 1 }));
        }
        carried = axis;
        const halfWidth = ribbonWidth(run.kind, index / (spine.length - 1)) / 2;
        edges.push({
          left: {
            x: spine[index].x + side.x * halfWidth,
            y: spine[index].y + side.y * halfWidth,
            z: spine[index].z + side.z * halfWidth,
          },
          right: {
            x: spine[index].x - side.x * halfWidth,
            y: spine[index].y - side.y * halfWidth,
            z: spine[index].z - side.z * halfWidth,
          },
        });
      }
      for (let index = 1; index < edges.length; index += 1) {
        const a = project(edges[index - 1].left);
        const b = project(edges[index].left);
        const c = project(edges[index].right);
        const d = project(edges[index - 1].right);
        const depth = (a.depth + b.depth + c.depth + d.depth) / 4;
        const amount = fogAmount(depth);
        primitives.push({
          kind: "quad",
          depth,
          color: mix(base, fog, amount),
          edge: mix(palette.edge, fog, amount),
          points: [a, b, c, d].map((point) => ({ px: point.px, py: point.py })),
        });
      }
    }

    if (contacts && showInterface) {
      const residues = structure.backbone;
      const highlight = (chain: string, list: number[], color: string) => {
        for (const resSeq of list) {
          const residue = (residues[chain] ?? []).find((item) => item.resSeq === resSeq);
          if (!residue) {
            continue;
          }
          const point = project(residue);
          primitives.push({
            kind: "marker",
            depth: point.depth,
            color: mix(color, fog, fogAmount(point.depth)),
            radius: 3.6,
            at: { px: point.px, py: point.py },
            ring: false,
          });
        }
      };
      highlight(model.target_chain, contacts.targetResidues, TARGET_PALETTE.edge);
      highlight(model.binder_chain, contacts.binderResidues, BINDER_PALETTE.edge);
    }

    if (contacts && showContacts) {
      for (const pair of contacts.pairs) {
        const from = project(pair.from);
        const to = project(pair.to);
        primitives.push({
          kind: "dash",
          depth: (from.depth + to.depth) / 2,
          from: { px: from.px, py: from.py },
          to: { px: to.px, py: to.py },
        });
      }
    }

    primitives.sort((left, right) => left.depth - right.depth);

    context.lineJoin = "round";
    context.lineCap = "round";
    for (const item of primitives) {
      if (item.kind === "quad") {
        context.beginPath();
        context.moveTo(item.points[0].px, item.points[0].py);
        for (const point of item.points.slice(1)) {
          context.lineTo(point.px, point.py);
        }
        context.closePath();
        context.fillStyle = item.color;
        context.fill();
        context.strokeStyle = item.edge;
        context.lineWidth = 0.7;
        context.stroke();
        continue;
      }
      if (item.kind === "segment") {
        context.beginPath();
        context.moveTo(item.from.px, item.from.py);
        context.lineTo(item.to.px, item.to.py);
        context.strokeStyle = item.color;
        context.lineWidth = item.width;
        context.stroke();
        continue;
      }
      if (item.kind === "marker") {
        context.beginPath();
        context.arc(item.at.px, item.at.py, item.radius, 0, Math.PI * 2);
        context.fillStyle = item.color;
        context.fill();
        if (item.ring) {
          context.strokeStyle = HOTSPOT_COLOR;
          context.lineWidth = 2;
          context.stroke();
        }
        continue;
      }
      context.save();
      context.setLineDash([3, 3]);
      context.beginPath();
      context.moveTo(item.from.px, item.from.py);
      context.lineTo(item.to.px, item.to.py);
      context.strokeStyle = CONTACT_COLOR;
      context.lineWidth = 1.3;
      context.stroke();
      context.restore();
    }
  }

  useEffect(() => {
    let cancelled = false;
    setStructure(null);
    setContacts(null);
    setMismatch(null);
    setStatus("Loading the alpha-carbon trace.");
    const canvas = canvasRef.current;
    if (canvas) {
      try {
        canvas.getContext("2d")?.clearRect(0, 0, canvas.width, canvas.height);
      } catch {
        // jsdom has no canvas implementation.
      }
    }
    if (!servedPdbUrl(model.pdb_url)) {
      setStatus("This PDB is not in the file list for this review.");
      return;
    }
    fetch(model.pdb_url)
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(String(response.status));
        }
        return response.text();
      })
      .then((text) => {
        if (cancelled) {
          return;
        }
        const parsed = parseStructure(text);
        const chainA = (parsed.backbone[model.target_chain] ?? []).length;
        const chainB = (parsed.backbone[model.binder_chain] ?? []).length;
        if (chainA !== model.target_residues || chainB !== model.binder_residues) {
          setMismatch(
            `The loaded PDB has ${chainA} CA atoms on chain ${model.target_chain} and ${chainB} on chain ${model.binder_chain}. This review record expects ${model.target_residues} and ${model.binder_residues}.`,
          );
        } else {
          setMismatch(null);
        }
        setStructure(parsed);
        setContacts(
          heavyAtomContacts(
            parsed.heavyAtoms,
            model.target_chain,
            model.binder_chain,
            model.contacts.cutoff_angstrom,
          ),
        );
        setStatus(
          `Chain ${model.target_chain}, target, ${chainA} CA. Chain ${model.binder_chain}, binder, ${chainB} CA.`,
        );
      })
      .catch(() => {
        if (!cancelled) {
          setStatus("The PDB did not load.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [model]);

  useEffect(() => {
    draw();
  }, [structure, contacts, runs, showContacts, showInterface, cartoon, width, height]);

  useEffect(() => {
    if (!spinning) {
      return;
    }
    let frame = 0;
    const step = () => {
      viewRef.current.yaw += 0.006;
      draw();
      frame = window.requestAnimationFrame(step);
      spinRef.current = frame;
    };
    frame = window.requestAnimationFrame(step);
    return () => window.cancelAnimationFrame(frame);
  }, [spinning, structure, contacts, runs, showContacts, showInterface, cartoon]);

  const disagreements = agreement.filter((item) => !item.matches);

  return (
    <div className="trace">
      {controls ? (
        <div className="trace-controls">
          <button type="button" aria-pressed={cartoon} onClick={() => setCartoon(!cartoon)}>
            Cartoon
          </button>
          <button
            type="button"
            aria-pressed={showContacts}
            onClick={() => setShowContacts(!showContacts)}
          >
            Contacts
          </button>
          <button
            type="button"
            aria-pressed={showInterface}
            onClick={() => setShowInterface(!showInterface)}
          >
            Interface
          </button>
          <button type="button" aria-pressed={spinning} onClick={() => setSpinning(!spinning)}>
            Spin
          </button>
          <button
            type="button"
            onClick={() => {
              viewRef.current = { yaw: 0.7, pitch: 0.35, zoom: 1 };
              draw();
            }}
          >
            Reset
          </button>
        </div>
      ) : null}
      <canvas
        ref={canvasRef}
        width={width}
        height={height}
        aria-label={`Alpha-carbon trace of ${model.id}. Drag to rotate.`}
        onPointerDown={(event) => {
          event.currentTarget.setPointerCapture(event.pointerId);
          dragRef.current = {
            x: event.clientX,
            y: event.clientY,
            yaw: viewRef.current.yaw,
            pitch: viewRef.current.pitch,
          };
        }}
        onPointerMove={(event) => {
          const drag = dragRef.current;
          if (!drag) {
            return;
          }
          viewRef.current.yaw = drag.yaw + (event.clientX - drag.x) * 0.01;
          viewRef.current.pitch = drag.pitch + (event.clientY - drag.y) * 0.01;
          draw();
        }}
        onPointerUp={() => {
          dragRef.current = null;
        }}
        onWheel={(event) => {
          event.preventDefault();
          const next = viewRef.current.zoom * (event.deltaY < 0 ? 1.08 : 1 / 1.08);
          viewRef.current.zoom = Math.min(Math.max(next, 0.5), 4);
          draw();
        }}
      />
      <p className="trace-legend">
        <span className="trace-swatch trace-swatch-target" /> Chain {model.target_chain}, target
        <span className="trace-swatch trace-swatch-binder" /> Chain {model.binder_chain}, binder
        <span className="trace-swatch trace-swatch-contact" /> Contact at{" "}
        {model.contacts.cutoff_angstrom} Å
      </p>
      <p className="trace-status">{status}</p>
      {mismatch ? <p role="alert">{mismatch}</p> : null}
      {contacts ? (
        <p className="trace-status">
          Computed here from the served PDB: {contacts.pairs.length} residue pairs,{" "}
          {contacts.targetResidues.length} target and {contacts.binderResidues.length} binder residues
          in contact, minimum{" "}
          {contacts.minimumDistance === null ? "none" : contacts.minimumDistance.toFixed(3)} Å.{" "}
          {contactsRecorded
            ? disagreements.length === 0
              ? "Every value agrees with the recorded review."
              : `Disagrees with the recorded review on: ${disagreements.map((item) => item.field).join(", ")}.`
            : "The review does not record these contact counts. The counts above are calculated from the served PDB."}
        </p>
      ) : null}
      {share ? (
        <p className="trace-status">
          Geometric secondary structure of the binder, approximated from CA positions: helix{" "}
          {share.helix}%, sheet {share.sheet}%, loop {share.loop}%. This is not DSSP and not the
          BindCraft column.
        </p>
      ) : null}
    </div>
  );
}
