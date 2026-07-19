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

// Start/stop post a job then re-scan so the new pilot_running state surfaces
// once the daemon answers /api/v1/health. Returns true on accepted dispatch.
async function runControl(name, action, label) {
  error.value = ''
  controlLoading.value = name
  try {
    const result = await action()
    if (result?.error) {
      error.value = apiErrorMessage(result)
      return false
    }
    // Exponential backoff poll: reload registry until pilot_running flips.
    // This avoids the stale "stopped" badge that fixed 1.5s setTimeout caused.
    const delays = [1000, 2000, 4000, 8000, 16000] // max 31s
    for (const delay of delays) {
      await new Promise(r => setTimeout(r, delay))
      await load()
      const project = projects.value.find(p => p.name === name)
      if (label === 'start' && project?.pilot_running) return true
      if (label === 'stop' && !project?.pilot_running) return true
    }
    // Timeout: show final state anyway
    return true
  } catch (caught) {
    error.value = caught.message || `Could not ${label} pilot.`
    return false
  } finally {
    if (controlLoading.value === name) controlLoading.value = ''
  }
}

function startPilot(name) {
  return runControl(name, () => mefApi.pilotUp(name), 'start')
}

function stopPilot(name) {
  return runControl(name, () => mefApi.pilotDown(name), 'stop')
}

export function useRegistry() {
  return { projects, mefRoot, loading, controlLoading, error, portCollisions, load, startPilot, stopPilot }
}
