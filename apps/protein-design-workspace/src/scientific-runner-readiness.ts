export const READINESS_PATH = "/api/scientific-runner-readiness";

export type ReadinessStatus =
  | "disabled"
  | "blocked"
  | "ready"
  | "configuration_failure"
  | "adapter_failure";

export type ReadinessCheck = {
  code: string;
  status: "passed" | "blocked";
  text: string;
};

export type ReadinessReply = {
  status: ReadinessStatus;
  text: string;
  session_id: string;
  profile_id: string | null;
  tool_name: string | null;
  tool_version: string | null;
  runtime_name: string | null;
  runtime_version: string | null;
  image_digest: string | null;
  target_scope: string | null;
  manifest_digest: string | null;
  observed_at: string;
  checks: ReadinessCheck[];
  execution_authorized: false;
  scientific_execution: false;
  scientific_validation: false;
};

const STATUSES = new Set<ReadinessStatus>([
  "disabled",
  "blocked",
  "ready",
  "configuration_failure",
  "adapter_failure",
]);

const CHECK_CODES = new Set([
  "argv_template",
  "entrypoint",
  "execution_profile",
  "expected_inputs",
  "expected_outputs",
  "generator_version_not_recorded",
  "image_identity",
  "invalid_manifest_type",
  "manifest_invalid",
  "manifest_not_canonical",
  "manifest_too_large",
  "manifest_unreadable",
  "output_limit",
  "readiness_adapter_failed",
  "readiness_probe",
  "readiness_probe_failed",
  "readiness_probe_malformed",
  "readiness_probe_mismatch",
  "readiness_probe_not_recorded",
  "readiness_probe_rejected",
  "readiness_probe_timeout",
  "resource_envelope",
  "runner_disabled",
  "runtime_identity",
  "scientific_runner_manifest_unreadable",
  "target_execution_blocked",
  "target_scope",
  "timeout_limit",
  "tool_identity",
  "tool_version",
  "unsupported_profile",
  "unsupported_schema",
]);

const READY_CODES = [
  "argv_template",
  "entrypoint",
  "execution_profile",
  "expected_inputs",
  "expected_outputs",
  "image_identity",
  "output_limit",
  "readiness_probe",
  "resource_envelope",
  "runner_disabled",
  "runtime_identity",
  "target_scope",
  "timeout_limit",
  "tool_identity",
  "tool_version",
] as const;

const PROFILES: Record<string, readonly [string, string]> = {
  "pdl1-bindcraft-historical-v1": ["bindcraft", "PDL1"],
  "pdl1-bindcraft-docker-v1": ["bindcraft", "PDL1"],
  "cd22-mber-v1": ["mber", "CD22"],
  "taci-mber-v1": ["mber", "TACI"],
};

const TOKEN = /^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$/;
const REJECTED_VERSIONS = new Set(["latest", "current", "unknown"]);

const FIELDS = [
  "status",
  "text",
  "session_id",
  "profile_id",
  "tool_name",
  "tool_version",
  "runtime_name",
  "runtime_version",
  "image_digest",
  "target_scope",
  "manifest_digest",
  "observed_at",
  "checks",
  "execution_authorized",
  "scientific_execution",
  "scientific_validation",
] as const;

const CANONICAL_UTC =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{6}))?\+00:00$/;

export function observationInstant(value: string): number | null {
  const match = CANONICAL_UTC.exec(value);
  if (!match || match[7] === "000000") {
    return null;
  }
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6]);
  const micros = match[7] ? Number(match[7]) : 0;
  const instant = Date.UTC(year, month - 1, day, hour, minute, second, Math.floor(micros / 1000));
  const parsed = new Date(instant);
  if (
    parsed.getUTCFullYear() !== year ||
    parsed.getUTCMonth() !== month - 1 ||
    parsed.getUTCDate() !== day ||
    parsed.getUTCHours() !== hour ||
    parsed.getUTCMinutes() !== minute ||
    parsed.getUTCSeconds() !== second
  ) {
    return null;
  }
  return instant * 1000 + (micros % 1000);
}

export function selectReadiness(
  current: ReadinessReply | null,
  incoming: ReadinessReply,
  operation: number,
  latestOperation: number,
): ReadinessReply | null {
  if (operation !== latestOperation) {
    return current;
  }
  const incomingInstant = observationInstant(incoming.observed_at);
  if (incomingInstant === null) {
    return current;
  }
  const currentInstant = current ? observationInstant(current.observed_at) : null;
  if (currentInstant !== null && incomingInstant < currentInstant) {
    return current;
  }
  return incoming;
}

export async function readScientificRunnerReadiness(): Promise<ReadinessReply> {
  const response = await fetch(READINESS_PATH, {
    method: "GET",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error("readiness_rejected");
  }
  const payload: unknown = await response.json();
  if (!isReadinessReply(payload)) {
    throw new Error("readiness_rejected");
  }
  return payload;
}

function isReadinessReply(value: unknown): value is ReadinessReply {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  if (Object.keys(record).sort().join() !== [...FIELDS].sort().join()) {
    return false;
  }
  if (typeof record.status !== "string" || !STATUSES.has(record.status as ReadinessStatus)) {
    return false;
  }
  if (!safeCopy(record.text) || !token(record.session_id)) {
    return false;
  }
  if (typeof record.observed_at !== "string" || observationInstant(record.observed_at) === null) {
    return false;
  }
  if (
    record.execution_authorized !== false ||
    record.scientific_execution !== false ||
    record.scientific_validation !== false
  ) {
    return false;
  }
  if (!identityOk(record.profile_id, record.tool_name, record.target_scope)) {
    return false;
  }
  if (
    !versionOk(record.tool_version) ||
    !versionOk(record.runtime_name) ||
    !versionOk(record.runtime_version)
  ) {
    return false;
  }
  if (!optionalDigest(record.image_digest, /^sha256:[0-9a-f]{64}$/)) {
    return false;
  }
  if (!optionalDigest(record.manifest_digest, /^[0-9a-f]{64}$/)) {
    return false;
  }
  if (!checksMatch(record.status, record.checks)) {
    return false;
  }
  if (record.status !== "ready") {
    return true;
  }
  return (
    (record.profile_id === "pdl1-bindcraft-historical-v1" ||
      record.profile_id === "pdl1-bindcraft-docker-v1") &&
    record.tool_name === "bindcraft" &&
    record.target_scope === "PDL1" &&
    requiredVersion(record.tool_version) &&
    requiredVersion(record.runtime_name) &&
    requiredVersion(record.runtime_version) &&
    record.image_digest !== null &&
    record.manifest_digest !== null
  );
}

function checksMatch(status: string, checks: unknown): checks is ReadinessCheck[] {
  if (!Array.isArray(checks) || checks.length === 0 || !checks.every(isCheck)) {
    return false;
  }
  const seen = new Set<string>();
  let blocked = 0;
  for (const check of checks) {
    if (seen.has(check.code) || !CHECK_CODES.has(check.code)) {
      return false;
    }
    seen.add(check.code);
    if (check.status === "blocked") {
      blocked += 1;
    }
  }
  if (status === "ready") {
    return blocked === 0 && seen.size === READY_CODES.length && READY_CODES.every((code) => seen.has(code));
  }
  if (status === "blocked") {
    return blocked > 0;
  }
  if (status === "disabled") {
    return checks.some((check) => check.code === "runner_disabled" && check.status === "blocked");
  }
  return blocked === checks.length;
}

function isCheck(value: unknown): value is ReadinessCheck {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return false;
  }
  const record = value as Record<string, unknown>;
  return (
    Object.keys(record).sort().join() === "code,status,text" &&
    typeof record.code === "string" &&
    CHECK_CODES.has(record.code) &&
    (record.status === "passed" || record.status === "blocked") &&
    safeCopy(record.text)
  );
}

function identityOk(profile: unknown, tool: unknown, target: unknown): boolean {
  if (profile !== null && (typeof profile !== "string" || !(profile in PROFILES))) {
    return false;
  }
  if (tool !== null && tool !== "bindcraft" && tool !== "mber") {
    return false;
  }
  if (target !== null && target !== "PDL1" && target !== "CD22" && target !== "TACI") {
    return false;
  }
  if (typeof profile === "string") {
    const [expectedTool, expectedTarget] = PROFILES[profile];
    if ((tool !== null && tool !== expectedTool) || (target !== null && target !== expectedTarget)) {
      return false;
    }
  }
  if (tool === "bindcraft" && target !== null && target !== "PDL1") {
    return false;
  }
  if (tool === "mber" && target === "PDL1") {
    return false;
  }
  if (target === "CD22" && tool !== null && tool !== "mber") {
    return false;
  }
  if (target === "TACI" && tool !== null && tool !== "mber") {
    return false;
  }
  if (target === "PDL1" && tool !== null && tool !== "bindcraft") {
    return false;
  }
  if (tool === "mber" && target === "CD22" && profile !== null && profile !== "cd22-mber-v1") {
    return false;
  }
  return !(tool === "mber" && target === "TACI" && profile !== null && profile !== "taci-mber-v1");
}

function versionOk(value: unknown): boolean {
  return value === null || requiredVersion(value);
}

function requiredVersion(value: unknown): boolean {
  return (
    typeof value === "string" &&
    TOKEN.test(value) &&
    !REJECTED_VERSIONS.has(value.toLowerCase()) &&
    safeCopy(value)
  );
}

function optionalDigest(value: unknown, pattern: RegExp): boolean {
  return value === null || (typeof value === "string" && pattern.test(value));
}

function token(value: unknown): value is string {
  return typeof value === "string" && TOKEN.test(value) && safeCopy(value);
}

function safeCopy(value: unknown): value is string {
  return (
    typeof value === "string" &&
    value.length > 0 &&
    value.length <= 500 &&
    !/[\u0000-\u001F\u007F\\/`]/.test(value) &&
    !/traceback|token=|sqlite|select\s/i.test(value)
  );
}
