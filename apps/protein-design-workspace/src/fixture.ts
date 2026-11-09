import sessionFixture from "./session-fixture.json";
import type { WorkspaceSession } from "./contracts";

/** Canonical display fixture. Parity is checked against Python `WorkspaceSession.to_dict()`. */
export const prototypeWorkspace = sessionFixture as WorkspaceSession;
