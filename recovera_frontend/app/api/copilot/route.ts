import { NextRequest, NextResponse } from 'next/server';
import type { ChatApiResponse } from '@/lib/types';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type RequestBody = {
  session_id?: string;
  message?: string;
};

function resolveChatEndpoint() {
  const exactUrl = process.env.CHAT_API_URL || process.env.COPILOT_API_URL || '';
  if (exactUrl.trim()) return exactUrl.trim();

  const apiBase = process.env.CHAT_API_BASE_URL || process.env.NEXT_PUBLIC_CHAT_API_URL || '';
  if (!apiBase.trim()) return '';

  const cleanBase = apiBase.trim().replace(/\/$/, '');
  if (/\/api\/chat$/i.test(cleanBase) || /\/chat$/i.test(cleanBase)) return cleanBase;
  return `${cleanBase}/api/chat`;
}

export async function POST(request: NextRequest) {
  const chatEndpoint = resolveChatEndpoint();
  if (!chatEndpoint) {
    return NextResponse.json(
      {
        error: 'Chatbot API URL is not configured. Set CHAT_API_URL or CHAT_API_BASE_URL.'
      },
      { status: 503 }
    );
  }

  let body: RequestBody;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body.' }, { status: 400 });
  }

  if (!body.session_id || !body.message?.trim()) {
    return NextResponse.json({ error: 'session_id and message are required.' }, { status: 400 });
  }

  try {
    const backendHeaders: Record<string, string> = { 'Content-Type': 'application/json' };
    if (process.env.BACKEND_API_KEY) backendHeaders['X-API-Key'] = process.env.BACKEND_API_KEY;

    const res = await fetch(chatEndpoint, {
      method: 'POST',
      headers: backendHeaders,
      body: JSON.stringify({ session_id: body.session_id, message: body.message }),
      cache: 'no-store'
    });

    const contentType = res.headers.get('content-type') || '';
    const data = contentType.includes('application/json')
      ? ((await res.json()) as ChatApiResponse | { detail?: unknown; error?: unknown })
      : { answer: await res.text() };

    if (!res.ok) {
      return NextResponse.json({ error: 'Chatbot API error', upstream: data }, { status: res.status });
    }

    return NextResponse.json(data, { headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    return NextResponse.json(
      {
        error: 'Unable to reach chatbot API.',
        detail: error instanceof Error ? error.message : 'Unknown network error'
      },
      { status: 502 }
    );
  }
}
