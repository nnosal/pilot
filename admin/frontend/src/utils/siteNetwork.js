// mef-specific: whether a site is reachable only over plain local HTTP, or has been
// registered with slim (nilbuild), mef's local-dev HTTPS proxy. Backed by the `slim`
// field _site_resource() computes from the project's own .env SLIM_DOMAINS.
export const siteNetworkLabel = (site) => (site.slim ? 'HTTPS · slim' : 'Local')
export const siteNetworkTheme = (site) => (site.slim ? 'blue' : 'gray')
