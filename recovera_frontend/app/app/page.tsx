'use client';

import { FormEvent, KeyboardEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fallbackDashboard } from '@/lib/fallback';
import type { DashboardPayload, SellerRisk } from '@/lib/types';
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  PolarAngleAxis,
  PolarGrid,
  Radar,
  RadarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';

type PageKey = 'overview' | 'risk' | 'rag' | 'ai' | 'marketing' | 'copilot';
type ThemeMode = 'light' | 'dark';
type LanguageMode = 'en' | 'ar';
type DashboardLoadState = 'loading' | 'ready' | 'error';

let dashboardInitialLoadStarted = false;

type NavItem = {
  id: PageKey;
  label: string;
  icon: IconName;
};

type IconName = 'grid' | 'warning' | 'database' | 'pulse' | 'trend' | 'sparkle' | 'refresh' | 'filter' | 'pin' | 'share' | 'bell' | 'moon' | 'sun' | 'search' | 'bot' | 'mic' | 'send' | 'plus' | 'expand' | 'type' | 'pen' | 'chat' | 'radio' | 'bolt' | 'volume' | 'stop';

type CopilotChartData = {
  type?: string;
  title?: string;
  labels?: string[];
  datasets?: Array<{ label?: string; data?: number[] }>;
  values?: number[];
  data?: number[];
  rows?: Array<Record<string, unknown>>;
};

type NormalizedCopilotChart = {
  title: string;
  chartType: 'bar' | 'bar_vertical' | 'line' | 'area' | 'pie';
  labels: string[];
  datasets: Array<{ label: string; key: string; data: number[]; tone: 'primary' | 'success' | 'accent' }>;
};

type ChatMessage = {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  createdAt: number;
  isError?: boolean;
  sql?: string | null;
  rowCount?: number;
  executionMs?: number;
  steps?: string[];
  showSteps?: boolean;
  showSql?: boolean;
  intent?: string;
  route?: string;
  difficulty?: string;
  confidence?: number;
  chartData?: CopilotChartData | null;
};

type CopilotApiPayload = Record<string, unknown>;

type AnalysisCardType = 'finding' | 'impact' | 'evidence' | 'recommendation';

type AnalysisCard = {
  type: AnalysisCardType;
  content: string;
};

const navItems: NavItem[] = [
  { id: 'overview', label: 'Overview', icon: 'grid' },
  { id: 'risk', label: 'Risk', icon: 'warning' },
  { id: 'rag', label: 'Vector RAG', icon: 'database' },
  { id: 'ai', label: 'AI Monitor', icon: 'pulse' },
  { id: 'marketing', label: 'Marketing', icon: 'trend' },
  { id: 'copilot', label: 'Copilot', icon: 'sparkle' }
];


const uiCopy: Record<LanguageMode, {
  nav: Record<PageKey, string>;
  refresh: string;
  toggleTheme: string;
  light: string;
  dark: string;
  language: string;
  brandTagline: string;
  data: string;
  synced: string;
  connecting: string;
  neural: string;
  records: string;
}> = {
  en: {
    nav: {
      overview: 'Overview',
      risk: 'Risk',
      rag: 'Vector RAG',
      ai: 'AI Monitor',
      marketing: 'Marketing',
      copilot: 'Copilot'
    },
    refresh: 'Refresh',
    toggleTheme: 'Toggle theme',
    light: 'Light',
    dark: 'Dark',
    language: 'العربية',
    brandTagline: 'recover · revenue · growth',
    data: 'DATA',
    synced: 'SYNCED',
    connecting: 'CONNECTING',
    neural: 'NEURAL',
    records: 'RECORDS'
  },
  ar: {
    nav: {
      overview: 'نظرة عامة',
      risk: 'المخاطر',
      rag: 'البحث الذكي',
      ai: 'مراقبة الذكاء',
      marketing: 'التسويق',
      copilot: 'المساعد'
    },
    refresh: 'تحديث',
    toggleTheme: 'تبديل النمط',
    light: 'فاتح',
    dark: 'داكن',
    language: 'EN',
    brandTagline: 'استرداد · إيرادات · نمو',
    data: 'البيانات',
    synced: 'آخر تحديث',
    connecting: 'جاري الاتصال',
    neural: 'النظام الذكي',
    records: 'السجلات'
  }
};

const kpiSeries = {
  risk: [58, 61, 60, 56, 52, 50, 54, 60, 65],
  recovered: [28, 35, 38, 37, 31, 29, 32, 40, 47],
  leakage: [32, 39, 42, 41, 35, 34, 39, 45, 51],
  sellers: [42, 50, 54, 53, 47, 44, 48, 56, 64]
};

const monthly = [
  { month: 'Jan', leakage: 620, recovered: 170, orders: 8200, disputes: 115 },
  { month: 'Feb', leakage: 700, recovered: 215, orders: 8750, disputes: 128 },
  { month: 'Mar', leakage: 640, recovered: 260, orders: 9180, disputes: 119 },
  { month: 'Apr', leakage: 805, recovered: 310, orders: 9360, disputes: 104 },
  { month: 'May', leakage: 760, recovered: 330, orders: 9340, disputes: 92 },
  { month: 'Jun', leakage: 890, recovered: 420, orders: 9070, disputes: 87 },
  { month: 'Jul', leakage: 870, recovered: 455, orders: 8600, disputes: 79 },
  { month: 'Aug', leakage: 930, recovered: 505, orders: 8250, disputes: 76 },
  { month: 'Sep', leakage: 1020, recovered: 600, orders: 8180, disputes: 83 },
  { month: 'Oct', leakage: 970, recovered: 645, orders: 8500, disputes: 96 },
  { month: 'Nov', leakage: 1110, recovered: 720, orders: 9250, disputes: 108 },
  { month: 'Dec', leakage: 1240, recovered: 810, orders: 9690, disputes: 123 }
];

const scenarioMix = [
  { name: 'Pricing', value: 32, color: 'var(--purple)' },
  { name: 'Fraud', value: 24, color: 'var(--cyan)' },
  { name: 'Returns', value: 18, color: 'var(--green)' },
  { name: 'Discount', value: 14, color: 'var(--magenta)' },
  { name: 'Logistics', value: 12, color: 'var(--yellow)' }
];

const topRegions = [
  { name: 'Nasr City', value: 34 },
  { name: 'New Cairo', value: 29 },
  { name: 'Maadi', value: 22 },
  { name: 'Giza', value: 19 },
  { name: 'Alexandria', value: 16 },
  { name: 'Heliopolis', value: 13 },
  { name: '6 October', value: 10 }
];

const riskRows = [
  { seller: 'Lumiere Global', id: 'S-1043', volume: 'EGP 1.24M', leakage: '8.2%', tier: 'High' },
  { seller: 'Kinetico SA', id: 'S-0921', volume: 'EGP 842K', leakage: '5.1%', tier: 'Medium' },
  { seller: 'Nebula Logistics', id: 'S-0712', volume: 'EGP 1.61M', leakage: '11.4%', tier: 'High' },
  { seller: 'Vertex Trading', id: 'S-1182', volume: 'EGP 489K', leakage: '2.8%', tier: 'Low' },
  { seller: 'Prime Foundry', id: 'S-0588', volume: 'EGP 2.10M', leakage: '6.7%', tier: 'Medium' },
  { seller: 'Aether Systems', id: 'S-1334', volume: 'EGP 733K', leakage: '1.9%', tier: 'Low' }
];

const embeddingTables = [
  { name: 'schema_embeddings', rows: 184, color: 'var(--violet)' },
  { name: 'review_embeddings', rows: 2140, color: 'var(--cyan)' },
  { name: 'business_embeddings', rows: 96, color: 'var(--green)' },
  { name: 'metrics_embeddings', rows: 413, color: 'var(--magenta)' }
];

const radarSignals = [
  { signal: 'Latency', score: 82 },
  { signal: 'Recall', score: 91 },
  { signal: 'Precision', score: 96 },
  { signal: 'Cache', score: 87 },
  { signal: 'Cost', score: 62 },
  { signal: 'Fresh', score: 78 }
];

const similarityRows = [
  { score: '0.961', label: 'mv_seller_risk · leakage_rate_pct', width: 96, tone: 'green' },
  { score: '0.921', label: 'ORDER BY leakage_rate_pct DESC', width: 91, tone: 'green' },
  { score: '0.887', label: 'sellers · return_rate · disputes', width: 87, tone: 'cyan' },
  { score: '0.741', label: 'quarterly aggregates · order_quarter', width: 74, tone: 'yellow' },
  { score: '0.702', label: 'mv_leakage_dashboard · region', width: 70, tone: 'yellow' }
];

const latencyBuckets = [
  { bucket: '<20', count: 150, color: 'var(--purple)' },
  { bucket: '20-50', count: 320, color: 'var(--purple)' },
  { bucket: '50-80', count: 120, color: 'var(--purple)' },
  { bucket: '80-120', count: 55, color: 'var(--purple)' },
  { bucket: '>120', count: 14, color: 'var(--yellow)' }
];

const pipelineTiming = [
  { name: 'Embed', value: 38, time: '12ms' },
  { name: 'ANN', value: 19, time: '8ms' },
  { name: 'Re-rank', value: 16, time: '8ms' },
  { name: 'SQL', value: 36, time: '22ms' },
  { name: 'LLM', value: 100, time: '62ms' }
];

const aiQueryRows = [
  { q: 'Top leakage regions last quarter', latency: '142ms', conf: '0.94', status: 'OK' },
  { q: 'Why did Nasr City drop 8% in Sep?', latency: '188ms', conf: '0.88', status: 'OK' },
  { q: 'Predict Q1 fraud exposure', latency: '312ms', conf: '0.62', status: 'Flagged' },
  { q: 'ROAS by channel YTD', latency: '121ms', conf: '0.96', status: 'OK' }
];

const roasChannels = [
  { channel: 'Search', roas: 4.2 },
  { channel: 'Social', roas: 2.8 },
  { channel: 'Display', roas: 1.6 },
  { channel: 'Email', roas: 5.1 },
  { channel: 'Affiliate', roas: 3.4 }
];

const neuralActivity = Array.from({ length: 34 }, (_, index) => ({
  tick: index + 1,
  value: [34, 58, 27, 66, 71, 29, 24, 43, 73, 31, 29, 66, 37, 34, 58, 71, 44, 26, 79, 68, 43, 40, 54, 83, 72, 51, 42, 34, 56, 50, 39, 30, 36, 33][index]
}));

const chartPalette = ['var(--purple)', 'var(--cyan)', 'var(--green)', 'var(--magenta)', 'var(--yellow)', 'var(--blue)'];

function formatCompactCurrency(value: number) {
  const safe = Number.isFinite(value) ? value : 0;
  const abs = Math.abs(safe);
  if (abs >= 1_000_000_000) return `EGP ${(safe / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `EGP ${(safe / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `EGP ${(safe / 1_000).toFixed(1)}K`;
  return `EGP ${Math.round(safe).toLocaleString()}`;
}


function formatKpiCurrency(value: number) {
  const safe = Number.isFinite(value) ? value : 0;
  const abs = Math.abs(safe);
  if (abs >= 1_000_000_000) return `EGP ${(safe / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `EGP ${(safe / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `EGP ${(safe / 1_000).toFixed(1)}K`;
  return `EGP ${Math.round(safe).toLocaleString()}`;
}

function formatAxisCurrency(value: number) {
  const safe = Number.isFinite(value) ? value : 0;
  const abs = Math.abs(safe);
  if (abs >= 1_000_000_000) return `${(safe / 1_000_000_000).toFixed(1)}B`;
  if (abs >= 1_000_000) return `${(safe / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(safe / 1_000).toFixed(0)}K`;
  return `${Math.round(safe)}`;
}

function truncateMiddle(value: string, max = 18) {
  const safe = value || 'Campaign';
  if (safe.length <= max) return safe;
  const left = Math.ceil((max - 1) / 2);
  const right = Math.floor((max - 1) / 2);
  return `${safe.slice(0, left)}…${safe.slice(-right)}`;
}

function formatCompactNumber(value: number) {
  const safe = Number.isFinite(value) ? value : 0;
  const abs = Math.abs(safe);
  if (abs >= 1_000_000_000) return `${(safe / 1_000_000_000).toFixed(2)}B`;
  if (abs >= 1_000_000) return `${(safe / 1_000_000).toFixed(2)}M`;
  if (abs >= 1_000) return `${(safe / 1_000).toFixed(1)}K`;
  return Math.round(safe).toLocaleString();
}

function formatPercent(value: number) {
  return `${(Number.isFinite(value) ? value : 0).toFixed(1)}%`;
}

function formatSellerName(row: SellerRisk) {
  const name = String(row.sellerName || '').trim();
  if (name && name !== row.sellerId) return name;
  return truncateMiddle(row.sellerId || 'Unknown seller', 18);
}

function formatSellerLocation(row: SellerRisk) {
  const city = String(row.city || '').trim();
  const state = String(row.state || '').trim();
  if (city && state && state !== '-') return `${city}, ${state}`;
  return city || state || 'Unknown';
}

function makeSparkline(values: number[], fallback: number[]) {
  const finite = values.filter((value) => Number.isFinite(value));
  const max = Math.max(...finite, 0);
  if (finite.length < 2 || max <= 0) return fallback;
  return finite.map((value) => Math.max(4, Math.round((value / max) * 100)));
}

function toMillions(value: number) {
  return Number(((Number.isFinite(value) ? value : 0) / 1_000_000).toFixed(2));
}

function formatMonthTick(value: string) {
  const text = String(value || '').trim();
  const parts = text.split(/\s+/);
  if (parts.length >= 2) {
    const month = parts[0].slice(0, 3);
    const year = parts[1].slice(-2);
    return `${month} '${year}`;
  }
  return text;
}

export default function Page() {
  const [activePage, setActivePage] = useState<PageKey>('overview');
  const [theme, setTheme] = useState<ThemeMode>('dark');
  const [language, setLanguage] = useState<LanguageMode>('en');
  const [dashboard, setDashboard] = useState<DashboardPayload>(fallbackDashboard);
  const [dashboardStatus, setDashboardStatus] = useState<DashboardLoadState>('loading');
  const [dashboardError, setDashboardError] = useState<string | null>(null);

  const loadDashboard = useCallback(async () => {
    setDashboardStatus('loading');
    setDashboardError(null);
    try {
      const response = await fetch(`/api/dashboard?ts=${Date.now()}`, { cache: 'no-store' });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload?.error || 'Dashboard API returned an error');
      }
      setDashboard(payload as DashboardPayload);
      setDashboardStatus('ready');
    } catch (error) {
      setDashboard(fallbackDashboard);
      setDashboardError(error instanceof Error ? error.message : 'Dashboard API is unavailable');
      setDashboardStatus('error');
    }
  }, []);

  useEffect(() => {
    const savedTheme = window.localStorage.getItem('recovera-theme') as ThemeMode | null;
    if (savedTheme === 'light' || savedTheme === 'dark') setTheme(savedTheme);

    const savedLanguage = window.localStorage.getItem('recovera-language') as LanguageMode | null;
    if (savedLanguage === 'en' || savedLanguage === 'ar') {
      setLanguage(savedLanguage);
    } else if (navigator.language?.toLowerCase().startsWith('ar')) {
      setLanguage('ar');
    }
  }, []);

  useEffect(() => {
    // Next.js dev mode can mount client components twice. Guard the initial
    // dashboard fetch to avoid duplicate database connection attempts.
    if (dashboardInitialLoadStarted) return;
    dashboardInitialLoadStarted = true;
    loadDashboard();
  }, [loadDashboard]);

  useEffect(() => {
    window.localStorage.setItem('recovera-theme', theme);
  }, [theme]);

  useEffect(() => {
    window.localStorage.setItem('recovera-language', language);
    document.documentElement.lang = language;
    document.documentElement.dir = language === 'ar' ? 'rtl' : 'ltr';
  }, [language]);

  const toggleTheme = () => setTheme((current) => (current === 'light' ? 'dark' : 'light'));
  const toggleLanguage = () => setLanguage((current) => (current === 'ar' ? 'en' : 'ar'));

  return (
    <main className={`recovera-app view-${activePage}`} data-theme={theme} data-locale={language} dir={language === 'ar' ? 'rtl' : 'ltr'} lang={language}>
      <AppHeader theme={theme} language={language} onToggleTheme={toggleTheme} onToggleLanguage={toggleLanguage} onRefresh={loadDashboard} onNavigateHome={() => setActivePage('overview')} />
      <SideNav activePage={activePage} language={language} onNavigate={setActivePage} />
      <SystemTicker dashboard={dashboard} status={dashboardStatus} language={language} />
      <section className="workspace" aria-label="Recovera workspace">
        {activePage === 'overview' && <OverviewPage dashboard={dashboard} status={dashboardStatus} error={dashboardError} />}
        {activePage === 'risk' && <RiskPage dashboard={dashboard} />}
        {activePage === 'rag' && <VectorRagPage dashboard={dashboard} />}
        {activePage === 'ai' && <AiMonitorPage dashboard={dashboard} />}
        {activePage === 'marketing' && <MarketingPage dashboard={dashboard} />}
        {activePage === 'copilot' && <CopilotPage />}
      </section>
      <FloatingTools />
    </main>
  );
}

function AppHeader({ theme, language, onToggleTheme, onToggleLanguage, onRefresh, onNavigateHome }: { theme: ThemeMode; language: LanguageMode; onToggleTheme: () => void; onToggleLanguage: () => void; onRefresh: () => void; onNavigateHome: () => void }) {
  const t = uiCopy[language];
  return (
    <header className="app-header">
      <div className="header-topline">
        <button className="brand-lockup" type="button" onClick={onNavigateHome} aria-label={language === 'ar' ? 'فتح النظرة العامة' : 'Open overview'}>
          <LogoMark />
          <span>
            <strong>Recovera</strong>
            <small>{t.brandTagline}</small>
          </span>
        </button>

        <div className="header-actions" aria-label={language === 'ar' ? 'أوامر المساحة' : 'Workspace actions'}>
          <IconButton icon="refresh" label={t.refresh} onClick={onRefresh} />
          <button className="lang-switch" type="button" onClick={onToggleLanguage} aria-label={language === 'ar' ? 'Switch to English' : 'التبديل إلى العربية'}>
            <span>{t.language}</span>
          </button>
          <button className="theme-switch" type="button" onClick={onToggleTheme} aria-label={t.toggleTheme}>
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            <span>{theme === 'dark' ? t.light : t.dark}</span>
          </button>
          <button className="avatar-button" type="button" aria-label={language === 'ar' ? 'قائمة المستخدم' : 'User menu'}>MA</button>
        </div>
      </div>
    </header>
  );
}

function SideNav({ activePage, language, onNavigate }: { activePage: PageKey; language: LanguageMode; onNavigate: (page: PageKey) => void }) {
  const labels = uiCopy[language].nav;
  return (
    <aside className="side-nav" aria-label={language === 'ar' ? 'التنقل الرئيسي' : 'Primary navigation'}>
      <nav>
        {navItems.map((item) => {
          const label = labels[item.id] || item.label;
          return (
            <button key={item.id} className={activePage === item.id ? 'active' : ''} type="button" onClick={() => onNavigate(item.id)} title={label}>
              <span className="nav-icon"><Icon name={item.icon} /></span>
              <span className="nav-label">{label}</span>
            </button>
          );
        })}
      </nav>
    </aside>
  );
}

function SystemTicker({ dashboard, status, language }: { dashboard: DashboardPayload; status: DashboardLoadState; language: LanguageMode }) {
  const t = uiCopy[language];
  const sourceLabel = status === 'loading' ? t.connecting : dashboard.source.toUpperCase();
  const [syncedAtLabel, setSyncedAtLabel] = useState('--:--');

  useEffect(() => {
    const generatedAt = new Date(dashboard.generatedAt);
    if (Number.isNaN(generatedAt.getTime())) {
      setSyncedAtLabel('--:--');
      return;
    }
    setSyncedAtLabel(generatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
  }, [dashboard.generatedAt]);

  return (
    <div className="system-ticker">
      <span className="stream-status"><i /> {sourceLabel} {t.data} · {t.synced} <span suppressHydrationWarning>{syncedAtLabel}</span></span>
      <span className="system-metrics">
        <b><Icon name="bot" /> {t.neural} · {Math.round(dashboard.kpis.avgQueryLatency)}MS</b>
        <b><Icon name="radio" /> PGVECTOR · {Math.round(dashboard.kpis.cacheHitRate)}%</b>
        <b><Icon name="bolt" /> {t.records} · {formatCompactNumber(dashboard.kpis.totalRecords)}</b>
      </span>
    </div>
  );
}

function OverviewPage({ dashboard, status, error }: { dashboard: DashboardPayload; status: DashboardLoadState; error: string | null }) {
  const monthlyChart = dashboard.monthly.map((row) => ({
    month: row.month,
    revenue: toMillions(row.revenue),
    leakage: toMillions(row.leakage),
    orders: row.orders
  }));
  const regionChart = dashboard.topRegions.map((row) => ({ name: row.label, value: toMillions(row.value), orders: row.orders }));
  const profitMargin = dashboard.kpis.totalRevenue > 0 ? (dashboard.kpis.totalProfit / dashboard.kpis.totalRevenue) * 100 : 0;
  const profitSeries = makeSparkline(dashboard.monthly.map((row) => row.revenue * Math.max(profitMargin / 100, 0.02)), kpiSeries.recovered);
  const leakageOrderSeries = makeSparkline(dashboard.monthly.map((row) => row.orders * Math.max(row.leakageRate, 0) / 100), kpiSeries.risk);
  const scenarioTotal = dashboard.scenarios.reduce((sum, row) => sum + Math.max(row.revenueAtRisk, 0), 0);
  const scenarioItems = dashboard.scenarios.map((row, index) => ({
    name: row.scenario,
    value: scenarioTotal > 0 ? Number(((row.revenueAtRisk / scenarioTotal) * 100).toFixed(1)) : 0,
    amount: row.revenueAtRisk,
    orders: row.orders,
    color: chartPalette[index % chartPalette.length]
  }));
  const systemTiles = dashboard.system.slice(0, 4);

  return (
    <div className="page-grid overview-grid">
      {(status !== 'ready' || dashboard.source !== 'live' || dashboard.warnings.length > 0 || error) && (
        <DashboardNotice dashboard={dashboard} status={status} error={error} />
      )}

      <div className="kpi-strip">
        <MetricTile label="Total revenue" value={formatKpiCurrency(dashboard.kpis.totalRevenue)} delta={dashboard.source === 'live' ? 'Live database' : dashboard.source === 'partial' ? 'Partial database' : 'Demo fallback'} tone="green" series={makeSparkline(dashboard.monthly.map((row) => row.revenue), kpiSeries.recovered)} />
        <MetricTile label="Total profit" value={formatKpiCurrency(dashboard.kpis.totalProfit)} delta={`${formatPercent(profitMargin)} margin`} tone="green" series={profitSeries} />
        <MetricTile label="Total orders" value={formatCompactNumber(dashboard.kpis.totalOrders)} delta="orders in system" tone="blue" series={makeSparkline(dashboard.monthly.map((row) => row.orders), kpiSeries.sellers)} />
        <MetricTile label="Leakage orders" value={formatCompactNumber(dashboard.kpis.leakageOrders)} delta={`${formatPercent(dashboard.kpis.leakageRate)} leakage rate`} tone="yellow" series={leakageOrderSeries} />
        <MetricTile label="Vector docs" value={formatCompactNumber(dashboard.kpis.vectorDocs)} delta={`${Math.round(dashboard.kpis.cacheHitRate)}% cache hit`} tone="green" series={kpiSeries.leakage} />
      </div>

      <GlassCard className="wide-card" title="Monthly Revenue vs Leakage" subtitle="EGP · MILLIONS" pulse>
        <div className="chart-large">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={monthlyChart} margin={{ top: 16, right: 18, left: 0, bottom: 6 }}>
              <defs>
                <linearGradient id="leakageFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="var(--cyan)" stopOpacity={0.34} />
                  <stop offset="100%" stopColor="var(--cyan)" stopOpacity={0.02} />
                </linearGradient>
                <linearGradient id="revenueFill" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stopColor="var(--purple)" stopOpacity={0.34} />
                  <stop offset="100%" stopColor="var(--purple)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis dataKey="month" tickLine={false} axisLine={false} stroke="var(--muted)" fontSize={12} minTickGap={18} tickFormatter={formatMonthTick} />
              <YAxis tickLine={false} axisLine={false} stroke="var(--muted)" fontSize={13} width={58} />
              <Tooltip content={<ChartTooltip suffix="M" />} />
              <Area type="monotone" dataKey="revenue" name="Revenue" stroke="var(--purple)" strokeWidth={3} fill="url(#revenueFill)" />
              <Area type="monotone" dataKey="leakage" name="Leakage" stroke="var(--cyan)" strokeWidth={3} fill="url(#leakageFill)" />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <Legend items={[['Revenue', 'var(--purple)'], ['Leakage', 'var(--cyan)']]} />
      </GlassCard>

      <GlassCard title="Leakage by Scenario" subtitle="SHARE OF TOTAL" pulse>
        <ScenarioDonut items={scenarioItems} />
      </GlassCard>

      <GlassCard className="regions-card" title="Top Regions — Revenue" subtitle="EGP · MILLIONS" pulse>
        <div className="chart-medium region-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={regionChart} margin={{ top: 20, right: 12, left: 4, bottom: 4 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis dataKey="name" tickLine={false} axisLine={false} stroke="var(--muted)" fontSize={13} angle={-14} textAnchor="end" height={46} />
              <YAxis tickLine={false} axisLine={false} stroke="var(--muted)" width={48} fontSize={13} />
              <Tooltip content={<ChartTooltip suffix="M" />} />
              <Bar dataKey="value" radius={[8, 8, 0, 0]}>
                {regionChart.map((_, index) => <Cell key={index} fill={index === 0 ? 'var(--purple)' : 'var(--yellow)'} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>

      <GlassCard className="health-card" title="AI Health" subtitle="RAG PIPELINE SIGNALS" pulse>
        <div className="health-grid">
          {systemTiles.map((signal) => (
            <HealthTile key={signal.name} label={signal.name} value={signal.value} status={signal.status === 'healthy' ? 'healthy' : 'watch'} />
          ))}
        </div>
        <div className="warning-note"><Icon name="warning" /> {dashboard.warnings[0] || 'Live Supabase/Postgres connection is serving the dashboard.'}</div>
      </GlassCard>
    </div>
  );
}

function RiskPage({ dashboard }: { dashboard: DashboardPayload }) {
  const total = Math.max(dashboard.sellerRisk.length, 1);
  const highPct = Math.round((dashboard.sellerRisk.filter((row) => ['critical', 'high'].includes(row.riskTier.toLowerCase())).length / total) * 100);
  const mediumPct = Math.round((dashboard.sellerRisk.filter((row) => row.riskTier.toLowerCase() === 'medium').length / total) * 100);
  const lowPct = Math.max(0, 100 - highPct - mediumPct);

  return (
    <div className="page-grid risk-grid single-row">
      <GlassCard className="risk-board" title="Seller Risk Board" subtitle="LIVE EXPOSURE" pulse>
        <table className="risk-table">
          <thead>
            <tr>
              <th>Seller</th>
              <th>Location</th>
              <th>Revenue at risk</th>
              <th>Leakage</th>
              <th>Tier</th>
            </tr>
          </thead>
          <tbody>
            {dashboard.sellerRisk.map((row) => (
              <tr key={row.sellerId}>
                <td className="seller-name-cell">
                  <strong>{formatSellerName(row)}</strong>
                  {row.sellerName && row.sellerName !== row.sellerId ? <small>{truncateMiddle(row.sellerId, 12)}</small> : null}
                </td>
                <td>{formatSellerLocation(row)}</td>
                <td>{formatCompactCurrency(row.revenueAtRisk)}</td>
                <td>{formatPercent(row.leakageRate)}</td>
                <td><RiskBadge tier={row.riskTier} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </GlassCard>

      <GlassCard title="Tier distribution" subtitle="ACTIVE SELLERS">
        <div className="tier-bars">
          <TierBar label="High/Critical" value={highPct} tone="red" />
          <TierBar label="Medium" value={mediumPct} tone="yellow" />
          <TierBar label="Low" value={lowPct} tone="green" />
        </div>
      </GlassCard>
    </div>
  );
}

function VectorRagPage({ dashboard }: { dashboard: DashboardPayload }) {
  return (
    <div className="page-grid rag-grid">
      <StatTile label="Total vectors" value={formatCompactNumber(dashboard.kpis.vectorDocs)} detail="rag.documents active" />
      <StatTile label="Avg Query" value={`${Math.round(dashboard.kpis.avgQueryLatency)}ms`} detail="retrieval_log average" />
      <StatTile label="Cache hit" value={`${Math.round(dashboard.kpis.cacheHitRate)}%`} detail="retrieval_cache" />
      <StatTile label="Recent queries" value={formatCompactNumber(dashboard.recentQueries.length)} detail="last dashboard sample" />

      <GlassCard className="wide-card" title="Embedding tables" subtitle="RAG.* · ROW COUNT BY TYPE" pulse>
        <div className="chart-large compact-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={embeddingTables} layout="vertical" margin={{ top: 14, right: 24, left: 70, bottom: 8 }}>
              <CartesianGrid horizontal={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis type="number" domain={[0, 2200]} ticks={[0, 550, 1100, 1650, 2200]} axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={13} />
              <YAxis dataKey="name" type="category" width={154} axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={13} />
              <Tooltip content={<ChartTooltip />} />
              <Bar dataKey="rows" radius={[0, 8, 8, 0]}>
                {embeddingTables.map((row) => <Cell key={row.name} fill={row.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>

      <GlassCard title="RAG signal radar" subtitle="PIPELINE HEALTH · 0–100" pulse>
        <div className="chart-large compact-chart radar-wrap">
          <ResponsiveContainer width="100%" height="100%">
            <RadarChart data={radarSignals} outerRadius="74%">
              <PolarGrid stroke="var(--chart-grid)" />
              <PolarAngleAxis dataKey="signal" stroke="var(--muted)" tick={{ fontSize: 12 }} />
              <Radar dataKey="score" stroke="var(--purple)" strokeWidth={2} fill="var(--purple)" fillOpacity={0.34} />
            </RadarChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>

      <GlassCard className="wide-card" title="Cosine similarity — last query" subtitle={'"HIGHEST LEAKAGE SELLERS THIS QUARTER"'} pulse>
        <div className="similarity-list">
          {similarityRows.map((row) => (
            <div className="similarity-row" key={row.label}>
              <span className={`score-chip ${row.tone}`}>{row.score}</span>
              <strong>{row.label}</strong>
              <i><em style={{ width: `${row.width}%` }} /></i>
            </div>
          ))}
        </div>
      </GlassCard>

      <GlassCard title="Cache efficiency" subtitle="RETRIEVAL_CACHE HIT RATE" pulse>
        <RingGauge value={Math.round(dashboard.kpis.cacheHitRate)} />
      </GlassCard>

      <GlassCard className="wide-card" title="Query latency distribution" subtitle="P95 · SUB-50MS TARGET" pulse>
        <div className="chart-small">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={latencyBuckets} margin={{ top: 8, right: 20, left: 0, bottom: 4 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis dataKey="bucket" axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={13} />
              <YAxis axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={13} width={44} />
              <Tooltip content={<ChartTooltip />} />
              <Bar dataKey="count" radius={[8, 8, 0, 0]}>
                {latencyBuckets.map((row) => <Cell key={row.bucket} fill={row.color} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>

      <GlassCard title="Pipeline timing" subtitle="PER STAGE · BOOST MS" pulse>
        <div className="pipeline-list">
          {pipelineTiming.map((row) => (
            <div className="pipeline-row" key={row.name}>
              <span><Icon name="radio" /> {row.name}</span>
              <i><em style={{ width: `${row.value}%` }} /></i>
              <b>{row.time}</b>
            </div>
          ))}
        </div>
      </GlassCard>
    </div>
  );
}

function AiMonitorPage({ dashboard }: { dashboard: DashboardPayload }) {
  const hallucinationCount = dashboard.recentQueries.filter((row) => row.hallucinationFlag).length;

  return (
    <div className="page-grid ai-grid single-row">
      <AiStat label="Retrieval Latency" value={`${Math.round(dashboard.kpis.avgQueryLatency)} ms`} status="HEALTHY" />
      <AiStat label="Cache Hit" value={`${Math.round(dashboard.kpis.cacheHitRate)}%`} status="HEALTHY" />
      <AiStat label="SQL Guard" value={dashboard.system.find((item) => item.name.toLowerCase().includes('guard'))?.value || 'Online'} status="HEALTHY" />
      <AiStat label="Hallucination" value={formatCompactNumber(hallucinationCount)} status={hallucinationCount > 0 ? 'WATCH' : 'CLEAR'} watch={hallucinationCount > 0} />

      <GlassCard className="queries-card" title="Recent RAG queries" subtitle="LAST 6 EVENTS">
        <div className="query-list">
          {dashboard.recentQueries.map((row) => (
            <article className="query-row" key={`${row.createdAt}-${row.query}`}>
              <span className="query-icon"><Icon name="bot" /></span>
              <strong>{row.query}</strong>
              <small>{Math.round(row.latencyMs)}ms</small>
              <small>conf {row.confidence.toFixed(2)}</small>
              <b className={row.hallucinationFlag ? 'flagged' : 'ok'}>{row.hallucinationFlag ? 'Flagged' : row.route}</b>
            </article>
          ))}
        </div>
      </GlassCard>
    </div>
  );
}

function MarketingPage({ dashboard }: { dashboard: DashboardPayload }) {
  const [selectedChannel, setSelectedChannel] = useState('All');

  const marketingChart = useMemo(() => dashboard.marketing.map((row) => ({
    channel: row.channel,
    roas: Number(row.roas.toFixed(2)),
    spend: row.spend,
    revenue: row.revenue,
    profit: row.profit || 0,
    campaigns: row.campaigns || 0
  })), [dashboard.marketing]);

  useEffect(() => {
    if (selectedChannel !== 'All' && !dashboard.marketing.some((row) => row.channel === selectedChannel)) {
      setSelectedChannel('All');
    }
  }, [dashboard.marketing, selectedChannel]);

  const filteredMarketing = selectedChannel === 'All'
    ? dashboard.marketing
    : dashboard.marketing.filter((row) => row.channel === selectedChannel);

  const filteredCampaigns = (dashboard.marketingCampaigns || [])
    .filter((row) => selectedChannel === 'All' || row.channel === selectedChannel)
    .sort((a, b) => b.revenue - a.revenue || b.profit - a.profit || b.spend - a.spend);

  const campaignChart = filteredCampaigns.slice(0, 10).map((row) => ({
    ...row,
    campaignLabel: truncateMiddle(row.campaign || row.campaignId, 18)
  }));

  const totalSpend = filteredMarketing.reduce((sum, row) => sum + row.spend, 0);
  const totalRevenue = filteredMarketing.reduce((sum, row) => sum + row.revenue, 0);
  const totalProfit = filteredMarketing.reduce((sum, row) => sum + (row.profit || 0), 0);
  const blendedRoas = totalSpend > 0 ? totalRevenue / totalSpend : 0;
  const totalCampaigns = selectedChannel === 'All'
    ? (dashboard.marketingCampaigns?.length || filteredMarketing.reduce((sum, row) => sum + (row.campaigns || 0), 0))
    : filteredCampaigns.length;
  const totalOrders = filteredCampaigns.reduce((sum, row) => sum + row.orders, 0);
  const maxCampaignRevenue = Math.max(...filteredCampaigns.map((row) => row.revenue), 1);
  const topCampaign = filteredCampaigns[0];

  return (
    <div className="page-grid marketing-grid">
      <GlassCard className="wide-card marketing-channel-card" title="ROAS by Channel" subtitle={selectedChannel === 'All' ? 'CLICK A CHANNEL TO FILTER' : `FILTERED · ${selectedChannel.toUpperCase()}`}>
        <div className="channel-filter-strip" aria-label="Marketing channel filter">
          <button type="button" className={selectedChannel === 'All' ? 'active' : ''} onClick={() => setSelectedChannel('All')}>All channels</button>
          {marketingChart.map((row) => (
            <button type="button" key={row.channel} className={selectedChannel === row.channel ? 'active' : ''} onClick={() => setSelectedChannel(row.channel)}>
              {row.channel}
            </button>
          ))}
        </div>
        <div className="chart-large compact-chart roas-chart clickable-chart">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={marketingChart} layout="vertical" margin={{ top: 18, right: 22, left: 34, bottom: 8 }}>
              <CartesianGrid horizontal={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis type="number" axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={13} />
              <YAxis dataKey="channel" type="category" axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={13} width={88} />
              <Tooltip content={<ChartTooltip suffix="x" />} />
              <Bar dataKey="roas" radius={[0, 8, 8, 0]} onClick={(data) => data?.channel && setSelectedChannel(data.channel)}>
                {marketingChart.map((row, index) => (
                  <Cell
                    key={row.channel}
                    cursor="pointer"
                    fill={selectedChannel === 'All' || selectedChannel === row.channel ? 'var(--purple)' : 'color-mix(in srgb, var(--purple) 35%, var(--muted))'}
                    fillOpacity={selectedChannel === 'All' || selectedChannel === row.channel ? 1 : 0.45}
                    stroke={selectedChannel === row.channel ? 'var(--cyan)' : 'transparent'}
                    strokeWidth={selectedChannel === row.channel ? 2 : 0}
                  />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </GlassCard>

      <GlassCard title="Attribution Snapshot" subtitle={selectedChannel === 'All' ? 'ALL CHANNELS' : selectedChannel.toUpperCase()}>
        <div className="snapshot-grid marketing-snapshot-grid">
          <SnapshotMetric label="Spend" value={formatCompactCurrency(totalSpend)} />
          <SnapshotMetric label="Revenue" value={formatCompactCurrency(totalRevenue)} />
          <SnapshotMetric label="Profit" value={formatCompactCurrency(totalProfit)} />
          <SnapshotMetric label="Blended ROAS" value={`${blendedRoas.toFixed(2)}x`} />
          <SnapshotMetric label="Campaigns" value={formatCompactNumber(totalCampaigns)} />
          <SnapshotMetric label="Orders" value={formatCompactNumber(totalOrders)} />
        </div>
        {topCampaign && (
          <div className="top-campaign-callout">
            <span>Top campaign</span>
            <strong>{topCampaign.campaign}</strong>
            <small>{formatCompactCurrency(topCampaign.revenue)} revenue · {formatCompactCurrency(topCampaign.profit)} profit</small>
          </div>
        )}
      </GlassCard>

      <GlassCard className="campaign-card" title="Campaign Revenue Ranking" subtitle={selectedChannel === 'All' ? 'TOP CAMPAIGNS ACROSS ALL CHANNELS' : `CAMPAIGNS IN ${selectedChannel.toUpperCase()}`} pulse>
        {campaignChart.length ? (
          <div className="campaign-performance-grid">
            <div className="campaign-chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={campaignChart} layout="vertical" margin={{ top: 8, right: 22, left: 30, bottom: 8 }}>
                  <CartesianGrid horizontal={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
                  <XAxis type="number" axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={12} tickFormatter={(value) => formatAxisCurrency(Number(value))} />
                  <YAxis dataKey="campaignLabel" type="category" axisLine={false} tickLine={false} stroke="var(--muted)" fontSize={12} width={116} />
                  <Tooltip content={<MarketingCurrencyTooltip />} />
                  <Bar name="Revenue" dataKey="revenue" fill="var(--green)" radius={[0, 7, 7, 0]} />
                  <Bar name="Profit" dataKey="profit" fill="var(--cyan)" radius={[0, 7, 7, 0]} />
                  <Bar name="Spend" dataKey="spend" fill="var(--yellow)" radius={[0, 7, 7, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="campaign-ranking-list">
              {filteredCampaigns.slice(0, 8).map((row, index) => (
                <article className="campaign-row" key={`${row.channel}-${row.campaignId}`}>
                  <span className="campaign-rank">#{index + 1}</span>
                  <div className="campaign-main">
                    <strong title={row.campaign}>{row.campaign}</strong>
                    <small>{row.channel} · ROAS {row.roas.toFixed(2)}x · {formatCompactNumber(row.orders)} orders</small>
                    <i><em style={{ width: `${Math.max(4, (row.revenue / maxCampaignRevenue) * 100)}%` }} /></i>
                  </div>
                  <b>{formatCompactCurrency(row.revenue)}</b>
                </article>
              ))}
            </div>
          </div>
        ) : (
          <div className="empty-campaign-state">
            <strong>No campaign rows for this filter.</strong>
            <span>Check marketing.campaign_attribution or choose another channel.</span>
          </div>
        )}
      </GlassCard>
    </div>
  );
}

function CopilotPage() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [sessionId] = useState(() => makeCopilotSessionId());
  const messagesEnd = useRef<HTMLDivElement | null>(null);
  const threadRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    const thread = threadRef.current;
    if (!thread) return;
    const lastMessage = messages[messages.length - 1];
    if (isLoading) {
      messagesEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
      return;
    }
    if (lastMessage?.role === 'assistant') {
      const latest = document.getElementById(`copilot-${lastMessage.id}`);
      latest?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages.length, isLoading]);

  const toggleMessagePanel = (messageId: string, panel: 'showSql' | 'showSteps') => {
    setMessages((current) => current.map((message) => (
      message.id === messageId ? { ...message, [panel]: !message[panel] } : message
    )));
  };

  const copySql = async (sql?: string | null) => {
    if (!sql) return;
    try {
      await navigator.clipboard.writeText(sql);
    } catch {
      // Clipboard can be blocked in some browsers. The SQL stays visible for manual copy.
    }
  };

  const sendMessage = async (text: string) => {
    const cleanText = text.trim();
    if (!cleanText || isLoading) return;

    const userMessage: ChatMessage = { id: `u-${Date.now()}`, role: 'user', content: cleanText, createdAt: Date.now() };
    setInput('');
    setMessages((current) => [...current, userMessage]);
    setIsLoading(true);

    try {
      const response = await fetch('/api/copilot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: cleanText, session_id: sessionId })
      });

      const payload = await readJsonSafely(response);
      if (!response.ok) {
        throw new Error(getApiErrorMessage(payload) || `Copilot API returned HTTP ${response.status}`);
      }

      setMessages((current) => [...current, normalizeCopilotResponse(payload)]);
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `a-${Date.now()}`,
          role: 'assistant',
          content: `**Error:** ${error instanceof Error ? error.message : 'Copilot service is unavailable.'}`,
          isError: true,
          createdAt: Date.now()
        }
      ]);
    } finally {
      setIsLoading(false);
      window.setTimeout(() => inputRef.current?.focus(), 40);
    }
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    sendMessage(input);
  };

  const handleComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendMessage(input);
    }
  };

  const quickPrompts = [
    { label: 'Forecast', icon: 'trend' as IconName, prompt: 'Forecast next quarter revenue leakage.' },
    { label: 'Risky sellers', icon: 'warning' as IconName, prompt: 'Show me the riskiest sellers today.' },
    { label: 'Best channel', icon: 'bolt' as IconName, prompt: 'Which marketing channel is performing best?' },
    { label: 'Anomalies', icon: 'radio' as IconName, prompt: 'Find revenue anomalies in the latest stream.' }
  ];

  return (
    <section className={`copilot-home ${messages.length > 0 ? 'has-messages' : ''}`} aria-label="Recovera Copilot">
      <div className="copilot-home-brand">
        <span><Icon name="sparkle" /></span>
        <strong>Recovera Copilot</strong>
      </div>

      <div className="copilot-ready-pill"><i /> {isLoading ? 'Analyzing' : 'Ready'}</div>

      <div className="copilot-stage">
        {messages.length === 0 ? (
          <div className="copilot-empty-state">
            <span className="copilot-hero-icon"><Icon name="sparkle" /></span>
            <h1>How can I help today?</h1>
            <p>Ask about revenue, risk, or growth.</p>
          </div>
        ) : (
          <div className="copilot-thread" ref={threadRef} aria-live="polite">
            {messages.map((message) => (
              <CopilotMessage
                key={message.id}
                message={message}
                onTogglePanel={toggleMessagePanel}
                onCopySql={copySql}
              />
            ))}
            {isLoading && <CopilotTyping />}
            <div ref={messagesEnd} />
          </div>
        )}
      </div>

      <div className="copilot-composer-wrap">
        <div className="copilot-prompt-row" aria-label="Suggested prompts">
          {quickPrompts.map((item) => (
            <button key={item.label} type="button" onClick={() => sendMessage(item.prompt)} disabled={isLoading}>
              <Icon name={item.icon} />
              {item.label}
            </button>
          ))}
        </div>

        <form className="copilot-compose" onSubmit={submit}>
          <textarea
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={handleComposerKeyDown}
            onPaste={(event) => {
              event.preventDefault();
              const pasted = event.clipboardData.getData('text/plain').replace(/^\n+|\n+$/g, '');
              const ta = event.currentTarget;
              const start = ta.selectionStart ?? ta.value.length;
              const end = ta.selectionEnd ?? ta.value.length;
              const next = ta.value.slice(0, start) + pasted + ta.value.slice(end);
              setInput(next);
              requestAnimationFrame(() => {
                ta.selectionStart = ta.selectionEnd = start + pasted.length;
              });
            }}
            placeholder="Ask Recovera about revenue leakage, seller risk, refunds, or growth..."
            aria-label="Ask Recovera Copilot"
            rows={1}
            disabled={isLoading}
          />
          <button type="submit" aria-label="Send message" disabled={!input.trim() || isLoading}>
            <Icon name="send" />
          </button>
        </form>

        <p className="copilot-note">Recovera can make mistakes. Verify important insights.</p>
      </div>
    </section>
  );
}

function useTTS(text: string, lang: 'ar' | 'en') {
  const [speaking, setSpeaking] = React.useState(false);
  const utterRef = React.useRef<SpeechSynthesisUtterance | null>(null);

  const stop = React.useCallback(() => {
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, []);

  const toggle = React.useCallback(() => {
    if (speaking) { stop(); return; }
    const plain = text
      .replace(/\*\*(.+?)\*\*/g, '$1')
      .replace(/\*(.+?)\*/g, '$1')
      .replace(/#{1,6}\s*/g, '')
      .replace(/`{1,3}[^`]*`{1,3}/g, '')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/^\s*[-*+]\s+/gm, '')
      .replace(/^\s*\d+\.\s+/gm, '')
      .replace(/\n{2,}/g, '. ')
      .replace(/\n/g, ' ')
      .trim();
    if (!plain) return;
    const utter = new SpeechSynthesisUtterance(plain);
    utter.lang = lang === 'ar' ? 'ar-EG' : 'en-US';
    utter.rate = 1;
    utter.pitch = 1;
    // Prefer a matching voice if available
    const voices = window.speechSynthesis.getVoices();
    const match = voices.find(v => v.lang.startsWith(lang === 'ar' ? 'ar' : 'en'));
    if (match) utter.voice = match;
    utter.onend = () => setSpeaking(false);
    utter.onerror = () => setSpeaking(false);
    utterRef.current = utter;
    window.speechSynthesis.speak(utter);
    setSpeaking(true);
  }, [speaking, text, lang, stop]);

  // Cleanup on unmount
  React.useEffect(() => () => { window.speechSynthesis.cancel(); }, []);

  return { speaking, toggle, stop };
}

function CopilotMessage({ message, onTogglePanel, onCopySql }: { message: ChatMessage; onTogglePanel: (messageId: string, panel: 'showSql' | 'showSteps') => void; onCopySql: (sql?: string | null) => void }) {
  const isUser = message.role === 'user';
  const parsed = !isUser && !message.isError ? parseAnalysisCards(message.content) : { remaining: message.content, cards: [] as AnalysisCard[] };
  const showBubble = isUser || message.isError || parsed.remaining.trim().length > 0 || parsed.cards.length === 0;

  const direction = getMessageDirection(message.content);

  // Build the full plain text for TTS: remaining prose + all card contents
  const ttsText = !isUser && !message.isError
    ? [parsed.remaining, ...parsed.cards.map(c => c.content)].filter(Boolean).join('\n')
    : '';
  const ttsLang: 'ar' | 'en' = direction === 'rtl' ? 'ar' : 'en';
  const { speaking, toggle } = useTTS(ttsText, ttsLang);

  return (
    <article id={`copilot-${message.id}`} className={`copilot-msg ${isUser ? 'user' : 'assistant'} ${message.isError ? 'error' : ''}`} dir={direction}>
      <div className={`copilot-avatar ${isUser ? 'user' : 'assistant'}`}>
        {isUser ? direction === 'rtl' ? 'أنت' : 'You' : <Icon name="sparkle" />}
      </div>

      <div className="copilot-msg-body">
        {showBubble && (
          <div className={`copilot-bubble ${isUser ? 'user' : 'assistant'} ${message.isError ? 'error' : ''}`}>
            {isUser ? <p>{message.content}</p> : <MarkdownBlock content={parsed.remaining || message.content} />}
          </div>
        )}

        {!isUser && parsed.cards.length > 0 && <AnalysisCards cards={parsed.cards} />}

        {!isUser && !message.isError && message.chartData && <CopilotResponseChart chartData={message.chartData} />}

        {!isUser && !message.isError && ttsText && (
          <div className="copilot-tts-row">
            <button
              type="button"
              className={`copilot-tts-btn${speaking ? ' speaking' : ''}`}
              onClick={toggle}
              aria-label={speaking ? (ttsLang === 'ar' ? 'إيقاف القراءة' : 'Stop reading') : (ttsLang === 'ar' ? 'استمع للرد' : 'Listen to response')}
              title={speaking ? (ttsLang === 'ar' ? 'إيقاف' : 'Stop') : (ttsLang === 'ar' ? 'استمع' : 'Listen')}
            >
              <Icon name={speaking ? 'stop' : 'volume'} />
              <span>{speaking ? (ttsLang === 'ar' ? 'إيقاف' : 'Stop') : (ttsLang === 'ar' ? 'استمع' : 'Listen')}</span>
              {speaking && <span className="copilot-tts-wave"><i/><i/><i/></span>}
            </button>
          </div>
        )}

        {!isUser && !message.isError && <CopilotMeta message={message} onTogglePanel={onTogglePanel} />}

        {!isUser && message.steps && message.steps.length > 0 && message.showSteps && (
          <div className="copilot-steps-box">
            <div className="copilot-panel-title"><Icon name="pulse" /> Pipeline steps</div>
            {message.steps.map((step, index) => <div className="copilot-step" key={`${message.id}-step-${index}`}><b>✓</b>{step}</div>)}
          </div>
        )}

        {!isUser && message.sql && (
          <div className="copilot-sql-box">
            <button className="copilot-sql-header" type="button" onClick={() => onTogglePanel(message.id, 'showSql')}>
              <span><Icon name="database" /> SQL query</span>
              <em>{message.showSql ? 'Hide' : 'Show'}</em>
            </button>
            {message.showSql && (
              <div className="copilot-sql-body">
                <button type="button" onClick={() => onCopySql(message.sql)}>Copy SQL</button>
                <pre dir="ltr">{message.sql}</pre>
              </div>
            )}
          </div>
        )}
      </div>
    </article>
  );
}


const CHART_TYPE_LABELS: Record<NormalizedCopilotChart['chartType'], string> = {
  bar: 'Distribution',
  bar_vertical: 'Distribution',
  line: 'Trend',
  area: 'Trend',
  pie: 'Breakdown'
};

function CopilotResponseChart({ chartData }: { chartData: CopilotChartData }) {
  const chart = normalizeChartForRender(chartData);
  if (!chart) return null;

  const tooltipStyle = {
    contentStyle: { border: '1px solid var(--line)', borderRadius: 14, background: 'var(--card-solid)', color: 'var(--text)', boxShadow: 'var(--shadow-soft)' },
    formatter: (value: number, name: string) => [formatMetricValue(Number(value)), chart.datasets.find((d) => d.key === name)?.label || name],
    labelStyle: { color: 'var(--text)', fontWeight: 800 }
  };

  const rows = chart.labels.map((label, index) => {
    const row: Record<string, string | number> = { label };
    chart.datasets.forEach((dataset) => { row[dataset.key] = dataset.data[index] ?? 0; });
    return row;
  });

  // Pie data uses first dataset only
  const pieData = chart.labels.map((label, index) => ({
    name: label,
    value: chart.datasets[0]?.data[index] ?? 0
  }));

  const isHorizontalBar = chart.chartType === 'bar';
  const isVerticalBar = chart.chartType === 'bar_vertical';
  const isLine = chart.chartType === 'line';
  const isArea = chart.chartType === 'area';
  const isPie = chart.chartType === 'pie';

  const barChartHeight = Math.min(440, Math.max(230, chart.labels.length * (chart.datasets.length > 1 ? 34 : 38) + 96));
  const verticalBarHeight = Math.min(380, Math.max(220, 80 + chart.labels.length * 44));
  const lineAreaHeight = 260;
  const pieHeight = 280;

  return (
    <section className="copilot-chart-card" dir="ltr" aria-label="Query result visualization">
      <header>
        <div>
          <strong>{chart.title}</strong>
          <span>•</span>
          <em>{CHART_TYPE_LABELS[chart.chartType]}</em>
        </div>
        <div className="copilot-chart-icons" aria-hidden="true"><Icon name="database" /><Icon name="trend" /></div>
      </header>

      {chart.datasets.length > 1 && !isPie && (
        <div className="copilot-chart-legend">
          {chart.datasets.map((dataset) => <span key={dataset.key} className={dataset.tone}><i />{dataset.label}</span>)}
        </div>
      )}

      {/* Horizontal Bar (default) */}
      {isHorizontalBar && (
        <div className="copilot-chart-shell" style={{ height: barChartHeight }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 18, left: 8, bottom: 8 }} barCategoryGap={chart.datasets.length > 1 ? 8 : 10}>
              <CartesianGrid stroke="var(--chart-grid)" strokeDasharray="0" />
              <XAxis type="number" tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={{ stroke: 'var(--line-strong)' }} tickFormatter={(v) => compactNumber(Number(v))} />
              <YAxis type="category" dataKey="label" width={144} tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} interval={0} tickFormatter={(v) => truncateChartTick(v)} />
              <Tooltip cursor={{ fill: 'rgba(99,183,239,0.08)' }} {...tooltipStyle} />
              {chart.datasets.map((dataset) => (
                <Bar key={dataset.key} dataKey={dataset.key} name={dataset.label} fill={`var(--copilot-chart-${dataset.tone})`} radius={[0, 4, 4, 0]} maxBarSize={24} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Vertical Bar */}
      {isVerticalBar && (
        <div className="copilot-chart-shell" style={{ height: verticalBarHeight }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} margin={{ top: 12, right: 18, left: 0, bottom: 32 }} barCategoryGap={chart.datasets.length > 1 ? 8 : 10}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis dataKey="label" tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} angle={-20} textAnchor="end" height={48} tickFormatter={(v) => truncateChartTick(v, 14)} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} width={52} tickFormatter={(v) => compactNumber(Number(v))} />
              <Tooltip cursor={{ fill: 'rgba(99,183,239,0.08)' }} {...tooltipStyle} />
              {chart.datasets.map((dataset) => (
                <Bar key={dataset.key} dataKey={dataset.key} name={dataset.label} fill={`var(--copilot-chart-${dataset.tone})`} radius={[4, 4, 0, 0]} maxBarSize={32} />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Line Chart */}
      {isLine && (
        <div className="copilot-chart-shell" style={{ height: lineAreaHeight }}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis dataKey="label" tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => truncateChartTick(v, 12)} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} width={52} tickFormatter={(v) => compactNumber(Number(v))} />
              <Tooltip {...tooltipStyle} />
              {chart.datasets.map((dataset) => (
                <Line key={dataset.key} type="monotone" dataKey={dataset.key} name={dataset.label} stroke={`var(--copilot-chart-${dataset.tone})`} strokeWidth={2.5} dot={{ r: 3, fill: `var(--copilot-chart-${dataset.tone})` }} activeDot={{ r: 5 }} />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Area Chart */}
      {isArea && (
        <div className="copilot-chart-shell" style={{ height: lineAreaHeight }}>
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={rows} margin={{ top: 12, right: 18, left: 0, bottom: 8 }}>
              <defs>
                {chart.datasets.map((dataset, i) => (
                  <linearGradient key={dataset.key} id={`area-fill-${i}`} x1="0" x2="0" y1="0" y2="1">
                    <stop offset="0%" stopColor={`var(--copilot-chart-${dataset.tone})`} stopOpacity={0.32} />
                    <stop offset="100%" stopColor={`var(--copilot-chart-${dataset.tone})`} stopOpacity={0.02} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid vertical={false} stroke="var(--chart-grid)" strokeDasharray="3 5" />
              <XAxis dataKey="label" tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} tickFormatter={(v) => truncateChartTick(v, 12)} />
              <YAxis tick={{ fill: 'var(--muted)', fontSize: 11 }} tickLine={false} axisLine={false} width={52} tickFormatter={(v) => compactNumber(Number(v))} />
              <Tooltip {...tooltipStyle} />
              {chart.datasets.map((dataset, i) => (
                <Area key={dataset.key} type="monotone" dataKey={dataset.key} name={dataset.label} stroke={`var(--copilot-chart-${dataset.tone})`} strokeWidth={2.5} fill={`url(#area-fill-${i})`} />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Pie Chart */}
      {isPie && (
        <div className="copilot-chart-shell" style={{ height: pieHeight }}>
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie data={pieData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius="72%" innerRadius="44%" cornerRadius={6} stroke="none" label={({ name, percent }) => `${truncateChartTick(name, 12)} ${(percent * 100).toFixed(0)}%`} labelLine={false}>
                {pieData.map((_, index) => (
                  <Cell key={index} fill={chartPalette[index % chartPalette.length]} />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{ border: '1px solid var(--line)', borderRadius: 14, background: 'var(--card-solid)', color: 'var(--text)' }}
                formatter={(value) => [formatMetricValue(Number(value)), chart.datasets[0]?.label || 'Value']}
              />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}

function CopilotMeta({ message, onTogglePanel }: { message: ChatMessage; onTogglePanel: (messageId: string, panel: 'showSql' | 'showSteps') => void }) {
  const badges: Array<{ label: string; tone: string }> = [];
  if (message.intent) badges.push({ label: message.intent, tone: 'blue' });
  if (message.route) badges.push({ label: message.route, tone: 'purple' });
  if (message.difficulty) badges.push({ label: message.difficulty, tone: message.difficulty === 'complex' ? 'red' : message.difficulty === 'medium' ? 'yellow' : 'green' });
  if (typeof message.confidence === 'number') badges.push({ label: `${Math.round(message.confidence * 100)}% conf`, tone: 'cyan' });

  if (!badges.length && !message.sql && !message.steps?.length && !message.executionMs && message.rowCount === undefined) return null;

  return (
    <div className="copilot-meta">
      {badges.map((badge) => <span key={`${badge.label}-${badge.tone}`} className={`copilot-badge ${badge.tone}`}>{badge.label}</span>)}
      <span className="copilot-meta-spacer" />
      {message.steps && message.steps.length > 0 && (
        <button type="button" onClick={() => onTogglePanel(message.id, 'showSteps')}>{message.showSteps ? 'Hide steps' : 'Show steps'}</button>
      )}
      {message.sql && (
        <button type="button" onClick={() => onTogglePanel(message.id, 'showSql')}>{message.showSql ? 'Hide SQL' : 'Show SQL'}</button>
      )}
      {message.executionMs !== undefined && <span className="copilot-meta-muted">{message.executionMs}ms</span>}
      {message.rowCount !== undefined && <span className="copilot-meta-muted">{message.rowCount} rows</span>}
    </div>
  );
}

function AnalysisCards({ cards }: { cards: AnalysisCard[] }) {
  const labels: Record<AnalysisCardType, string> = {
    finding: 'Key Finding',
    impact: 'Business Impact',
    evidence: 'Evidence',
    recommendation: 'Recommendation'
  };
  const icons: Record<AnalysisCardType, IconName> = {
    finding: 'warning',
    impact: 'trend',
    evidence: 'database',
    recommendation: 'bolt'
  };

  return (
    <div className="copilot-analysis-cards">
      {cards.map((card, index) => (
        <section key={`${card.type}-${index}`} className="copilot-analysis-card">
          <header>
            <span className={card.type}><Icon name={icons[card.type]} /></span>
            <strong>{labels[card.type]}</strong>
          </header>
          <div className="copilot-analysis-body"><MarkdownBlock content={card.content} /></div>
        </section>
      ))}
    </div>
  );
}

function CopilotTyping() {
  return (
    <article className="copilot-msg assistant">
      <div className="copilot-avatar assistant"><Icon name="sparkle" /></div>
      <div className="copilot-msg-body">
        <div className="copilot-typing-indicator">
          <i /><i /><i />
          <span>Analyzing Recovera signals</span>
        </div>
      </div>
    </article>
  );
}

function MarkdownBlock({ content }: { content: string }) {
  return <div className="copilot-markdown" dangerouslySetInnerHTML={{ __html: renderCopilotMarkdown(content) }} />;
}

async function readJsonSafely(response: Response): Promise<CopilotApiPayload> {
  try {
    return await response.json();
  } catch {
    return {};
  }
}

function normalizeCopilotResponse(payload: CopilotApiPayload): ChatMessage {
  const nested = isRecord(payload.data) ? payload.data : isRecord(payload.result) ? payload.result : {};
  const content = firstText(
    payload.answer,
    payload.response,
    payload.message,
    payload.content,
    nested.answer,
    nested.response,
    nested.message,
    nested.content
  ) || 'The Copilot API returned a response, but no answer text was found.';

  return {
    id: `a-${Date.now()}`,
    role: 'assistant',
    content,
    createdAt: Date.now(),
    sql: firstText(payload.sql_used, payload.sql, payload.generated_sql, nested.sql_used, nested.sql, nested.generated_sql) || null,
    rowCount: firstNumber(payload.row_count, payload.rowCount, payload.rows, nested.row_count, nested.rowCount, nested.rows),
    executionMs: firstNumber(payload.execution_ms, payload.executionMs, payload.latency_ms, payload.latencyMs, nested.execution_ms, nested.latency_ms),
    steps: firstStringArray(payload.steps, payload.pipeline_steps, payload.pipeline, nested.steps, nested.pipeline_steps),
    intent: firstText(payload.intent, nested.intent),
    route: firstText(payload.route, nested.route),
    difficulty: firstText(payload.difficulty, nested.difficulty),
    confidence: normalizeConfidence(firstNumber(payload.confidence, nested.confidence)),
    chartData: extractCopilotChartData(payload, nested)
  };
}


function extractCopilotChartData(payload: CopilotApiPayload, nested: Record<string, unknown>): CopilotChartData | null {
  const rows = firstRecordArray(
    payload.query_results,
    payload.queryResults,
    payload.records,
    payload.result_rows,
    payload.resultRows,
    payload.rows,
    nested.query_results,
    nested.queryResults,
    nested.records,
    nested.result_rows,
    nested.resultRows,
    nested.rows
  );
  if (rows) return makeChartDataFromRows(rows);

  const columnRows = makeRowsFromColumns(payload) || makeRowsFromColumns(nested);
  if (columnRows) return makeChartDataFromRows(columnRows);

  const chart = firstRecord(
    payload.chart_data,
    payload.chartData,
    payload.chart,
    payload.visualization,
    payload.visualisation,
    payload.plot,
    nested.chart_data,
    nested.chartData,
    nested.chart,
    nested.visualization,
    nested.visualisation,
    nested.plot
  );
  if (chart) return chart as CopilotChartData;

  return null;
}

function resolveChartType(raw?: string): NormalizedCopilotChart['chartType'] {
  const t = (raw || '').toLowerCase().replace(/[-\s]/g, '_');
  if (t.includes('line')) return 'line';
  if (t.includes('area')) return 'area';
  if (t.includes('pie') || t.includes('donut') || t.includes('doughnut')) return 'pie';
  if (t.includes('vertical') || t.includes('column')) return 'bar_vertical';
  return 'bar';
}

function normalizeChartForRender(chartData: CopilotChartData, inheritedType?: string): NormalizedCopilotChart | null {
  const chartType = resolveChartType(chartData.type || inheritedType);
  const labels = Array.isArray(chartData.labels) ? chartData.labels.map((label) => String(label)).filter(Boolean) : [];
  const datasetsSource = Array.isArray(chartData.datasets) ? chartData.datasets : [];
  const normalizedDatasets = datasetsSource
    .map((dataset, index) => ({
      label: dataset.label || (index === 0 ? 'Total Revenue' : `Series ${index + 1}`),
      key: `value_${index}`,
      data: Array.isArray(dataset.data) ? dataset.data.map(Number).filter((value) => Number.isFinite(value)) : [],
      tone: index === 0 ? 'primary' as const : index === 1 ? 'success' as const : 'accent' as const
    }))
    .filter((dataset) => dataset.data.length > 0);

  if (labels.length > 0 && normalizedDatasets.length > 0) {
    return {
      title: chartData.title || 'Query Results',
      chartType,
      labels,
      datasets: normalizedDatasets.map((dataset) => ({ ...dataset, data: labels.map((_, index) => dataset.data[index] ?? 0) }))
    };
  }

  const values = Array.isArray(chartData.values) ? chartData.values : Array.isArray(chartData.data) ? chartData.data : [];
  const numericValues = values.map(Number).filter((value) => Number.isFinite(value));
  if (labels.length > 0 && numericValues.length > 0) {
    return {
      title: chartData.title || 'Query Results',
      chartType,
      labels,
      datasets: [{ label: 'Value', key: 'value_0', data: labels.map((_, index) => numericValues[index] ?? 0), tone: 'primary' }]
    };
  }

  if (Array.isArray(chartData.rows) && chartData.rows.length > 0) {
    return normalizeChartForRender(makeChartDataFromRows(chartData.rows), chartData.type || inheritedType);
  }

  return null;
}

function makeChartDataFromRows(rows: Array<Record<string, unknown>>): CopilotChartData {
  const visibleRows = rows.slice(0, 10);
  const sample = visibleRows[0] || {};
  const keys = Object.keys(sample);
  const textKey = pickLabelKey(keys, visibleRows);
  const numericKeys = pickNumericMetricKeys(keys, visibleRows);

  return {
    title: 'Query Results',
    labels: visibleRows.map((row, index) => formatChartLabel(row[textKey], index)),
    datasets: numericKeys.map((key) => ({
      label: prettifyKey(key),
      data: visibleRows.map((row) => Number(row[key]) || 0)
    }))
  };
}

function pickLabelKey(keys: string[], rows: Array<Record<string, unknown>>) {
  const preferred = [
    'region', 'city', 'customer_city', 'seller_city', 'seller_name', 'seller', 'channel', 'campaign', 'campaign_name',
    'scenario', 'leakage_type', 'month_label', 'month', 'name', 'label', 'product', 'category'
  ];
  const lowerToKey = new Map(keys.map((key) => [key.toLowerCase(), key]));
  for (const key of preferred) {
    const actual = lowerToKey.get(key);
    if (actual && rows.some((row) => String(row[actual] ?? '').trim())) return actual;
  }
  return keys.find((key) => rows.some((row) => typeof row[key] === 'string' && String(row[key]).trim())) || keys[0] || 'label';
}

function pickNumericMetricKeys(keys: string[], rows: Array<Record<string, unknown>>) {
  const numericKeys = keys.filter((key) => rows.some((row) => Number.isFinite(Number(row[key]))));
  const excluded = /(^|_)(id|rank|latitude|longitude|zip|postcode|year|month|quarter|flag|sequential)($|_)/i;
  const usable = numericKeys.filter((key) => !excluded.test(key));
  const moneyPriority = [
    'total_revenue', 'revenue', 'attributed_revenue', 'leakage_revenue', 'revenue_at_risk', 'total_profit', 'profit', 'spend', 'budget', 'payment_value'
  ];
  const lowerToKey = new Map(usable.map((key) => [key.toLowerCase(), key]));
  const selected: string[] = [];
  for (const key of moneyPriority) {
    const actual = lowerToKey.get(key);
    if (actual && !selected.includes(actual)) selected.push(actual);
  }
  if (selected.length >= 2) return selected.slice(0, 2);
  for (const key of usable) {
    if (!selected.includes(key)) selected.push(key);
    if (selected.length >= 2) break;
  }
  return selected;
}

function formatChartLabel(value: unknown, index: number) {
  const label = String(value ?? '').replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
  return label || `Row ${index + 1}`;
}

function truncateChartTick(value: unknown, max = 18) {
  const label = String(value ?? '');
  return label.length > max ? `${label.slice(0, max - 1)}…` : label;
}

function makeRowsFromColumns(source: Record<string, unknown>) {
  const columns = Array.isArray(source.columns) ? source.columns.map(String) : null;
  const rows = Array.isArray(source.rows) ? source.rows : null;
  if (!columns || !rows || !rows.every(Array.isArray)) return null;
  return rows.map((row) => Object.fromEntries(columns.map((column, index) => [column, (row as unknown[])[index]])));
}

function firstRecord(...values: unknown[]) {
  for (const value of values) {
    if (isRecord(value)) return value;
  }
  return null;
}

function firstRecordArray(...values: unknown[]) {
  for (const value of values) {
    if (Array.isArray(value) && value.length > 0 && value.every(isRecord)) return value as Array<Record<string, unknown>>;
  }
  return null;
}

function normalizeConfidence(value?: number) {
  if (value === undefined) return undefined;
  return value > 1 ? value / 100 : value;
}

function prettifyKey(key: string) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function compactNumber(value: number) {
  if (!Number.isFinite(value)) return '0';
  const abs = Math.abs(value);
  if (abs >= 1_000_000_000) return `${(value / 1_000_000_000).toFixed(abs >= 10_000_000_000 ? 0 : 1)}B`;
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(abs >= 10_000 ? 0 : 1)}K`;
  return String(Math.round(value));
}

function formatMetricValue(value: number) {
  if (!Number.isFinite(value)) return '0';
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: value % 1 ? 2 : 0 }).format(value);
}

function getMessageDirection(text: string): 'ltr' | 'rtl' {
  const arabicChars = text.match(/[\u0600-\u06FF]/g)?.length || 0;
  const latinChars = text.match(/[A-Za-z]/g)?.length || 0;
  return arabicChars > latinChars ? 'rtl' : 'ltr';
}

function getApiErrorMessage(payload: CopilotApiPayload) {
  const upstream = isRecord(payload.upstream) ? payload.upstream : {};
  return firstText(payload.error, payload.detail, payload.message, upstream.error, upstream.detail, upstream.message);
}

function makeCopilotSessionId() {
  if (typeof window === 'undefined') return 'recovera-ui-session';
  const storageKey = 'recovera-copilot-session';
  const saved = window.localStorage.getItem(storageKey);
  if (saved) return saved;
  const next = typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : `recovera-${Date.now()}`;
  window.localStorage.setItem(storageKey, next);
  return next;
}

function firstText(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) return value.trim();
    if (typeof value === 'number' && Number.isFinite(value)) return String(value);
    if (isRecord(value)) return JSON.stringify(value, null, 2);
  }
  return undefined;
}

function firstNumber(...values: unknown[]) {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  }
  return undefined;
}

function firstStringArray(...values: unknown[]) {
  for (const value of values) {
    if (Array.isArray(value)) {
      const steps = value.map((item) => typeof item === 'string' ? item : isRecord(item) ? firstText(item.name, item.label, item.step, item.message) : undefined).filter(Boolean) as string[];
      if (steps.length) return steps;
    }
  }
  return undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseAnalysisCards(content: string): { remaining: string; cards: AnalysisCard[] } {
  const labelToType: Record<string, AnalysisCardType> = {
    'key finding': 'finding',
    finding: 'finding',
    'business impact': 'impact',
    impact: 'impact',
    evidence: 'evidence',
    recommendation: 'recommendation'
  };

  const lines = content.split(/\r?\n/);
  const remaining: string[] = [];
  const cards: AnalysisCard[] = [];
  let active: AnalysisCard | null = null;

  for (const line of lines) {
    const match = line.match(/^\s*(?:#{1,3}\s*)?(?:\*\*)?(Key Finding|Finding|Business Impact|Impact|Evidence|Recommendation)(?:\*\*)?\s*:?\s*(.*)$/i);
    if (match) {
      if (active && active.content.trim()) cards.push({ ...active, content: active.content.trim() });
      const type = labelToType[match[1].toLowerCase()] || 'finding';
      active = { type, content: match[2]?.trim() || '' };
      continue;
    }

    if (active) active.content += `${active.content ? '\n' : ''}${line}`;
    else remaining.push(line);
  }

  if (active && active.content.trim()) cards.push({ ...active, content: active.content.trim() });
  return { remaining: remaining.join('\n').trim(), cards };
}

function renderCopilotMarkdown(text: string) {
  const normalized = text.trim();
  if (!normalized) return '';

  const tableHtml = renderMarkdownTable(normalized);
  if (tableHtml) return tableHtml;

  let html = escapeHtmlString(normalized);
  html = html.replace(/^### (.*)$/gim, '<h3>$1</h3>');
  html = html.replace(/^## (.*)$/gim, '<h2>$1</h2>');
  html = html.replace(/^# (.*)$/gim, '<h1>$1</h1>');
  html = html.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
  html = html.replace(/(^|\n)[-•]\s+([^\n]+)/g, '$1<li>$2</li>');
  html = html.replace(/(<li>[\s\S]*?<\/li>)/g, '<ul>$1</ul>');
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = html.replace(/\n/g, '<br />');
  html = `<p>${html}</p>`;
  html = html.replace(/<p><(h[123]|ul|pre)/g, '<$1');
  html = html.replace(/<\/((?:h[123])|ul|pre)><\/p>/g, '</$1>');
  return html;
}

function renderMarkdownTable(text: string) {
  const lines = text.split(/\r?\n/).filter((line) => line.trim());
  if (lines.length < 2 || !lines[0].includes('|') || !/^\s*\|?\s*:?-{3,}:?/.test(lines[1])) return null;
  const rows = lines.filter((line) => line.includes('|')).map((line) => line.trim().replace(/^\||\|$/g, '').split('|').map((cell) => escapeHtmlString(cell.trim())));
  if (rows.length < 2) return null;
  const [head, , ...body] = rows;
  return `<div class="copilot-table-wrap"><table><thead><tr>${head.map((cell) => `<th>${cell}</th>`).join('')}</tr></thead><tbody>${body.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function escapeHtmlString(value: string) {
  return value.replace(/[&<>"]/g, (character) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[character] || character));
}


function MetricTile({ label, value, delta, tone, series }: { label: string; value: string; delta: string; tone: 'yellow' | 'green' | 'blue'; series: number[] }) {
  const data = useMemo(() => series.map((point, index) => ({ index, point })), [series]);
  return (
    <article className={`metric-tile tone-${tone}`}>
      <span>{label}</span>
      <div className="metric-value-row">
        <strong>{value}</strong>
        <small>{delta}</small>
      </div>
      <div className="sparkline">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 0, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={`spark-${tone}`} x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stopColor={`var(--${tone})`} stopOpacity={0.38} />
                <stop offset="100%" stopColor={`var(--${tone})`} stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <XAxis dataKey="index" hide />
            <YAxis hide domain={[0, 'dataMax + 10']} />
            <Area dataKey="point" type="monotone" stroke={`var(--${tone})`} strokeWidth={2.4} fill={`url(#spark-${tone})`} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </article>
  );
}

function StatTile({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <article className="stat-tile">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}

function AiStat({ label, value, status, watch = false }: { label: string; value: string; status: string; watch?: boolean }) {
  return (
    <article className="ai-stat">
      <span>{label}</span>
      <strong>{value}</strong>
      <b className={watch ? 'watch' : ''}>{status}</b>
    </article>
  );
}

function GlassCard({ title, subtitle, children, className = '', pulse = false }: { title: string; subtitle?: string; children?: ReactNode; className?: string; pulse?: boolean }) {
  return (
    <section className={`glass-card ${className}`.trim()}>
      <div className="card-title-row">
        <div>
          <h2>{title}</h2>
          {subtitle !== undefined && <p>{subtitle}</p>}
        </div>
        {pulse && <Icon name="pulse" />}
      </div>
      {children}
    </section>
  );
}

function DashboardNotice({ dashboard, status, error }: { dashboard: DashboardPayload; status: DashboardLoadState; error: string | null }) {
  const title = status === 'loading' ? 'Connecting to database' : status === 'error' ? 'Dashboard API error' : dashboard.source === 'demo' ? 'Demo data active' : dashboard.source === 'partial' ? 'Partial live data' : 'Live database';
  const detail = error || dashboard.warnings[0] || 'Dashboard is reading from the configured database.';
  return (
    <div className={`dashboard-notice source-${dashboard.source}`}>
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  );
}

function formatScenarioName(name: string) {
  return name.replace(/_/g, ' ').replace(/\s+/g, ' ').trim();
}

type ScenarioBreakdownItem = { name: string; value: number; color: string; amount?: number; orders?: number };

function ScenarioDonut({ items }: { items: ScenarioBreakdownItem[] }) {
  const fallbackItems: ScenarioBreakdownItem[] = scenarioMix;
  const rawData: ScenarioBreakdownItem[] = (items.length ? items : fallbackItems).filter((item) => item.value > 0);
  const topRows: ScenarioBreakdownItem[] = rawData.slice(0, 6);
  const overflowRows: ScenarioBreakdownItem[] = rawData.slice(6);
  const otherTotal = overflowRows.reduce((sum, item) => sum + item.value, 0);
  const otherAmount = overflowRows.reduce((sum, item) => sum + (item.amount || 0), 0);
  const otherOrders = overflowRows.reduce((sum, item) => sum + (item.orders || 0), 0);
  const data = otherTotal > 0
    ? [...topRows, { name: 'Other scenarios', value: Number(otherTotal.toFixed(1)), amount: otherAmount, orders: otherOrders, color: 'var(--muted)' }]
    : topRows;
  const maxValue = Math.max(...data.map((item) => item.value), 1);

  return (
    <div className="scenario-bars">
      {data.map((item) => {
        const label = formatScenarioName(item.name);
        return (
          <article className="scenario-row" key={item.name}>
            <div className="scenario-row-head">
              <span title={label}><i style={{ background: item.color }} />{label}</span>
              <b>{item.value}%</b>
            </div>
            <div className="scenario-track" aria-hidden="true">
              <div style={{ width: `${Math.max(4, (item.value / maxValue) * 100)}%`, background: item.color }} />
            </div>
            {item.amount !== undefined && (
              <small>{formatCompactCurrency(item.amount)}{item.orders ? ` · ${formatCompactNumber(item.orders)} orders` : ''}</small>
            )}
          </article>
        );
      })}
    </div>
  );
}

function RingGauge({ value }: { value: number }) {
  const data = [{ name: 'hit', value }, { name: 'miss', value: 100 - value }];
  return (
    <div className="ring-gauge">
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie data={data} dataKey="value" startAngle={90} endAngle={-270} innerRadius="68%" outerRadius="84%" cornerRadius={14} stroke="none">
            <Cell fill="var(--purple)" />
            <Cell fill="var(--gauge-track)" />
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      <div><strong>{value}%</strong><span>CACHE HIT</span></div>
    </div>
  );
}

function HealthTile({ label, value, status }: { label: string; value: string; status: 'healthy' | 'watch' }) {
  return (
    <article className="health-tile">
      <span>{label}</span>
      <strong>{value}</strong>
      <i className={status} />
    </article>
  );
}

function RiskBadge({ tier }: { tier: string }) {
  return <span className={`risk-badge ${tier.toLowerCase()}`}>{tier}</span>;
}

function TierBar({ label, value, tone }: { label: string; value: number; tone: 'red' | 'yellow' | 'green' }) {
  return (
    <div className="tier-bar">
      <div><span>{label}</span><b>{value}%</b></div>
      <i><em className={tone} style={{ width: `${value}%` }} /></i>
    </div>
  );
}

function SnapshotMetric({ label, value }: { label: string; value: string }) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function InsightCard({ label, children }: { label: string; children: ReactNode }) {
  return (
    <article>
      <span><Icon name="sparkle" /> {label}</span>
      <p>{children}</p>
    </article>
  );
}

function PinnedInsight({ text }: { text: string }) {
  return (
    <article>
      <Icon name="trend" />
      <strong>{text}</strong>
    </article>
  );
}

function Legend({ items }: { items: Array<[string, string]> }) {
  return (
    <div className="chart-legend">
      {items.map(([label, color]) => <span key={label}><i style={{ background: color }} />{label}</span>)}
    </div>
  );
}

function ChartTooltip({ active, payload, label, suffix = '' }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="chart-tooltip">
      <strong>{label}</strong>
      {payload.map((entry: any) => (
        <span key={entry.name || entry.dataKey}>
          <i style={{ background: entry.color }} />
          {entry.name || entry.dataKey}: {typeof entry.value === 'number' ? entry.value.toLocaleString() : entry.value}{suffix}
        </span>
      ))}
    </div>
  );
}

function MarketingCurrencyTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  const row = payload[0]?.payload;
  return (
    <div className="chart-tooltip">
      <strong>{row?.campaign || label}</strong>
      {row?.channel && <span>{row.channel}</span>}
      {payload.map((entry: any) => (
        <span key={entry.name || entry.dataKey}>
          <i style={{ background: entry.color }} />
          {entry.name || entry.dataKey}: {formatCompactCurrency(Number(entry.value || 0))}
        </span>
      ))}
      {typeof row?.roas === 'number' && <span>ROAS: {row.roas.toFixed(2)}x</span>}
    </div>
  );
}

function FloatingTools() {
  return (
    <div className="floating-tools" aria-label="Canvas tools">
      <button type="button" aria-label="Expand"><Icon name="expand" /></button>
      <button type="button" aria-label="Text"><Icon name="type" /></button>
      <button type="button" aria-label="Annotate"><Icon name="pen" /></button>
      <button type="button" aria-label="Comment"><Icon name="chat" /></button>
    </div>
  );
}

function LogoMark() {
  return (
    <span className="logo-mark logo-radar" aria-hidden="true">
      <span className="radar-pulse one" />
      <span className="radar-pulse two" />
      <span className="radar-sweep" />
      <svg className="radar-glyph" viewBox="0 0 48 48" fill="none">
        <circle cx="24" cy="24" r="15.5" stroke="currentColor" strokeWidth="1.8" opacity="0.52" />
        <circle cx="24" cy="24" r="8.6" stroke="currentColor" strokeWidth="1.6" opacity="0.42" />
        <path d="M24 9v6M24 33v6M9 24h6M33 24h6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" opacity="0.55" />
        <path d="M24 24 35 15" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
        <circle cx="24" cy="24" r="3.6" fill="currentColor" />
        <circle cx="32.6" cy="17" r="2.2" fill="currentColor" opacity="0.82" />
      </svg>
    </span>
  );
}

function IconButton({ icon, label, onClick }: { icon: IconName; label: string; onClick?: () => void }) {
  return <button className="icon-button" type="button" aria-label={label} onClick={onClick}><Icon name={icon} /></button>;
}

function Icon({ name }: { name: IconName }) {
  const common = { width: 20, height: 20, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, 'aria-hidden': true };
  switch (name) {
    case 'grid': return <svg {...common}><path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h4A1.5 1.5 0 0 1 11 5.5v4A1.5 1.5 0 0 1 9.5 11h-4A1.5 1.5 0 0 1 4 9.5v-4Z" /><path d="M13 5.5A1.5 1.5 0 0 1 14.5 4h4A1.5 1.5 0 0 1 20 5.5v4a1.5 1.5 0 0 1-1.5 1.5h-4A1.5 1.5 0 0 1 13 9.5v-4Z" /><path d="M4 14.5A1.5 1.5 0 0 1 5.5 13h4a1.5 1.5 0 0 1 1.5 1.5v4A1.5 1.5 0 0 1 9.5 20h-4A1.5 1.5 0 0 1 4 18.5v-4Z" /><path d="M13 14.5a1.5 1.5 0 0 1 1.5-1.5h4a1.5 1.5 0 0 1 1.5 1.5v4a1.5 1.5 0 0 1-1.5 1.5h-4a1.5 1.5 0 0 1-1.5-1.5v-4Z" /></svg>;
    case 'warning': return <svg {...common}><path d="M12 3.5 21 20H3L12 3.5Z" /><path d="M12 9v5" /><path d="M12 17.5h.01" /><path d="M7.8 17h8.4" opacity={0.35} /></svg>;
    case 'database': return <svg {...common}><path d="M4 7c0-2 3.6-3.5 8-3.5S20 5 20 7s-3.6 3.5-8 3.5S4 9 4 7Z" /><path d="M4 7v5c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5V7" /><path d="M4 12v5c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5v-5" /></svg>;
    case 'pulse': return <svg {...common}><path d="M3 12h3.4l2.1-5.5 4.1 11 2.7-7 1.7 3h4" /><path d="M4 19h16" opacity={0.35} /></svg>;
    case 'trend': return <svg {...common}><path d="M4 17.5 9.5 12l4 4L20 7.5" /><path d="M15 7h5v5" /><path d="M4 20h16" opacity={0.35} /></svg>;
    case 'sparkle': return <svg {...common}><path d="M12 3.2 14 9l5.8 2-5.8 2-2 5.8-2-5.8-5.8-2 5.8-2 2-5.8Z" /><path d="M5 15.5 5.8 18l2.4.8-2.4.8L5 22l-.8-2.4-2.4-.8 2.4-.8.8-2.5Z" /><path d="M19 2.5l.7 2.1 2.1.7-2.1.7L19 8.1l-.7-2.1-2.1-.7 2.1-.7.7-2.1Z" /></svg>;
    case 'refresh': return <svg {...common}><path d="M20 11a8 8 0 0 0-14.5-4.5L3 9" /><path d="M3 4v5h5" /><path d="M4 13a8 8 0 0 0 14.5 4.5L21 15" /><path d="M21 20v-5h-5" /></svg>;
    case 'filter': return <svg {...common}><path d="M3 5h18l-7 8v5l-4 2v-7L3 5Z" /></svg>;
    case 'pin': return <svg {...common}><path d="M9 4h6l-1 6 4 4v2H6v-2l4-4-1-6Z" /><path d="M12 16v5" /></svg>;
    case 'share': return <svg {...common}><circle cx="18" cy="5" r="3" /><circle cx="6" cy="12" r="3" /><circle cx="18" cy="19" r="3" /><path d="m8.6 10.5 6.8-4" /><path d="m8.6 13.5 6.8 4" /></svg>;
    case 'bell': return <svg {...common}><path d="M6 8a6 6 0 1 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9" /><path d="M10 21h4" /></svg>;
    case 'moon': return <svg {...common}><path d="M20.5 14.5A8.5 8.5 0 0 1 9.5 3.5a7 7 0 1 0 11 11Z" /></svg>;
    case 'sun': return <svg {...common}><circle cx="12" cy="12" r="4" /><path d="M12 2v2" /><path d="M12 20v2" /><path d="m4.93 4.93 1.41 1.41" /><path d="m17.66 17.66 1.41 1.41" /><path d="M2 12h2" /><path d="M20 12h2" /><path d="m6.34 17.66-1.41 1.41" /><path d="m19.07 4.93-1.41 1.41" /></svg>;
    case 'search': return <svg {...common}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>;
    case 'bot': return <svg {...common}><rect x="5" y="8" width="14" height="10" rx="3" /><path d="M12 5v3" /><path d="M8 12h.01" /><path d="M16 12h.01" /><path d="M9 18v2" /><path d="M15 18v2" /></svg>;
    case 'mic': return <svg {...common}><rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5 11a7 7 0 0 0 14 0" /><path d="M12 18v3" /></svg>;
    case 'send': return <svg {...common}><path d="M22 2 11 13" /><path d="m22 2-7 20-4-9-9-4 20-7Z" /></svg>;
    case 'plus': return <svg {...common}><path d="M12 5v14" /><path d="M5 12h14" /></svg>;
    case 'expand': return <svg {...common}><path d="M8 3H3v5" /><path d="M16 3h5v5" /><path d="M8 21H3v-5" /><path d="M16 21h5v-5" /><path d="M3 3l6 6" /><path d="M21 3l-6 6" /><path d="M3 21l6-6" /><path d="M21 21l-6-6" /></svg>;
    case 'type': return <svg {...common}><path d="M4 7V4h16v3" /><path d="M9 20h6" /><path d="M12 4v16" /></svg>;
    case 'pen': return <svg {...common}><path d="m16 3 5 5L8 21H3v-5L16 3Z" /></svg>;
    case 'chat': return <svg {...common}><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v8Z" /></svg>;
    case 'radio': return <svg {...common}><path d="M4.9 19.1a10 10 0 0 1 0-14.2" /><path d="M8.5 15.5a5 5 0 0 1 0-7" /><circle cx="12" cy="12" r="1.8" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /><path d="M19.1 4.9a10 10 0 0 1 0 14.2" /></svg>;
    case 'bolt': return <svg {...common}><path d="m13 2-9 13h7l-1 7 9-13h-7l1-7Z" /></svg>;
    case 'volume': return <svg {...common}><path d="M11 5 6 9H2v6h4l5 4V5Z" /><path d="M15.5 8.5a5 5 0 0 1 0 7" /><path d="M19 5a10 10 0 0 1 0 14" /></svg>;
    case 'stop': return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="2" /></svg>;
  }
}
