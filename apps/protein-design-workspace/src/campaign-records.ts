export type CampaignRecord = {
  campaign_id: string;
  name: string;
  scientific_objective: string;
  notes: string;
  created_at: string;
  updated_at: string;
  design_request_ids: string[];
};

export type CampaignDraft = {
  name: string;
  scientific_objective: string;
  notes: string;
};

function isCampaign(value: unknown): value is CampaignRecord {
  if (!value || typeof value !== "object") {
    return false;
  }
  const item = value as Record<string, unknown>;
  return (
    typeof item.campaign_id === "string" &&
    typeof item.name === "string" &&
    typeof item.scientific_objective === "string" &&
    typeof item.notes === "string" &&
    typeof item.created_at === "string" &&
    typeof item.updated_at === "string" &&
    Array.isArray(item.design_request_ids) &&
    item.design_request_ids.every((entry) => typeof entry === "string")
  );
}

export async function listCampaigns(): Promise<CampaignRecord[]> {
  const response = await fetch("/api/campaigns");
  if (!response.ok) {
    throw new Error("campaigns_unavailable");
  }
  const body = (await response.json()) as { campaigns?: unknown };
  if (!body || !Array.isArray(body.campaigns) || !body.campaigns.every(isCampaign)) {
    throw new Error("campaigns_unavailable");
  }
  return body.campaigns;
}

export async function createCampaign(draft: CampaignDraft): Promise<CampaignRecord> {
  const response = await fetch("/api/campaigns", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(draft),
  });
  if (!response.ok) {
    throw new Error("campaign_rejected");
  }
  const body: unknown = await response.json();
  if (!isCampaign(body)) {
    throw new Error("campaign_rejected");
  }
  return body;
}

export async function linkDesignRequest(campaignId: string, requestId: string): Promise<void> {
  const response = await fetch(`/api/campaigns/${encodeURIComponent(campaignId)}/design-requests`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ request_id: requestId }),
  });
  if (!response.ok) {
    throw new Error("campaign_link_rejected");
  }
}

export async function readCampaign(campaignId: string): Promise<CampaignRecord> {
  const response = await fetch(`/api/campaigns/${encodeURIComponent(campaignId)}`);
  if (!response.ok) {
    throw new Error("campaign_absent");
  }
  const body: unknown = await response.json();
  if (!isCampaign(body)) {
    throw new Error("campaign_absent");
  }
  return body;
}
