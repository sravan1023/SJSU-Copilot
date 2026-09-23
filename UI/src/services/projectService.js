import { supabase } from '../supabaseClient';

// -- Projects CRUD --------------------------------------------------------

/**
 * Fetch all projects for the current user, sorted by most recent.
 */
export async function fetchProjects() {
  const { data, error } = await supabase
    .from('projects')
    .select('id, name, description, created_at, updated_at')
    .order('updated_at', { ascending: false });
  if (error) throw error;
  return data;
}

/**
 * Create a new project.
 */
export async function createProject(userId, name, description = null) {
  const { data, error } = await supabase
    .from('projects')
    .insert({ user_id: userId, name, description })
    .select()
    .single();
  if (error) throw error;
  return data;
}

/**
 * Rename a project.
 */
export async function renameProject(projectId, name) {
  const { data, error } = await supabase
    .from('projects')
    .update({ name })
    .eq('id', projectId)
    .select()
    .single();
  if (error) throw error;
  return data;
}

/**
 * Delete a project. Conversations are unlinked (project_id set to null), not deleted.
 */
export async function deleteProject(projectId) {
  const { error } = await supabase
    .from('projects')
    .delete()
    .eq('id', projectId);
  if (error) throw error;
}

// -- Project ↔ Conversation linking ---------------------------------------

/**
 * Fetch conversations that belong to a specific project.
 */
export async function fetchProjectConversations(projectId) {
  const { data, error } = await supabase
    .from('conversations')
    .select('id, title, last_message_preview, updated_at, project_id, audience')
    .eq('project_id', projectId)
    .order('updated_at', { ascending: false });
  if (error) throw error;
  return data;
}

/**
 * Move a conversation into a project.
 */
export async function assignConversationToProject(conversationId, projectId) {
  const { error } = await supabase
    .from('conversations')
    .update({ project_id: projectId })
    .eq('id', conversationId);
  if (error) throw error;
}

/**
 * Remove a conversation from its project (make it standalone again).
 */
export async function unassignConversationFromProject(conversationId) {
  const { error } = await supabase
    .from('conversations')
    .update({ project_id: null })
    .eq('id', conversationId);
  if (error) throw error;
}
