import { request } from './client'

export const mefApi = {
  listProjects: () => request.get('mef/projects').json(),
  createProject: (payload) => request.post('mef/projects', { json: payload }).json(),
  deleteProject: (name) => request.delete(`mef/projects/${encodeURIComponent(name)}`).json(),
  pilotUp: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/pilot-up`).json(),
  pilotDown: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/pilot-down`).json(),
  pilotOpen: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/pilot-open`).json(),
  listRegistry: () => request.get('mef/registry').json(),
  getJob: (jobId) => request.get(`mef/jobs/${encodeURIComponent(jobId)}`).json(),
  getAutoLoginToken: (name) =>
    request.post(`mef/projects/${encodeURIComponent(name)}/auto-login-token`).json(),
}
