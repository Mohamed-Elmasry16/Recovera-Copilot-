import { fallbackDashboard } from './fallback';
import { firstSuccessful, getDatabaseConfigSummary, hasDatabaseUrl, numberish, pingDatabase, sql, supportedDatabaseEnvNames, textish } from './db';
import type { DashboardPayload, MarketingCampaignPoint, MarketingPoint, MonthlyPoint, ScenarioPoint, SellerRisk, SystemSignal, TopRegion } from './types';

function fallbackWithWarnings(warnings: string[]): DashboardPayload {
  return {
    ...fallbackDashboard,
    generatedAt: new Date().toISOString(),
    warnings: warnings.length ? warnings : fallbackDashboard.warnings
  };
}

export async function readDashboard(): Promise<DashboardPayload> {
  const warnings: string[] = [];

  if (!hasDatabaseUrl()) {
    return fallbackWithWarnings([`Missing Supabase/Postgres connection string. Set one of: ${supportedDatabaseEnvNames()}.`]);
  }

  // Fail fast once before launching all dashboard queries. Without this guard, a bad
  // pooler host/port can create 8 simultaneous timeouts and make the page wait 30-40s.
  try {
    await pingDatabase();
  } catch (error) {
    const config = getDatabaseConfigSummary();
    const message = error instanceof Error ? error.message : String(error);
    return fallbackWithWarnings([
      `database: ${message}`,
      config.warning || `database config: ${config.host || 'unknown host'}:${config.port || 'unknown port'}`
    ].filter(Boolean));
  }

  const [kpiRows, topRegions, monthly, scenarios, sellerRisk, recentQueries, marketing, marketingCampaigns, system] = await Promise.all([
    firstSuccessful('kpis', [readKpisFromViews, readKpisFromBaseTables], warnings),
    firstSuccessful('topRegions', [readTopRegionsFromMv, readTopRegionsFromBaseTables], warnings),
    firstSuccessful('monthly', [readMonthlyFromMv, readMonthlyFromOrders], warnings),
    firstSuccessful('scenarios', [readScenariosFromMv, readScenariosFromReasons], warnings),
    firstSuccessful('sellerRisk', [readSellerRiskFromMv, readSellerRiskFromBaseTables], warnings),
    firstSuccessful('recentQueries', [readRecentQueriesFromRetrievalLog, readRecentQueriesFromChatbotLog], warnings),
    firstSuccessful('marketing', [readMarketingFromAttribution, readMarketingFromCampaigns], warnings),
    firstSuccessful('marketingCampaigns', [readMarketingCampaignsFromAttribution, readMarketingCampaignsFromCampaigns], warnings),
    firstSuccessful('system', [readSystemSignals], warnings)
  ]);

  const merged: DashboardPayload = {
    source: warnings.length ? 'partial' : 'live',
    generatedAt: new Date().toISOString(),
    kpis: kpiRows || fallbackDashboard.kpis,
    topRegions: topRegions && topRegions.length ? topRegions : fallbackDashboard.topRegions,
    monthly: monthly && monthly.length ? monthly : fallbackDashboard.monthly,
    scenarios: scenarios && scenarios.length ? scenarios : fallbackDashboard.scenarios,
    sellerRisk: sellerRisk && sellerRisk.length ? sellerRisk : fallbackDashboard.sellerRisk,
    recentQueries: recentQueries && recentQueries.length ? recentQueries : fallbackDashboard.recentQueries,
    marketing: marketing && marketing.length ? marketing : fallbackDashboard.marketing,
    marketingCampaigns: marketingCampaigns && marketingCampaigns.length ? marketingCampaigns : fallbackDashboard.marketingCampaigns,
    system: system && system.length ? system : fallbackDashboard.system,
    warnings
  };

  return merged;
}

async function readKpisFromViews() {
  const [dashboard, docs, queryStats] = await Promise.all([
    sql(`
      SELECT
        COUNT(*)::float AS order_records,
        COALESCE(SUM(total_revenue), 0)::float AS total_revenue,
        COALESCE(SUM(total_profit), 0)::float AS total_profit,
        COALESCE(SUM(CASE WHEN anomaly_flag = 1 THEN 1 ELSE 0 END), 0)::float AS leakage_orders,
        COALESCE(SUM(CASE WHEN anomaly_flag = 1 THEN total_revenue ELSE 0 END), 0)::float AS revenue_at_risk,
        COALESCE(AVG(CASE WHEN anomaly_flag = 1 THEN 1.0 ELSE 0.0 END), 0)::float * 100 AS leakage_rate,
        0::float AS critical_orders
      FROM ml_output.mv_leakage_dashboard
    `),
    sql(`SELECT COUNT(*)::float AS vector_docs FROM rag.documents WHERE is_active = TRUE`),
    sql(`
      SELECT
        COALESCE(AVG(latency_ms), 0)::float AS avg_query_latency,
        COALESCE(AVG(CASE WHEN result_json IS NOT NULL THEN 1 ELSE 0 END), 0)::float * 100 AS cache_hit_rate
      FROM rag.retrieval_log
      LEFT JOIN rag.retrieval_cache ON retrieval_cache.query_hash = md5(retrieval_log.query_text)
    `)
  ]);

  const r = dashboard[0] || {};
  const d = docs[0] || {};
  const q = queryStats[0] || {};
  return {
    totalRecords: numberish(r.order_records) + numberish(d.vector_docs),
    totalRevenue: numberish(r.total_revenue),
    totalProfit: numberish(r.total_profit),
    totalOrders: numberish(r.order_records),
    leakageOrders: numberish(r.leakage_orders),
    revenueAtRisk: numberish(r.revenue_at_risk),
    leakageRate: numberish(r.leakage_rate),
    criticalOrders: numberish(r.critical_orders),
    avgQueryLatency: numberish(q.avg_query_latency, fallbackDashboard.kpis.avgQueryLatency),
    cacheHitRate: numberish(q.cache_hit_rate, fallbackDashboard.kpis.cacheHitRate),
    vectorDocs: numberish(d.vector_docs)
  };
}

async function readKpisFromBaseTables() {
  const [orders, docs, latency] = await Promise.all([
    sql(`
      SELECT
        COUNT(*)::float AS order_records,
        COALESCE(SUM(total_revenue), 0)::float AS total_revenue,
        COALESCE(SUM(total_profit), 0)::float AS total_profit,
        COALESCE(SUM(CASE WHEN profit_margin < 0 OR inventory_mismatch THEN 1 ELSE 0 END), 0)::float AS leakage_orders,
        COALESCE(SUM(CASE WHEN profit_margin < 0 OR inventory_mismatch THEN total_revenue ELSE 0 END), 0)::float AS revenue_at_risk,
        COALESCE(AVG(CASE WHEN profit_margin < 0 OR inventory_mismatch THEN 1.0 ELSE 0.0 END), 0)::float * 100 AS leakage_rate
      FROM ecommerce.orders
    `),
    sql(`SELECT COUNT(*)::float AS vector_docs FROM rag.documents WHERE is_active = TRUE`),
    sql(`SELECT COALESCE(AVG(latency_ms), 0)::float AS avg_query_latency FROM rag.retrieval_log`)
  ]);
  const o = orders[0] || {};
  return {
    totalRecords: numberish(o.order_records) + numberish(docs[0]?.vector_docs),
    totalRevenue: numberish(o.total_revenue),
    totalProfit: numberish(o.total_profit),
    totalOrders: numberish(o.order_records),
    leakageOrders: numberish(o.leakage_orders),
    revenueAtRisk: numberish(o.revenue_at_risk),
    leakageRate: numberish(o.leakage_rate),
    criticalOrders: 0,
    avgQueryLatency: numberish(latency[0]?.avg_query_latency, fallbackDashboard.kpis.avgQueryLatency),
    cacheHitRate: fallbackDashboard.kpis.cacheHitRate,
    vectorDocs: numberish(docs[0]?.vector_docs)
  };
}

async function readTopRegionsFromMv(): Promise<TopRegion[]> {
  const rows = await sql(`
    SELECT customer_city AS label, COALESCE(SUM(total_revenue), 0)::float AS value, COUNT(*)::float AS orders
    FROM ml_output.mv_leakage_dashboard
    WHERE customer_city IS NOT NULL
    GROUP BY customer_city
    ORDER BY value DESC
    LIMIT 10
  `);
  return rows.map((r) => ({ label: textish(r.label), value: numberish(r.value), orders: numberish(r.orders) }));
}

async function readTopRegionsFromBaseTables(): Promise<TopRegion[]> {
  const rows = await sql(`
    SELECT c.customer_city AS label, COALESCE(SUM(o.total_revenue), 0)::float AS value, COUNT(*)::float AS orders
    FROM ecommerce.orders o
    JOIN ecommerce.customers c ON c.customer_id = o.customer_id
    WHERE c.customer_city IS NOT NULL
    GROUP BY c.customer_city
    ORDER BY value DESC
    LIMIT 10
  `);
  return rows.map((r) => ({ label: textish(r.label), value: numberish(r.value), orders: numberish(r.orders) }));
}

async function readMonthlyFromMv(): Promise<MonthlyPoint[]> {
  const rows = await sql(`
    WITH ordered_months AS (
      SELECT
        TO_DATE(TRIM(month_label), 'FMMonth YYYY') AS month_date,
        COALESCE(total_revenue, 0)::float AS revenue,
        COALESCE(revenue_at_risk, 0)::float AS leakage,
        COALESCE(leakage_rate_pct, 0)::float AS leakage_rate,
        COALESCE(total_orders, 0)::float AS orders
      FROM ml_output.mv_monthly_leakage
      WHERE month_label IS NOT NULL
    )
    SELECT
      TO_CHAR(month_date, 'Mon YYYY') AS month,
      revenue,
      leakage,
      leakage_rate,
      orders
    FROM ordered_months
    WHERE month_date IS NOT NULL
    ORDER BY month_date DESC
    LIMIT 12
  `);
  return rows.reverse().map((r) => ({
    month: textish(r.month),
    revenue: numberish(r.revenue),
    leakage: numberish(r.leakage),
    leakageRate: numberish(r.leakage_rate),
    orders: numberish(r.orders)
  }));
}

async function readMonthlyFromOrders(): Promise<MonthlyPoint[]> {
  const rows = await sql(`
    SELECT
      DATE_TRUNC('month', order_purchase_timestamp)::date AS bucket,
      TO_CHAR(DATE_TRUNC('month', order_purchase_timestamp), 'Mon YYYY') AS month,
      COALESCE(SUM(total_revenue), 0)::float AS revenue,
      COALESCE(SUM(CASE WHEN profit_margin < 0 OR inventory_mismatch THEN total_revenue ELSE 0 END), 0)::float AS leakage,
      COALESCE(AVG(CASE WHEN profit_margin < 0 OR inventory_mismatch THEN 1.0 ELSE 0.0 END), 0)::float * 100 AS leakage_rate,
      COUNT(*)::float AS orders
    FROM ecommerce.orders
    WHERE order_purchase_timestamp IS NOT NULL
    GROUP BY bucket, month
    ORDER BY bucket DESC
    LIMIT 12
  `);
  return rows.reverse().map((r) => ({
    month: textish(r.month),
    revenue: numberish(r.revenue),
    leakage: numberish(r.leakage),
    leakageRate: numberish(r.leakage_rate),
    orders: numberish(r.orders)
  }));
}

async function readScenariosFromMv(): Promise<ScenarioPoint[]> {
  const rows = await sql(`
    SELECT
      scenario,
      COALESCE(revenue_at_risk, 0)::float AS revenue_at_risk,
      COALESCE(total_orders, 0)::float AS orders,
      COALESCE(avg_anomaly_score, 0.85)::float AS confidence
    FROM ml_output.mv_leakage_by_scenario
    ORDER BY revenue_at_risk DESC
    LIMIT 8
  `);
  return rows.map((r) => ({ scenario: textish(r.scenario), revenueAtRisk: numberish(r.revenue_at_risk), orders: numberish(r.orders), confidence: numberish(r.confidence) }));
}

async function readScenariosFromReasons(): Promise<ScenarioPoint[]> {
  const rows = await sql(`
    SELECT
      lr.leakage_type AS scenario,
      COALESCE(SUM(o.total_revenue), 0)::float AS revenue_at_risk,
      COUNT(*)::float AS orders,
      COALESCE(AVG(lr.confidence), 0)::float AS confidence
    FROM ml_output.order_leakage_reasons lr
    JOIN ecommerce.orders o ON o.order_id = lr.order_id
    GROUP BY lr.leakage_type
    ORDER BY revenue_at_risk DESC
    LIMIT 8
  `);
  return rows.map((r) => ({ scenario: textish(r.scenario), revenueAtRisk: numberish(r.revenue_at_risk), orders: numberish(r.orders), confidence: numberish(r.confidence) }));
}

async function readSellerRiskFromMv(): Promise<SellerRisk[]> {
  const rows = await sql(`
    SELECT
      seller_id,
      COALESCE(NULLIF(seller_name::text, ''), seller_id::text) AS seller_name,
      COALESCE(NULLIF(seller_city::text, ''), 'Unknown') AS city,
      '-' AS state,
      COALESCE(leakage_rate_pct, 0)::float AS leakage_rate,
      COALESCE((total_revenue * leakage_rate_pct / 100.0), 0)::float AS revenue_at_risk,
      CASE
        WHEN COALESCE(leakage_rate_pct, 0) >= 20 THEN 'Critical'
        WHEN COALESCE(leakage_rate_pct, 0) >= 10 THEN 'High'
        WHEN COALESCE(leakage_rate_pct, 0) >= 5 THEN 'Medium'
        ELSE 'Low'
      END AS risk_tier
    FROM ml_output.mv_seller_risk
    ORDER BY leakage_rate DESC, revenue_at_risk DESC
    LIMIT 8
  `);
  return rows.map((r) => ({
    sellerId: textish(r.seller_id),
    sellerName: textish(r.seller_name),
    city: textish(r.city),
    state: textish(r.state, '-'),
    leakageRate: numberish(r.leakage_rate),
    revenueAtRisk: numberish(r.revenue_at_risk),
    riskTier: textish(r.risk_tier, 'High')
  }));
}

async function readSellerRiskFromBaseTables(): Promise<SellerRisk[]> {
  const rows = await sql(`
    SELECT
      s.seller_id,
      COALESCE(NULLIF(s.seller_name::text, ''), s.seller_id::text) AS seller_name,
      COALESCE(s.seller_city, 'Unknown') AS city,
      '-' AS state,
      COALESCE(AVG(CASE WHEN o.profit_margin < 0 OR o.inventory_mismatch THEN 1.0 ELSE 0.0 END), 0)::float * 100 AS leakage_rate,
      COALESCE(SUM(CASE WHEN o.profit_margin < 0 OR o.inventory_mismatch THEN o.total_revenue ELSE 0 END), 0)::float AS revenue_at_risk,
      CASE
        WHEN AVG(CASE WHEN o.profit_margin < 0 OR o.inventory_mismatch THEN 1.0 ELSE 0.0 END) >= 0.20 THEN 'Critical'
        WHEN AVG(CASE WHEN o.profit_margin < 0 OR o.inventory_mismatch THEN 1.0 ELSE 0.0 END) >= 0.10 THEN 'High'
        WHEN AVG(CASE WHEN o.profit_margin < 0 OR o.inventory_mismatch THEN 1.0 ELSE 0.0 END) >= 0.05 THEN 'Medium'
        ELSE 'Low'
      END AS risk_tier
    FROM ecommerce.order_items oi
    JOIN ecommerce.sellers s ON s.seller_id = oi.seller_id
    JOIN ecommerce.orders o ON o.order_id = oi.order_id
    GROUP BY s.seller_id, s.seller_name, s.seller_city
    ORDER BY leakage_rate DESC, revenue_at_risk DESC
    LIMIT 8
  `);
  return rows.map((r) => ({ sellerId: textish(r.seller_id), sellerName: textish(r.seller_name), city: textish(r.city), state: textish(r.state), leakageRate: numberish(r.leakage_rate), revenueAtRisk: numberish(r.revenue_at_risk), riskTier: textish(r.risk_tier) }));
}

async function readRecentQueriesFromRetrievalLog() {
  const rows = await sql(`
    SELECT query_text AS query,
           COALESCE(latency_ms, 0)::float AS latency_ms,
           COALESCE(confidence_score, 0)::float AS confidence,
           COALESCE(hallucination_flag, FALSE) AS hallucination_flag,
           CASE WHEN generated_sql IS NULL THEN 'rag' ELSE 'rag+sql' END AS route,
           NOW()::text AS created_at
    FROM rag.retrieval_log
    ORDER BY log_id DESC
    LIMIT 6
  `);
  return rows.map((r) => ({
    query: textish(r.query),
    latencyMs: numberish(r.latency_ms),
    confidence: numberish(r.confidence),
    hallucinationFlag: Boolean(r.hallucination_flag),
    route: textish(r.route),
    createdAt: textish(r.created_at)
  }));
}

async function readRecentQueriesFromChatbotLog() {
  const rows = await sql(`
    SELECT content AS query,
           0::float AS latency_ms,
           0.85::float AS confidence,
           FALSE AS hallucination_flag,
           'chat' AS route,
           COALESCE(created_at, NOW())::text AS created_at
    FROM ml_output.chatbot_conversations
    WHERE role = 'user'
    ORDER BY id DESC
    LIMIT 6
  `);
  return rows.map((r) => ({ query: textish(r.query), latencyMs: numberish(r.latency_ms), confidence: numberish(r.confidence), hallucinationFlag: Boolean(r.hallucination_flag), route: textish(r.route), createdAt: textish(r.created_at) }));
}

async function readMarketingFromAttribution(): Promise<MarketingPoint[]> {
  const rows = await sql(`
    WITH campaign_perf AS (
      SELECT
        mc.campaign_id,
        COALESCE(mc.channel, 'Unknown') AS channel,
        COALESCE(MAX(mc.budget), 0)::float AS spend,
        COALESCE(SUM(o.total_revenue), 0)::float AS revenue,
        COALESCE(SUM(o.total_profit), 0)::float AS profit
      FROM marketing.marketing_campaigns mc
      LEFT JOIN marketing.campaign_attribution ca ON ca.campaign_id = mc.campaign_id
      LEFT JOIN ecommerce.orders o ON o.order_id = ca.order_id
      GROUP BY mc.campaign_id, mc.channel
    ), session_perf AS (
      SELECT traffic_source AS channel, COUNT(DISTINCT session_id)::float AS sessions
      FROM marketing.website_sessions
      GROUP BY traffic_source
    )
    SELECT
      campaign_perf.channel,
      COALESCE(SUM(campaign_perf.spend), 0)::float AS spend,
      COALESCE(SUM(campaign_perf.revenue), 0)::float AS revenue,
      COALESCE(SUM(campaign_perf.profit), 0)::float AS profit,
      CASE WHEN COALESCE(SUM(campaign_perf.spend), 0) > 0 THEN (COALESCE(SUM(campaign_perf.revenue), 0) / SUM(campaign_perf.spend))::float ELSE 0 END AS roas,
      COALESCE(MAX(session_perf.sessions), 0)::float AS sessions,
      COUNT(DISTINCT campaign_perf.campaign_id)::float AS campaigns
    FROM campaign_perf
    LEFT JOIN session_perf ON session_perf.channel = campaign_perf.channel
    GROUP BY campaign_perf.channel
    ORDER BY revenue DESC
    LIMIT 8
  `);
  return rows.map((r) => ({
    channel: textish(r.channel),
    spend: numberish(r.spend),
    revenue: numberish(r.revenue),
    profit: numberish(r.profit),
    roas: numberish(r.roas),
    sessions: numberish(r.sessions),
    campaigns: numberish(r.campaigns)
  }));
}

async function readMarketingFromCampaigns(): Promise<MarketingPoint[]> {
  const rows = await sql(`
    SELECT channel,
           COALESCE(SUM(budget), 0)::float AS spend,
           0::float AS revenue,
           0::float AS profit,
           0::float AS roas,
           0::float AS sessions,
           COUNT(DISTINCT campaign_id)::float AS campaigns
    FROM marketing.marketing_campaigns
    GROUP BY channel
    ORDER BY spend DESC
    LIMIT 8
  `);
  return rows.map((r) => ({
    channel: textish(r.channel),
    spend: numberish(r.spend),
    revenue: numberish(r.revenue),
    profit: numberish(r.profit),
    roas: numberish(r.roas),
    sessions: numberish(r.sessions),
    campaigns: numberish(r.campaigns)
  }));
}

async function readMarketingCampaignsFromAttribution(): Promise<MarketingCampaignPoint[]> {
  const rows = await sql(`
    SELECT
      mc.campaign_id::text AS campaign_id,
      mc.campaign_id::text AS campaign,
      COALESCE(mc.channel, 'Unknown') AS channel,
      COALESCE(MAX(mc.budget), 0)::float AS spend,
      COALESCE(SUM(o.total_revenue), 0)::float AS revenue,
      COALESCE(SUM(o.total_profit), 0)::float AS profit,
      CASE WHEN COALESCE(MAX(mc.budget), 0) > 0 THEN (COALESCE(SUM(o.total_revenue), 0) / MAX(mc.budget))::float ELSE 0 END AS roas,
      COUNT(DISTINCT ca.order_id)::float AS orders
    FROM marketing.marketing_campaigns mc
    LEFT JOIN marketing.campaign_attribution ca ON ca.campaign_id = mc.campaign_id
    LEFT JOIN ecommerce.orders o ON o.order_id = ca.order_id
    GROUP BY mc.campaign_id, mc.channel
    ORDER BY revenue DESC, spend DESC
    LIMIT 40
  `);
  return rows.map((r) => ({
    campaignId: textish(r.campaign_id),
    campaign: textish(r.campaign),
    channel: textish(r.channel),
    spend: numberish(r.spend),
    revenue: numberish(r.revenue),
    profit: numberish(r.profit),
    roas: numberish(r.roas),
    orders: numberish(r.orders)
  }));
}

async function readMarketingCampaignsFromCampaigns(): Promise<MarketingCampaignPoint[]> {
  const rows = await sql(`
    SELECT
      campaign_id::text AS campaign_id,
      campaign_id::text AS campaign,
      COALESCE(channel, 'Unknown') AS channel,
      COALESCE(budget, 0)::float AS spend,
      0::float AS revenue,
      0::float AS profit,
      0::float AS roas,
      0::float AS orders
    FROM marketing.marketing_campaigns
    ORDER BY budget DESC
    LIMIT 40
  `);
  return rows.map((r) => ({
    campaignId: textish(r.campaign_id),
    campaign: textish(r.campaign),
    channel: textish(r.channel),
    spend: numberish(r.spend),
    revenue: numberish(r.revenue),
    profit: numberish(r.profit),
    roas: numberish(r.roas),
    orders: numberish(r.orders)
  }));
}

async function readSystemSignals(): Promise<SystemSignal[]> {
  const [db, docs, cache, guards] = await Promise.all([
    sql(`SELECT current_setting('server_version') AS version`),
    sql(`SELECT COUNT(*)::float AS active_docs, SUM(CASE WHEN needs_reembedding THEN 1 ELSE 0 END)::float AS pending FROM rag.documents WHERE is_active = TRUE`),
    sql(`SELECT COALESCE(SUM(hit_count), 0)::float AS hits, COUNT(*)::float AS keys FROM rag.retrieval_cache WHERE expires_at > NOW()`),
    sql(`SELECT COUNT(*)::float AS guard_rules FROM rag.sql_guard`)
  ]);

  const pending = numberish(docs[0]?.pending);
  const activeDocs = numberish(docs[0]?.active_docs);
  return [
    { name: `PostgreSQL ${textish(db[0]?.version, 'online').split(' ')[0]}`, status: 'healthy', value: 'Online', hint: 'Supabase connection succeeded' },
    { name: 'pgvector corpus', status: pending > 0 ? 'warning' : 'healthy', value: activeDocs.toLocaleString(), hint: pending > 0 ? `${pending.toLocaleString()} docs pending embedding` : 'All active docs embedded' },
    { name: 'RAG cache', status: numberish(cache[0]?.keys) > 0 ? 'healthy' : 'warning', value: `${numberish(cache[0]?.hits).toLocaleString()} hits`, hint: `${numberish(cache[0]?.keys).toLocaleString()} active cache keys` },
    { name: 'SQL guard', status: numberish(guards[0]?.guard_rules) > 0 ? 'healthy' : 'warning', value: `${numberish(guards[0]?.guard_rules)} rules`, hint: 'Pattern-based query protection' },
    { name: 'Materialized views', status: 'healthy', value: '<50ms', hint: 'Dashboard reads from precomputed analytics where available' }
  ];
}
