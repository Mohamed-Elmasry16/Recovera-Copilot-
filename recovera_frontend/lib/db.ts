import { Pool, type QueryResultRow } from 'pg';

/**
 * Server-only Postgres helper.
 *
 * Notes for Supabase:
 * - Transaction pooler normally uses port 6543 and is the best default for web apps.
 * - A password that contains URL-reserved characters must be URL encoded, e.g. # => %23.
 */
type PoolGlobal = typeof globalThis & { __recoveraPgPool?: Pool };

const DB_ENV_KEYS = ['SUPABASE_DB_URL', 'SUPABASE_POOLER_URL', 'POSTGRES_URL', 'DATABASE_URL', 'DATABASE_POOL_URL', 'DATABASE_POOL'] as const;

type DbEnvKey = (typeof DB_ENV_KEYS)[number];

function cleanEnvValue(value: string) {
  let next = value.trim();

  // Support accidental values like DATABASE_URL="postgresql://..." or DATABASE_URL=postgresql://...
  const accidentalAssignment = next.match(/^(?:DATABASE_URL|POSTGRES_URL|SUPABASE_POOLER_URL|SUPABASE_DB_URL|DATABASE_POOL_URL|DATABASE_POOL)\s*=\s*(.+)$/);
  if (accidentalAssignment?.[1]) next = accidentalAssignment[1].trim();

  if ((next.startsWith('"') && next.endsWith('"')) || (next.startsWith("'") && next.endsWith("'"))) {
    next = next.slice(1, -1).trim();
  }

  return next;
}

function getConfiguredConnectionString(): { key: DbEnvKey | null; value: string } {
  for (const key of DB_ENV_KEYS) {
    const value = process.env[key];
    if (value && value.trim()) {
      return { key, value: cleanEnvValue(value) };
    }
  }
  return { key: null, value: '' };
}

const CONFIGURED_CONNECTION = getConfiguredConnectionString();
const CONNECTION_STRING = CONFIGURED_CONNECTION.value;

function getParsedConnectionUrl() {
  if (!CONNECTION_STRING) return null;
  try {
    return new URL(CONNECTION_STRING);
  } catch {
    return null;
  }
}

function sslModeFromConnectionString() {
  return getParsedConnectionUrl()?.searchParams.get('sslmode') || null;
}

function shouldDisableSsl() {
  return sslModeFromConnectionString() === 'disable' || process.env.PG_SSL === 'disable';
}

function shouldRejectUnauthorized() {
  // Supabase pooler frequently fails in local Node environments with
  // "self-signed certificate in certificate chain" when sslmode=require is
  // parsed as certificate verification. Default to no verification for this app,
  // but allow strict verification by setting PG_SSL_REJECT_UNAUTHORIZED=true.
  return process.env.PG_SSL_REJECT_UNAUTHORIZED === 'true';
}

function connectionStringForPg() {
  if (!CONNECTION_STRING) return CONNECTION_STRING;
  const parsed = getParsedConnectionUrl();
  if (!parsed) return CONNECTION_STRING;

  // node-postgres/pg-connection-string can interpret sslmode=require as
  // verify-full, which causes local Supabase pooler failures. We pass SSL
  // explicitly through the Pool config instead.
  parsed.searchParams.delete('sslmode');
  parsed.searchParams.delete('sslcert');
  parsed.searchParams.delete('sslkey');
  parsed.searchParams.delete('sslrootcert');
  return parsed.toString();
}


export function supportedDatabaseEnvNames() {
  return DB_ENV_KEYS.join(', ');
}

export function hasDatabaseUrl() {
  return Boolean(CONNECTION_STRING);
}

export function getDatabaseConfigSummary() {
  if (!CONFIGURED_CONNECTION.key || !CONNECTION_STRING) {
    return {
      configured: false,
      envKey: null,
      driver: null,
      host: null,
      port: null,
      database: null,
      sslmode: null,
      sslVerification: null,
      isSupabasePooler: false,
      warning: `Missing database connection string. Set one of: ${supportedDatabaseEnvNames()}.`
    };
  }

  try {
    const parsed = new URL(CONNECTION_STRING);
    const isSupabasePooler = parsed.hostname.includes('pooler.supabase.com');
    const port = parsed.port || (parsed.protocol === 'postgresql:' || parsed.protocol === 'postgres:' ? '5432' : '');
    const warning = isSupabasePooler && port === '5432'
      ? 'Supabase pooler is configured on port 5432. If local/dev connection times out, use the transaction pooler port 6543.'
      : null;

    return {
      configured: true,
      envKey: CONFIGURED_CONNECTION.key,
      driver: parsed.protocol.replace(':', ''),
      host: parsed.hostname,
      port,
      database: parsed.pathname.replace(/^\//, '') || null,
      sslmode: parsed.searchParams.get('sslmode'),
      sslVerification: shouldRejectUnauthorized() ? 'verify' : 'no-verify',
      isSupabasePooler,
      warning
    };
  } catch (error) {
    return {
      configured: true,
      envKey: CONFIGURED_CONNECTION.key,
      driver: null,
      host: null,
      port: null,
      database: null,
      sslmode: null,
      sslVerification: null,
      isSupabasePooler: false,
      warning: `Invalid database URL in ${CONFIGURED_CONNECTION.key}. ${error instanceof Error ? error.message : String(error)}`
    };
  }
}

export function getPool(): Pool {
  if (!CONNECTION_STRING) {
    throw new Error(`Missing database connection string. Supported env names: ${supportedDatabaseEnvNames()}`);
  }

  const globalForPool = globalThis as PoolGlobal;
  if (!globalForPool.__recoveraPgPool) {
    globalForPool.__recoveraPgPool = new Pool({
      connectionString: connectionStringForPg(),
      ssl: shouldDisableSsl() ? undefined : { rejectUnauthorized: shouldRejectUnauthorized() },
      max: Number(process.env.PG_POOL_MAX || 2),
      idleTimeoutMillis: Number(process.env.PG_IDLE_TIMEOUT_MS || 10_000),
      connectionTimeoutMillis: Number(process.env.PG_CONNECTION_TIMEOUT_MS || 12_000),
      statement_timeout: Number(process.env.PG_STATEMENT_TIMEOUT_MS || 30_000),
      query_timeout: Number(process.env.PG_QUERY_TIMEOUT_MS || 30_000)
    });
  }
  return globalForPool.__recoveraPgPool;
}

export async function sql<T extends QueryResultRow = QueryResultRow>(query: string, params: unknown[] = []): Promise<T[]> {
  const pool = getPool();
  const result = await pool.query<T>(query, params);
  return result.rows;
}

export async function pingDatabase() {
  const startedAt = Date.now();
  const rows = await sql<{ ok: number }>('SELECT 1 AS ok');
  return {
    ok: rows[0]?.ok === 1,
    latencyMs: Date.now() - startedAt
  };
}

export async function firstSuccessful<T>(
  name: string,
  candidates: Array<() => Promise<T>>,
  warnings: string[]
): Promise<T | null> {
  let lastError = '';

  for (const candidate of candidates) {
    try {
      return await candidate();
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
  }

  warnings.push(`${name}: ${lastError || 'no compatible query succeeded'}`);
  return null;
}

export function numberish(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'bigint') return Number(value);
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/,/g, ''));
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

export function textish(value: unknown, fallback = 'Unknown'): string {
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (value === null || value === undefined) return fallback;
  return String(value);
}
