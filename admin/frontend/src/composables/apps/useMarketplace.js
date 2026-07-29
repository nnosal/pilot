import { ref, computed } from 'vue'
import { appsApi } from '@/api/apps'
import { settingsApi } from '@/api/settings'
import { sitesApi } from '@/api/sites'
import { parseBranchVersion, toSentenceCase } from '@/utils/format'
import { matchesPill } from '@/utils/marketplaceCategories'

const COLORS = ['#4f46e5', '#0891b2', '#059669', '#d97706', '#dc2626', '#7c3aed']

export function logoColor(name) {
  let hash = 0
  for (const char of name) hash = (hash * 31 + char.charCodeAt(0)) | 0
  return COLORS[Math.abs(hash) % COLORS.length]
}

function parseVersion(branch) {
  const match = /^version-(\d+)/.exec(branch || '')
  return match ? Number(match[1]) : null
}

function parseBenchBranch(branch) {
  const version = parseVersion(branch)
  if (version !== null) return { version, label: `v${version}` }
  if (branch === 'develop') return { version: null, label: 'Nightly' }
  return { version: null, label: parseBranchVersion(branch) || null }
}

export function isFrappeApp(app) {
  return Boolean(app.repo?.includes('github.com/frappe/'))
}

export function repoOwner(repo) {
  const match = /^(?:https?:\/\/|git@)[^/:]+[/:]([^/]+)\//.exec(repo || '')
  return match ? match[1].toLowerCase() : ''
}

function sortApps(a, b) {
  if (a.installed !== b.installed) return a.installed ? -1 : 1
  const as = a.stars ?? -1
  const bs = b.stars ?? -1
  if (as !== bs) return bs - as
  return (a.title || a.name).localeCompare(b.title || b.name)
}

export function useMarketplace(initialSiteName = '') {
  const registry = ref([])
  const benchName = ref('')
  const benchVersion = ref(null)
  const benchVersionLabel = ref(null)
  const loading = ref(true)
  const error = ref('')

  const search = ref('')
  const selectedPill = ref('All')
  const worksWith = ref('')
  const compatibleOnly = ref(true)

  const sites = ref([])
  const currentSiteName = ref('')
  const benchApps = ref([])
  const frameworkRepo = ref('')

  async function load() {
    loading.value = true
    error.value = ''
    try {
      const [registryData, installed, settings, siteList] = await Promise.all([
        appsApi.marketplace(),
        appsApi.installed(),
        settingsApi.get(),
        sitesApi.list(),
      ])
      registry.value = registryData.filter((app) => app.name !== 'frappe')
      benchApps.value = installed
      benchName.value = settings.bench?.name || 'this bench'
      frameworkRepo.value = settings.bench?.framework_repo || ''
      const benchBranch =
        parseBenchBranch(settings.bench?.default_branch) ||
        parseBenchBranch(installed.find((app) => app.name === 'frappe')?.branch)
      benchVersion.value = benchBranch.version
      benchVersionLabel.value = benchBranch.label

      sites.value = siteList.filter((site) => site.exists && !site.broken)
      if (currentSiteName.value) {
        if (!sites.value.some((site) => site.name === currentSiteName.value)) currentSiteName.value = ''
      } else if (initialSiteName) {
        currentSiteName.value = sites.value.find((site) => site.name === initialSiteName)?.name || ''
      }
    } catch (caught) {
      error.value = caught.message || 'Failed to load marketplace'
    } finally {
      loading.value = false
    }
  }

  const currentSite = computed(() => sites.value.find((site) => site.name === currentSiteName.value) || null)
  const installedOnCurrentSite = computed(() => new Set(currentSite.value?.installed_apps || []))

  // Only Frappe-made apps that some marketplace app depends on.
  const worksWithOptions = computed(() => {
    const names = new Set(registry.value.flatMap((app) => Object.keys(app.dependencies || {})))
    return [...names]
      .map((name) => registry.value.find((app) => app.name === name))
      .filter((entry) => entry && isFrappeApp(entry))
      .map((entry) => ({ name: entry.name, title: entry.title, logo_url: entry.logo_url || '' }))
      .sort((a, b) => a.title.localeCompare(b.title))
  })

  function matchesWorksWith(app) {
    return !worksWith.value || Object.hasOwn(app.dependencies || {}, worksWith.value)
  }

  function matchesSearch(app, query) {
    return (
      !query ||
      app.title?.toLowerCase().includes(query) ||
      app.description?.toLowerCase().includes(query)
    )
  }

  const matchingApps = computed(() => {
    const query = search.value.toLowerCase().trim()
    return registry.value
      .filter((app) => matchesPill(app, selectedPill.value))
      .filter(matchesWorksWith)
      .filter((app) => matchesSearch(app, query))
      .filter((app) => !compatibleOnly.value || app.is_installable)
      .map((app) => ({
        ...app,
        installed: installedOnCurrentSite.value.has(app.name),
        compatible: app.is_installable,
        needs: app.required_version,
        // Backend falls back to an arbitrary target's version when nothing
        // matches this bench's Frappe version (pilot/integrations/marketplace.py
        // read_all_apps) - that version isn't meaningful here, showing it next
        // to the title reads as "this is the version you'd get". Only label
        // apps this bench can actually install; the incompatible-app dialog
        // already surfaces the real requirement via `needs`.
        label: app.is_installable && app.version ? `v${app.version}` : '',
      }))
  })

  const isFiltered = computed(() => selectedPill.value !== 'All' || Boolean(worksWith.value))
  const filteredApps = computed(() => [...matchingApps.value].sort(sortApps))

  // A fork bench (dodock, ...) gets its own section: apps published by the same
  // owner as the framework repo are that distribution's, not third-party.
  const forkOwner = computed(() => {
    const owner = repoOwner(frameworkRepo.value)
    return owner === 'frappe' ? '' : owner
  })
  const forkLabel = computed(() => toSentenceCase(forkOwner.value))

  const isForkApp = (app) => Boolean(forkOwner.value) && repoOwner(app.repo) === forkOwner.value

  const frappeApps = computed(() => matchingApps.value.filter(isFrappeApp).sort(sortApps))
  const forkApps = computed(() => matchingApps.value.filter(isForkApp).sort(sortApps))
  const communityApps = computed(() =>
    matchingApps.value.filter((app) => !isFrappeApp(app) && !isForkApp(app)).sort(sortApps),
  )

  const registryNames = computed(() => new Set(registry.value.map((app) => app.name)))
  const otherBenchApps = computed(() =>
    benchApps.value
      .filter((app) => app.name !== 'frappe' && !registryNames.value.has(app.name))
      .map((app) => ({
        name: app.name,
        title: toSentenceCase(app.title || app.name),
        description: app.description,
        compatible: true,
        inBench: true,
      })),
  )

  return {
    loading,
    error,
    search,
    selectedPill,
    worksWith,
    compatibleOnly,
    worksWithOptions,
    isFiltered,
    filteredApps,
    benchName,
    benchVersion,
    benchVersionLabel,
    frappeApps,
    forkApps,
    forkLabel,
    communityApps,
    load,
    sites,
    currentSiteName,
    benchApps,
    otherBenchApps,
  }
}
