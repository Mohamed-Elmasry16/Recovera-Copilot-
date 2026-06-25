export type SourceMode = 'live' | 'partial' | 'demo';

export type Kpis = {
  totalRecords: number;
  totalRevenue: number;
  totalProfit: number;
  totalOrders: number;
  leakageOrders: number;
  revenueAtRisk: number;
  leakageRate: number;
  criticalOrders: number;
  avgQueryLatency: number;
  cacheHitRate: number;
  vectorDocs: number;
};

export type TopRegion = {
  label: string;
  value: number;
  orders: number;
};

export type MonthlyPoint = {
  month: string;
  revenue: number;
  leakage: number;
  leakageRate: number;
  orders: number;
};

export type ScenarioPoint = {
  scenario: string;
  revenueAtRisk: number;
  orders: number;
  confidence: number;
};

export type SellerRisk = {
  sellerId: string;
  sellerName?: string;
  city: string;
  state: string;
  leakageRate: number;
  revenueAtRisk: number;
  riskTier: string;
};

export type AiQuery = {
  query: string;
  latencyMs: number;
  confidence: number;
  hallucinationFlag: boolean;
  route: string;
  createdAt: string;
};

export type MarketingPoint = {
  channel: string;
  spend: number;
  revenue: number;
  profit: number;
  roas: number;
  sessions: number;
  campaigns: number;
};

export type MarketingCampaignPoint = {
  campaignId: string;
  campaign: string;
  channel: string;
  spend: number;
  revenue: number;
  profit: number;
  roas: number;
  orders: number;
};

export type SystemSignal = {
  name: string;
  status: 'healthy' | 'warning' | 'offline';
  value: string;
  hint: string;
};

export type DashboardPayload = {
  source: SourceMode;
  generatedAt: string;
  kpis: Kpis;
  topRegions: TopRegion[];
  monthly: MonthlyPoint[];
  scenarios: ScenarioPoint[];
  sellerRisk: SellerRisk[];
  recentQueries: AiQuery[];
  marketing: MarketingPoint[];
  marketingCampaigns: MarketingCampaignPoint[];
  system: SystemSignal[];
  warnings: string[];
};

export type ChatApiResponse = {
  session_id: string;
  answer: string;
  sql_used?: string | null;
  row_count?: number;
  execution_ms?: number;
  error?: string | null;
  steps?: string[];
  intent?: string;
  route?: string;
  difficulty?: string;
  rag_retrieved?: number;
  rag_cached?: boolean;
  confidence?: number;
  chart_data?: unknown;
};
