export const SITE_STATUS = {
  online: { label: 'Active', theme: 'green' },
  broken: { label: 'Broken', theme: 'red' },
  offline: { label: 'Paused', theme: 'orange' },
  provisioning: { label: 'Creating', theme: 'blue' },
}

export function siteStatus(site) {
  // Provisioning wins over "offline": the site dir/site_config.json may not
  // exist yet in the earliest moments of a new-site/reinstall task.
  if (site.provisioning) return 'provisioning'
  if (!site.exists) return 'offline'
  if (site.broken) return 'broken'
  return 'online'
}

export const siteStatusLabel = (site) => SITE_STATUS[siteStatus(site)].label
export const siteStatusTheme = (site) => SITE_STATUS[siteStatus(site)].theme
