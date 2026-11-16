import { api, type Session } from '../api/client'
import { act } from '../api/request'

/** Rename a session; on success hand the updated session back. Resolves the response status
 * (0 if Iris never answered), as TitleField expects. */
export async function renameSession(id: string, title: string, update: (session: Session) => void): Promise<number> {
  const { data, status } = await act(api.PATCH('/api/sessions/{session_id}', { params: { path: { session_id: id } }, body: { title } }))
  if (data) update(data)
  return status
}
