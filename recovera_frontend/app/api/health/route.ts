import { NextResponse } from 'next/server';
import { getDatabaseConfigSummary, hasDatabaseUrl, pingDatabase } from '@/lib/db';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

export async function GET() {
  const checks = {
    app: 'healthy',
    database: 'not_configured' as 'healthy' | 'not_configured' | 'offline',
    chatbot: 'not_configured' as 'healthy' | 'not_configured' | 'offline'
  };

  const database = getDatabaseConfigSummary();
  let databaseError: string | null = null;
  let databaseLatencyMs: number | null = null;

  if (hasDatabaseUrl()) {
    try {
      const ping = await pingDatabase();
      checks.database = ping.ok ? 'healthy' : 'offline';
      databaseLatencyMs = ping.latencyMs;
    } catch (error) {
      checks.database = 'offline';
      databaseError = error instanceof Error ? error.message : String(error);
    }
  }

  const chatBase = process.env.CHAT_API_BASE_URL || process.env.CHAT_API_URL || process.env.COPILOT_API_URL || process.env.NEXT_PUBLIC_CHAT_API_URL || '';
  if (chatBase) {
    try {
      const res = await fetch(`${chatBase.replace(/\/$/, '')}/api/health`, { cache: 'no-store' });
      checks.chatbot = res.ok ? 'healthy' : 'offline';
    } catch {
      checks.chatbot = 'offline';
    }
  }

  return NextResponse.json(
    {
      checks,
      database,
      databaseLatencyMs,
      databaseError,
      generatedAt: new Date().toISOString()
    },
    { headers: { 'Cache-Control': 'no-store' } }
  );
}
