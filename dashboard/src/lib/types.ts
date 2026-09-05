/** Mirrors the FastAPI response shapes in app/api/routes.py. */

export type CaseState =
  | "NEW"
  | "PROCESSING"
  | "AWAITING_OUTCOME"
  | "RECOVERED"
  | "LOST"
  | "ESCALATED_TO_HUMAN"
  | "STOPPED_UNRECOVERABLE";

export interface CaseSummary {
  id: number;
  display_ref: string;
  flow_type: string;
  state: CaseState;
  amount_paise: number;
  order_id: string | null;
  subscription_id: string | null;
  attempts_count: number;
  messages_sent_count: number;
  related_case_ids: number[];
  terminal_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  case_ref: string;
  actor: string;
  summary: string;
  before_state: string | null;
  after_state: string | null;
  timestamp: string;
}

export interface CaseDetail extends CaseSummary {
  policy_snapshot: Record<string, unknown>;
  audit_trail: Array<{
    actor: string;
    summary: string;
    before_state: string | null;
    after_state: string | null;
    timestamp: string;
  }>;
  diagnoses: Array<{
    root_cause_category: string;
    confidence: number;
    method: string;
    reasoning: string;
  }>;
  decisions: Array<{
    proposed_action: string;
    action_params: Record<string, unknown>;
    message_language: string | null;
    message_tone: string | null;
    reasoning: string;
    status: string;
  }>;
  actions: Array<{
    action_type: string;
    status: string;
    actor: string;
    external_ref: string | null;
    executed_at: string | null;
  }>;
  guardrail_checks: Array<{ passed: boolean; violated_rules: string[] }>;
  outcomes: Array<{
    outcome_type: string;
    amount_recovered_paise: number | null;
    recovered_at: string | null;
    matched_payment_id: string | null;
    detail: string | null;
    late_recovery_after_ttl: boolean;
  }>;
  can_approve: boolean;
}

export interface MetricsSummary {
  cases_total: number;
  recovered: number;
  lost: number;
  escalated: number;
  stopped_unrecoverable: number;
  pending: number;
  total_recovered_paise: number;
  recovery_rate: number;
  mean_time_to_recovery_hours: number;
  resolved_via_mandate_retry_pct: number;
  resolved_via_payment_link_pct: number;
  escalated_pct: number;
  false_positive_rate: number;
  guardrail_violations: number;
  guardrail_blocks: number;
  diagnosis_method_split: { rule: number; llm: number; fallback: number };
  computed_at: string;
  at_risk_paise: number;
  late_recovery_after_ttl: number;
  late_recovery_note: string;
}

export interface GuardrailLogEntry {
  id: number;
  case_ref: string;
  proposed_action: string;
  passed: boolean;
  violated_rules: string[];
  created_at: string;
}

export type AuditLogResponse = { total: number; entries: AuditEntry[] };
export type ExceptionsResponse = {
  total: number;
  exceptions: Array<CaseSummary & { state_tag: string }>;
};
export type PolicyResponse = {
  current: Record<string, unknown>;
  open_case_snapshots: Record<string, number>;
};
