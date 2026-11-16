import { api, type Notes, type Transcript } from '../api/client'
import { act } from '../api/request'

/** A finished session's notes, or undefined when they can't be loaded. */
export async function fetchNotes(id: string): Promise<Notes | undefined> {
  return (await act(api.GET('/api/sessions/{session_id}/notes', { params: { path: { session_id: id } } }))).data
}

/** A session's transcript, or undefined when it can't be loaded. */
export async function fetchTranscript(id: string): Promise<Transcript | undefined> {
  return (await act(api.GET('/api/sessions/{session_id}/transcript', { params: { path: { session_id: id } } }))).data
}
