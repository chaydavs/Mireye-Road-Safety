export type Driver = {
  label: string;
  field: string;
  value: string | number;
  source: string;
  source_url: string;
  fetched_at: string | null;
  contribution: number;
};

export type Rsl = {
  estimated: boolean;
  basis: string;
  low: number | null;
  high: number | null;
  last_treated: number | null;
  text: string;
};

export type SegmentProps = {
  segment_id: number;
  route_name: string;
  score: number;
  grade: string;
  rank_pct: number;
  color: string;
  bucket: string;
  mireye_share: number;
  mireye_field_count: number;
  decision_weights: Record<string, number>;
  drivers: Driver[];
  rsl: Rsl;
};

export type Summary = {
  segments: number;
  mireye_contribution: { median: number; min: number; max: number };
  mireye_by_fieldcount_median: number;
  non_mireye_median: { vdot_traffic: number; local_records: number };
  top_mireye_fields: { field: string; in_top_drivers: number }[];
};

export type Trigger = {
  type: string;
  detail: string;
  source?: string;
  source_url?: string;
  at?: string;
};

export type Live = {
  available: boolean;
  generated_at?: string;
  calm?: boolean;
  active_alerts?: number;
  alert_names?: string[];
  elevated_gages?: number;
  gage_ids?: string[];
  wet_week?: boolean;
  watched_segments?: number;
  watched?: number[];
  triggers?: Record<string, Trigger[]>;
};
