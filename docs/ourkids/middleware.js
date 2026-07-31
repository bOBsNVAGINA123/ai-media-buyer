import { next } from '@vercel/edge';

export const config = { matcher: '/(.*)' };

export default function middleware(request) {
  const USER = process.env.RIS_USER || '';
  const PASS = process.env.RIS_PASS || '';
  if (!USER || !PASS) return next();           // fail open only if unconfigured

  const auth = request.headers.get('authorization') || '';
  if (auth.startsWith('Basic ')) {
    let decoded = '';
    try { decoded = atob(auth.slice(6)); } catch (e) { decoded = ''; }
    const i = decoded.indexOf(':');
    if (i > -1) {
      const u = decoded.slice(0, i);
      const p = decoded.slice(i + 1);
      if (u === USER && p === PASS) return next();
    }
  }
  return new Response('Authentication required', {
    status: 401,
    headers: {
      'WWW-Authenticate': 'Basic realm="OurKids", charset="UTF-8"',
      'Content-Type': 'text/plain'
    }
  });
}
