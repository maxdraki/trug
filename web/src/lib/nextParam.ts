// Guards the `?next=` redirect target used to send a user back to (e.g.) the
// OAuth /oauth/authorize consent screen after signing in. Only a same-origin,
// relative path is ever trusted — anything with a scheme or that is
// protocol-relative (starts with "//") is an open-redirect vector and must
// be rejected.
export function safeNext(raw: string | null | undefined): string | null {
  if (!raw) return null;
  if (!raw.startsWith('/')) return null;
  if (raw.startsWith('//')) return null;
  // Guards against constructs like "/\evil.com" or "/\/evil.com" that some
  // browsers normalize backslashes into forward slashes for.
  if (raw.startsWith('/\\')) return null;
  return raw;
}
