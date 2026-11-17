// A Stage app is still an immutable app version, not a separate definition.
export function publishedRoute(pathname) {
  return pathname.match(/^\/(?:apps|d)\/([^/]+)(?:\/versions\/(\d+))?\/?$/)
    || pathname.match(/^\/api\/apps\/([^/]+)\/embed\/(\d+)\/?$/)
    || pathname.match(/^\/calliope\/artifacts\/([^/]+)\/versions\/(\d+)\/?$/);
}
