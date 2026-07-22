import { request } from './client'

export const shareApi = {
  ngrokStatus: () => request.get('share/ngrok').json(),
  ngrokConnect: (token) => request.put('share/ngrok', { json: { token } }).json(),
  ngrokDisconnect: () => request.delete('share/ngrok'),
  slimStatus: () => request.get('share/slim').json(),
  slimLogin: () => request.post('share/slim/login').json(),
  slimLoginStatus: () => request.get('share/slim/login').json(),
  slimDisconnect: () => request.delete('share/slim'),
}
