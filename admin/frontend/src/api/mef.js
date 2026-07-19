import { request } from './client'

export const mefApi = {
  listProjects: () => request.get('mef/projects').json(),
  createProject: (payload) => request.post('mef/projects', { json: payload }).json(),
  deleteProject: (name) => request.delete(`mef/projects/${encodeURIComponent(name)}`).json(),
  getJob: (jobId) => request.get(`mef/jobs/${encodeURIComponent(jobId)}`).json(),
}
