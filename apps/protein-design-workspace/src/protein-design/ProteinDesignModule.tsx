import { useState } from "react";
import { DesignPlan } from "./DesignPlan";
import { DesignRequest, type SelectedDesignRequest } from "./DesignRequest";
import { DesignResults } from "./DesignResults";

/** Design request and its plan stay on one page. Results only reads linked attempts. */

export function ProteinDesignModule() {
  const [selection, setSelection] = useState<SelectedDesignRequest | null>(null);
  const [view, setView] = useState<"design" | "results">(initialView);
  function choose(next: SelectedDesignRequest | null) {
    setSelection(next);
    writeLocation(view, next?.requestId ?? null);
  }
  function open(next: "design" | "results") {
    setView(next);
    writeLocation(next, selection?.requestId ?? null);
  }
  return (
    <div className="protein-design-module">
      <div className="protein-design-tabs" role="tablist" aria-label="Protein Design">
        <button type="button" role="tab" aria-selected={view === "design"} onClick={() => open("design")}>
          Design
        </button>
        <button type="button" role="tab" aria-selected={view === "results"} onClick={() => open("results")}>
          Results
        </button>
      </div>
      <div className="protein-design" hidden={view !== "design"}>
        <DesignRequest onSelection={choose} />
        <DesignPlan selection={selection} />
      </div>
      {view === "results" ? <DesignResults selection={selection} /> : null}
    </div>
  );
}

function initialView(): "design" | "results" {
  return new URLSearchParams(window.location.search).get("panel") === "results" ? "results" : "design";
}

function writeLocation(view: "design" | "results", requestId: string | null) {
  const params = new URLSearchParams(window.location.search);
  if (view === "results") {
    params.set("panel", "results");
  } else {
    params.delete("panel");
  }
  if (requestId) {
    params.set("request", requestId);
  } else {
    params.delete("request");
  }
  const query = params.toString();
  window.history.replaceState({}, "", query ? `/?${query}` : "/");
}
