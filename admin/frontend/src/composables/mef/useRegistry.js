import { ref, computed } from 'vue'
import { apiErrorMessage } from '@/api/client'
import { mefApi } from '@/api/mef'

// Singleton state: the Registry page and the header ProjectSwitcher share
// one fetch so opening the dropdown doesn't re-scan the mef root.
const projects = ref([])
const mefRoot = ref('')
const loading = ref(false)
const controlLoading = ref('')
const error = ref('')

// Detect projects sharing the same pilot_port (collision risk)
const portCollisions = computed(() => {
  const portMap = {}
  const collisions = new Set()
  projects.value.forEach(p => {
    if (!p.pilot_port) return
    if (portMap[p.pilot_port]) {
      collisions.add(p.pilot_port)
      collisions.add(portMap[p.pilot_port])
    } else {
      portMap[p.pilot_port] = p.name
    }
  })
  return collisions
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await mefApi.listRegistry()
    projects.value = data.projects || []
    mefRoot.value = data.mef_root || ''
  } catch (caught) {
    error.value = caught.message || 'Could not load registry.'
    projects.value = []
  } finally {
    loading.value = false
  }
}

// Start/stop a project's service (pilot/app/redis/db), then re-scan until
// the registry's services[service] status flips. Exponential backoff avoids
// both a stale badge (fixed short delay) and hammering the scan endpoint.
async function runControl(name, service, action) {
  error.value = ''
  controlLoading.value = `${name}:${service}`
  try {
    const dispatch = action === 'start' ? mefApi.startService : mefApi.stopService
    const result = await dispatch(name, service)
    if (result?.error) {
      error.value = apiErrorMessage(result)
      return false
    }
    const delays = [1000, 2000, 4000, 8000, 16000] // max 31s
    for (const delay of delays) {
      await new Promise(r => setTimeout(r, delay))
      await load()
      const state = projects.value.find(p => p.name === name)?.services?.[service]
      if (action === 'start' && state === 'running') break
      if (action === 'stop' && state !== 'running') break
    }
    await refreshProjectDetail(name)
    return true
  } catch (caught) {
    error.value = caught.message || `Could not ${action} ${service}.`
    return false
  } finally {
    if (controlLoading.value === `${name}:${service}`) controlLoading.value = ''
  }
}

function startPilot(name) {
  return runControl(name, 'pilot', 'start')
}

function stopPilot(name) {
  return runControl(name, 'pilot', 'stop')
}

function startService(name, service) {
  return runControl(name, service, 'start')
}

function stopService(name, service) {
  return runControl(name, service, 'stop')
}

// ----- expandable row: db status (not a pitchfork daemon) + sites, fetched
// lazily so opening the page doesn't spawn N subprocesses up front -----
const expandedProject = ref('')
const projectDetails = ref({})

function isExpanded(name) {
  return expandedProject.value === name
}

async function toggleExpand(name) {
  expandedProject.value = expandedProject.value === name ? '' : name
  if (expandedProject.value === name && !projectDetails.value[name]?.loaded) {
    await refreshProjectDetail(name)
  }
}

async function refreshProjectDetail(name) {
  // Only fetch for the project currently expanded (or already loaded once) —
  // a control action on a collapsed row shouldn't trigger a detail fetch.
  if (expandedProject.value !== name && !projectDetails.value[name]) return
  projectDetails.value = {
    ...projectDetails.value,
    [name]: { ...projectDetails.value[name], loading: true },
  }
  try {
    const [dbResult, sitesResult] = await Promise.all([
      mefApi.getDbStatus(name),
      mefApi.getProjectSites(name),
    ])
    const sites = sitesResult?.sites || []
    const siteSetup = await _fetchSiteSetup(name, sites)
    projectDetails.value = {
      ...projectDetails.value,
      [name]: {
        loaded: true,
        loading: false,
        dbStatus: dbResult?.status || 'unknown',
        sites,
        siteSetup,
      },
    }
  } catch {
    projectDetails.value = {
      ...projectDetails.value,
      [name]: { loaded: true, loading: false, dbStatus: 'unknown', sites: [], siteSetup: {} },
    }
  }
}

// Setup-wizard status is per-site, so fetch it once the sites list is known —
// one call per site, in parallel.
async function _fetchSiteSetup(name, sites) {
  const results = await Promise.all(sites.map((site) => mefApi.getSetupStatus(name, site.name)))
  return Object.fromEntries(sites.map((site, i) => [site.name, results[i]?.setup_complete ?? null]))
}

export function useRegistry() {
  return {
    projects,
    mefRoot,
    loading,
    controlLoading,
    error,
    portCollisions,
    expandedProject,
    projectDetails,
    load,
    startPilot,
    stopPilot,
    startService,
    stopService,
    isExpanded,
    toggleExpand,
    refreshProjectDetail,
  }
}
