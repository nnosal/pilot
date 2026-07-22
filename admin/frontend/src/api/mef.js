import { request } from './client'

export const mefApi = {
  listProjects: () => request.get('mef/projects').json(),
  createProject: (payload) => request.post('mef/projects', { json: payload }).json(),
  deleteProject: (name) => request.delete(`mef/projects/${encodeURIComponent(name)}`).json(),
  resumeProject: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/resume`).json(),
  doctorProject: (name) =>
    request.get(`mef/projects/${encodeURIComponent(name)}/doctor`).json(),
  pilotUp: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/pilot-up`).json(),
  pilotDown: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/pilot-down`).json(),
  pilotOpen: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/pilot-open`).json(),
  selfPilotDown: () => request.post('mef/self/pilot-down').json(),
  listRegistry: () => request.get('mef/registry').json(),
  getJob: (jobId) => request.get(`mef/jobs/${encodeURIComponent(jobId)}`).json(),
  getAutoLoginToken: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/auto-login-token`).json(),
  getDbStatus: (name) =>
    request.get(`mef/projects/${encodeURIComponent(name)}/db-status`).json(),
  getProjectSites: (name) =>
    request.get(`mef/projects/${encodeURIComponent(name)}/sites`).json(),
  startService: (name, service) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/services/${service}/start`).json(),
  stopService: (name, service) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/services/${service}/stop`).json(),
  testMailpit: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/mailpit/test`).json(),
  getSetupStatus: (name, site) =>
    request
      .get(`mef/projects/${encodeURIComponent(name)}/sites/${encodeURIComponent(site)}/setup-status`)
      .json(),
  runWizard: (name, site) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/sites/${encodeURIComponent(site)}/wizard`).json(),
}
