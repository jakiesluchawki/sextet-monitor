import type { Geometry } from "geojson";

export type Category = "earthquake" | "disaster" | "weather" | "aviation" | "cyber" | "internet";
export type TimeBasis = "occurred" | "changed" | "published" | "validity";
export type SourceState = "pending" | "ok" | "ok_empty" | "partial" | "error" | "stale" | "needs_credentials" | "disabled";
export interface SourceStatus {
  id: string; name: string; status: SourceState; enabled: boolean; requires_key: boolean;
  last_attempt_at: string | null; last_success_at: string | null; newest_content_at: string | null;
  next_due_at: string | null; record_count: number; error: string | null; poll_interval_seconds: number;
  coverage: string | Record<string, unknown>; license_name: string; license_url: string | null; attribution: string;
}
export interface EventSummary {
  id: string; kind: string; category: Category; title: string; description: string;
  occurred_start: string | null; occurred_end: string | null; issued_at: string | null;
  source_updated_at: string | null; first_seen_at: string; last_seen_at: string; last_changed_at: string | null;
  valid_from: string | null; valid_to: string | null; countries: string[]; geometry: Geometry | null;
  location_precision: string; time_precision: string; severity: number; severity_label: string;
  severity_reason: string; original_severity: unknown; lifecycle_status: "active" | "expired" | "withdrawn" | "unknown";
  verification_status: string; anomaly_score: null; source_ids: string[]; source_count: number;
  independent_source_count: number; source_url: string | null; tags: string[]; change_type: string;
}
export interface Evidence {
  id: string; source_id: string; source_name: string; provider_record_id: string; source_url: string | null;
  retrieved_at: string; issued_at: string | null; source_updated_at: string | null;
  origins: string[]; payload_hash: string; raw: Record<string, unknown> | null; raw_retained?: boolean; source_snapshot_at?: string | null; attribution: string; license_url: string | null;
}
export interface EventDetail extends EventSummary {
  evidence: Evidence[];
  revisions: Array<{id: string; recorded_at: string; change_type: string; summary: string}>;
  relations: Array<{event_id: string; title: string; relation_type: string; reason: string; distance_km: number | null; time_delta_hours: number | null}>;
}
export interface EventQuery {
  window_hours: number; time_basis: TimeBasis; country?: string; region?: "europe"; category?: Category;
  severity_min: number; min_sources: number; lat?: number; lon?: number; radius_km?: number;
  include_inactive: boolean; limit: number; since?: string; until?: string;
}
export interface EventsResponse {
  items: EventSummary[]; total: number; shown: number; mapped: number; unlocated: number; truncated: boolean;
  query: EventQuery; source_health: SourceStatus[]; generated_at: string; limitations?: string[];
}
export interface SourcesResponse {items: SourceStatus[]; generated_at: string}
export interface Fact {text: string; event_id: string; source_urls: string[]}
export interface QueryResponse {
  supported: boolean; answer: string; interpretation: Record<string, unknown> | null;
  events: EventSummary[]; facts: Fact[]; inferences: string[]; limitations: string[];
  source_health: SourceStatus[]; generated_at: string; query_explanation?: string;
}
export interface BriefingResponse {
  id: string; answer: string; since: string; until: string; generated_at: string;
  sections: Array<{title: string; items: Array<{event_id: string; text: string}>}>;
  facts: Fact[]; inferences?: string[]; limitations: string[]; source_health: SourceStatus[];
  scope?: {window_hours: number; country?: string | null}; first_briefing?: boolean;
  total?: number; shown?: number; processed_count?: number | null; omitted_fact_count?: number | null; truncated?: boolean;
}
export interface HealthResponse {status: string; version: string; database: string; ai_mode: "off"; timezone: "Europe/Warsaw"}
