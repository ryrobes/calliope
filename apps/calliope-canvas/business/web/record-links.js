// Links carry record identity only. The server resolves navigation and SQL.
export function readRecord(hash, slug) {
  const raw = new URLSearchParams(hash.replace(/^#/, '')).get('record');
  if (!raw) return {};
  try {
    const ref = JSON.parse(raw);
    if (!ref || Object.keys(ref).sort().join(',') !== 'id,record,slug'
      || ref.slug !== slug || !/^[a-zA-Z0-9_-]{1,160}$/.test(ref.record)
      || !(typeof ref.id === 'string' && ref.id.length > 0 && ref.id.length <= 512 || Number.isSafeInteger(ref.id))) throw Error();
    return {reference: ref};
  } catch { return {error: 'This record link is invalid. No record was opened.'}; }
}

export function recordHref(reference) {
  if (!reference || !/^[a-zA-Z0-9_-]{1,160}$/.test(reference.slug)) return null;
  const hash = '#'+new URLSearchParams({record:JSON.stringify(reference)});
  return readRecord(hash, reference.slug).reference ? `/apps/${encodeURIComponent(reference.slug)}${hash}` : null;
}
