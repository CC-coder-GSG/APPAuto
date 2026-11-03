import { api } from '../api.js';

// Read back both parts of a composite write; never repeat either POST or PUT.
export async function verifyBugResult({ bugId, ztId, majorId, softwareId, minorId, done, resolution }) {
  if (!majorId && !softwareId) return null;
  const data = await (await api(`/overall-test/overview?major_version_id=${majorId || 0}${softwareId ? `&software_id=${softwareId}` : ''}`)).json();
  const row = data.bug_pool?.find(item => Number(item.id) === Number(bugId));
  if (!row || Number(row.my_minor_version_id) !== Number(minorId) || row.my_test_done !== done || row.my_resolution !== resolution) return null;
  if (ztId) {
    const bug = await (await api(`/zentao/bugs/${Number(ztId)}/preview`)).json();
    if (bug.status !== (done ? 'closed' : 'active')) return null;
  }
  return true;
}
