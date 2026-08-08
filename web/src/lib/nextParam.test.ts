import { describe, it, expect } from 'vitest';
import { safeNext } from './nextParam';

describe('safeNext', () => {
  it('accepts a same-origin relative path', () => {
    expect(safeNext('/oauth/authorize?client_id=x&redirect_uri=y')).toBe(
      '/oauth/authorize?client_id=x&redirect_uri=y',
    );
  });

  it('accepts a bare root path', () => {
    expect(safeNext('/')).toBe('/');
  });

  it('rejects an absolute URL with a scheme', () => {
    expect(safeNext('https://evil.com')).toBeNull();
  });

  it('rejects an absolute URL disguised with a path', () => {
    expect(safeNext('https://evil.com/oauth/authorize')).toBeNull();
  });

  it('rejects a protocol-relative URL', () => {
    expect(safeNext('//evil.com')).toBeNull();
  });

  it('rejects a backslash-based protocol-relative trick', () => {
    expect(safeNext('/\\evil.com')).toBeNull();
  });

  it('rejects a javascript: scheme', () => {
    expect(safeNext('javascript:alert(1)')).toBeNull();
  });

  it('rejects a scheme without slashes', () => {
    expect(safeNext('mailto:a@b.com')).toBeNull();
  });

  it('rejects a path without a leading slash', () => {
    expect(safeNext('oauth/authorize')).toBeNull();
  });

  it('rejects an empty string', () => {
    expect(safeNext('')).toBeNull();
  });

  it('rejects null', () => {
    expect(safeNext(null)).toBeNull();
  });

  it('rejects undefined', () => {
    expect(safeNext(undefined)).toBeNull();
  });
});
