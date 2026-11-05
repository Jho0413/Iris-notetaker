import { api, type Notes, type Transcript } from '../api/client'

/** A finished session's notes, or undefined when they can't be loaded. */
export async function fetchNotes(id: string): Promise<Notes | undefined> {
  const { data } = await api
    .GET('/api/sessions/{session_id}/notes', { params: { path: { session_id: id } } })
    .catch(() => ({ data: undefined }))
  return data
}

/** A session's transcript, or undefined when it can't be loaded. */
export async function fetchTranscript(id: string): Promise<Transcript | undefined> {
  const { data } = await api
    .GET('/api/sessions/{session_id}/transcript', { params: { path: { session_id: id } } })
    .catch(() => ({ data: undefined }))
  return data
}
