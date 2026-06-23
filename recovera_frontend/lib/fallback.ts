import type { DashboardPayload } from './types';

export const fallbackDashboard: DashboardPayload = {
  source: 'demo',
  generatedAt: new Date().toISOString(),
  warnings: ['Database connection is not configured yet. Showing design-safe demo data.'],
  kpis: {
    totalRecords: 2900000,
    totalRevenue: 842900000,
    totalProfit: 168400000,
    totalOrders: 343300,
    leakageOrders: 23682,
    revenueAtRisk: 58200000,
    leakageRate: 6.9,
    criticalOrders: 0,
    avgQueryLatency: 47,
    cacheHitRate: 62,
    vectorDocs: 260000
  },
  topRegions: [
    { label: 'Nasr City', value: 34036871.75, orders: 11842 },
    { label: 'New Cairo', value: 29642010.4, orders: 10544 },
    { label: '6th October', value: 25710889.2, orders: 9321 },
    { label: 'Maadi', value: 21944872.1, orders: 8019 },
    { label: 'Heliopolis', value: 18300211.9, orders: 7430 },
    { label: 'Alexandria', value: 17400990.7, orders: 6922 },
    { label: 'Mansoura', value: 15111042.5, orders: 6127 },
    { label: 'Tanta', value: 12294761.6, orders: 5193 }
  ],
  monthly: [
    { month: 'Jan', revenue: 52200000, leakage: 3110000, leakageRate: 5.96, orders: 22800 },
    { month: 'Feb', revenue: 54850000, leakage: 3350000, leakageRate: 6.11, orders: 24080 },
    { month: 'Mar', revenue: 58290000, leakage: 3910000, leakageRate: 6.71, orders: 25610 },
    { month: 'Apr', revenue: 60740000, leakage: 4100000, leakageRate: 6.75, orders: 26430 },
    { month: 'May', revenue: 64820000, leakage: 4640000, leakageRate: 7.16, orders: 27820 },
    { month: 'Jun', revenue: 68100000, leakage: 5020000, leakageRate: 7.37, orders: 29240 },
    { month: 'Jul', revenue: 71480000, leakage: 5480000, leakageRate: 7.67, orders: 30790 },
    { month: 'Aug', revenue: 73250000, leakage: 5720000, leakageRate: 7.81, orders: 31810 }
  ],
  scenarios: [
    { scenario: 'Negative margin', revenueAtRisk: 13800000, orders: 3120, confidence: 0.91 },
    { scenario: 'Duplicate refund', revenueAtRisk: 10600000, orders: 1780, confidence: 0.94 },
    { scenario: 'Never shipped', revenueAtRisk: 8700000, orders: 1421, confidence: 0.89 },
    { scenario: 'Seller paid twice', revenueAtRisk: 7600000, orders: 925, confidence: 0.87 },
    { scenario: 'Payment dispute', revenueAtRisk: 5900000, orders: 812, confidence: 0.84 },
    { scenario: 'Freight leakage', revenueAtRisk: 4100000, orders: 601, confidence: 0.8 }
  ],
  sellerRisk: [
    { sellerId: 'SEL-01842', sellerName: 'Cairo Market Hub', city: 'Cairo', state: 'C', leakageRate: 18.7, revenueAtRisk: 1870000, riskTier: 'Critical' },
    { sellerId: 'SEL-02190', sellerName: 'Giza Trade House', city: 'Giza', state: 'G', leakageRate: 16.1, revenueAtRisk: 1540000, riskTier: 'Critical' },
    { sellerId: 'SEL-00451', sellerName: 'Alexandria Fulfillment', city: 'Alexandria', state: 'A', leakageRate: 14.6, revenueAtRisk: 1320000, riskTier: 'High' },
    { sellerId: 'SEL-01988', sellerName: 'Delta Seller Group', city: 'Mansoura', state: 'D', leakageRate: 12.4, revenueAtRisk: 1180000, riskTier: 'High' },
    { sellerId: 'SEL-01033', sellerName: 'Tanta Commerce Co.', city: 'Tanta', state: 'G', leakageRate: 10.9, revenueAtRisk: 940000, riskTier: 'High' }
  ],
  recentQueries: [
    { query: 'Top leakage scenarios this month', latencyMs: 42, confidence: 0.93, hallucinationFlag: false, route: 'text-to-sql', createdAt: new Date().toISOString() },
    { query: 'Which sellers have the highest leakage rate?', latencyMs: 51, confidence: 0.89, hallucinationFlag: false, route: 'rag+sql', createdAt: new Date(Date.now() - 8 * 60_000).toISOString() },
    { query: 'Orders with duplicate refunds', latencyMs: 38, confidence: 0.91, hallucinationFlag: false, route: 'sql', createdAt: new Date(Date.now() - 24 * 60_000).toISOString() }
  ],
  marketing: [
    { channel: 'Paid Search', spend: 4820000, revenue: 25400000, profit: 7112000, roas: 5.27, sessions: 412000, campaigns: 4 },
    { channel: 'Social', spend: 3780000, revenue: 14100000, profit: 3384000, roas: 3.73, sessions: 365000, campaigns: 5 },
    { channel: 'Email', spend: 710000, revenue: 8900000, profit: 3026000, roas: 12.54, sessions: 154000, campaigns: 3 },
    { channel: 'Affiliate', spend: 1600000, revenue: 9300000, profit: 2604000, roas: 5.81, sessions: 122000, campaigns: 2 },
    { channel: 'Organic', spend: 420000, revenue: 11200000, profit: 3472000, roas: 26.67, sessions: 211000, campaigns: 3 }
  ],
  marketingCampaigns: [
    { campaignId: 'PS-Brand-01', campaign: 'Brand search protection', channel: 'Paid Search', spend: 1180000, revenue: 8200000, profit: 2460000, roas: 6.95, orders: 9300 },
    { campaignId: 'PS-Generic-02', campaign: 'Generic category search', channel: 'Paid Search', spend: 1550000, revenue: 7600000, profit: 1900000, roas: 4.90, orders: 7800 },
    { campaignId: 'PS-Retarget-03', campaign: 'Search retargeting', channel: 'Paid Search', spend: 920000, revenue: 5100000, profit: 1428000, roas: 5.54, orders: 5200 },
    { campaignId: 'PS-Seasonal-04', campaign: 'Seasonal search push', channel: 'Paid Search', spend: 1170000, revenue: 4500000, profit: 1323000, roas: 3.85, orders: 4100 },
    { campaignId: 'SOC-TikTok-01', campaign: 'TikTok flash deals', channel: 'Social', spend: 980000, revenue: 4100000, profit: 984000, roas: 4.18, orders: 5300 },
    { campaignId: 'SOC-Meta-02', campaign: 'Meta prospecting', channel: 'Social', spend: 1120000, revenue: 3900000, profit: 858000, roas: 3.48, orders: 4900 },
    { campaignId: 'EM-Winback-01', campaign: 'Win-back email flow', channel: 'Email', spend: 180000, revenue: 3400000, profit: 1224000, roas: 18.89, orders: 3600 },
    { campaignId: 'EM-CrossSell-02', campaign: 'Cross-sell automation', channel: 'Email', spend: 230000, revenue: 2950000, profit: 1032500, roas: 12.83, orders: 3100 },
    { campaignId: 'AFF-Partner-01', campaign: 'Top affiliate partners', channel: 'Affiliate', spend: 920000, revenue: 5900000, profit: 1652000, roas: 6.41, orders: 5200 },
    { campaignId: 'ORG-SEO-01', campaign: 'SEO product clusters', channel: 'Organic', spend: 160000, revenue: 5200000, profit: 1716000, roas: 32.50, orders: 5700 }
  ],
  system: [
    { name: 'PostgreSQL 17', status: 'healthy', value: '<50ms', hint: 'Materialized view query latency' },
    { name: 'pgvector ANN', status: 'healthy', value: '~10ms', hint: 'IVFFlat cosine retrieval' },
    { name: 'Jina embeddings', status: 'healthy', value: '1024d', hint: 'Normalized vector space' },
    { name: 'SQL guard', status: 'healthy', value: 'Block', hint: 'Injection prevention enabled' },
    { name: 'RAG cache', status: 'warning', value: '62%', hint: 'Can improve with TTL tuning' }
  ]
};
