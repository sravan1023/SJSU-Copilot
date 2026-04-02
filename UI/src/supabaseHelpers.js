import { supabase } from './supabaseClient';

//  Auth helpers

export const getCurrentUser = async () => {
  const { data: { user } } = await supabase.auth.getUser();
  return user;
};

export const signOut = () => supabase.auth.signOut();

//  Profile

export const getProfile = async (userId) => {
  const { data, error } = await supabase
    .from('profiles')
    .select('*')
    .eq('id', userId)
    .single();
  return { data, error };
};

export const updateProfile = async (userId, updates) => {
  const { data, error } = await supabase
    .from('profiles')
    .update(updates)
    .eq('id', userId)
    .select()
    .single();
  return { data, error };
};

// Ensure a profile row exists for the given auth user.
// Prevents duplicates: checks by id first, then by email (Use Case: a user signs up with email/password and later
// logs in with Google using the same @sjsu.edu address).

export const ensureProfile = async (user) => {
  if (!user?.id) return null;

  // 1. Check if profile already exists by auth user id
  const { data: byId } = await supabase
    .from('profiles')
    .select('*')
    .eq('id', user.id)
    .single();

  if (byId) return byId;

  // 2. Check if a profile exists for the same email (linked to a
  //    different auth identity -- e.g. email/password vs Google OAuth).
  const { data: byEmail } = await supabase
    .from('profiles')
    .select('*')
    .eq('email', user.email)
    .single();

  if (byEmail) return byEmail;

  // 3. No profile exists — create one
  const meta = user.user_metadata || {};
  const fullName = meta.full_name || meta.name || '';

  const { data, error } = await supabase
    .from('profiles')
    .insert({
      id: user.id,
      email: user.email,
      full_name: fullName,
    })
    .select()
    .single();

  if (error) console.error('ensureProfile insert error:', error.message);
  return data;
};

// Conversations

export const getConversations = async (userId) => {
  const { data, error } = await supabase
    .from('saved_conversations')
    .select('*')
    .eq('user_id', userId)
    .order('updated_at', { ascending: false });
  return { data, error };
};

export const createConversation = async (userId, title = 'New Conversation') => {
  const { data, error } = await supabase
    .from('saved_conversations')
    .insert({ user_id: userId, title })
    .select()
    .single();
  return { data, error };
};

export const deleteConversation = async (conversationId) => {
  const { error } = await supabase
    .from('saved_conversations')
    .delete()
    .eq('id', conversationId);
  return { error };
};

// Chat messages

export const getMessages = async (conversationId) => {
  const { data, error } = await supabase
    .from('chat_messages')
    .select('*')
    .eq('conversation_id', conversationId)
    .order('created_at', { ascending: true });
  return { data, error };
};

export const insertMessage = async ({ conversationId, userId, role, content }) => {
  const { data, error } = await supabase
    .from('chat_messages')
    .insert({
      conversation_id: conversationId,
      user_id: userId,
      role,
      content,
    })
    .select()
    .single();
  return { data, error };
};

// Uploaded documents

export const getUserUploads = async (userId) => {
  const { data, error } = await supabase
    .from('uploaded_documents')
    .select('*')
    .eq('owner_id', userId)
    .order('created_at', { ascending: false });
  return { data, error };
};

export const insertUploadRecord = async ({ ownerId, fileName, storagePath, mimeType, sizeBytes }) => {
  const { data, error } = await supabase
    .from('uploaded_documents')
    .insert({
      owner_id: ownerId,
      file_name: fileName,
      storage_path: storagePath,
      mime_type: mimeType,
      size_bytes: sizeBytes,
    })
    .select()
    .single();
  return { data, error };
};

// RAG: semantic search

export const matchDocuments = async (queryEmbedding, matchCount = 5, matchThreshold = 0.78) => {
  const { data, error } = await supabase
    .rpc('match_documents', {
      query_embedding: queryEmbedding,
      match_count: matchCount,
      match_threshold: matchThreshold,
    });
  return { data, error };
};

// Job Fetcher

export const getJobSources = async () => {
  const { data, error } = await supabase
    .from('job_sources')
    .select('*')
    .order('created_at', { ascending: false });
  return { data, error };
};

export const createJobSource = async (source) => {
  const { data, error } = await supabase
    .from('job_sources')
    .insert(source)
    .select('id')
    .maybeSingle();
  return { data, error };
};

export const updateJobSource = async (sourceId, updates) => {
  const { data, error } = await supabase
    .from('job_sources')
    .update(updates)
    .eq('id', sourceId)
    .select('id')
    .maybeSingle();
  return { data, error };
};

export const getJobListings = async ({ limit = 250 } = {}) => {
  const { data, error } = await supabase
    .from('job_listings')
    .select('*')
    .order('posted_date', { ascending: false, nullsFirst: false })
    .limit(limit);
  return { data, error };
};

export const getExistingJobMatches = async ({ applyUrls = [], dedupeHashes = [] } = {}) => {
  if (!applyUrls.length && !dedupeHashes.length) {
    return { data: [], error: null };
  }

  const applyUrlList = applyUrls.filter(Boolean);
  const hashList = dedupeHashes.filter(Boolean);

  const rowsById = new Map();

  if (applyUrlList.length) {
    const { data, error } = await supabase
      .from('job_listings')
      .select('id, apply_url, dedupe_hash')
      .in('apply_url', applyUrlList);

    if (error) return { data: null, error };
    (data || []).forEach((row) => rowsById.set(row.id, row));
  }

  if (hashList.length) {
    const { data, error } = await supabase
      .from('job_listings')
      .select('id, apply_url, dedupe_hash')
      .in('dedupe_hash', hashList);

    if (error) return { data: null, error };
    (data || []).forEach((row) => rowsById.set(row.id, row));
  }

  return { data: Array.from(rowsById.values()), error: null };
};

export const insertJobListings = async (rows) => {
  if (!rows?.length) return { data: [], error: null };

  const toDateOnly = (value) => {
    if (!value) return null;

    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
      return value;
    }

    let parsed = null;

    if (typeof value === 'number' || /^\d+$/.test(`${value}`)) {
      const n = Number(value);
      if (!Number.isFinite(n)) return null;
      // Treat 10-digit as epoch seconds, 13-digit as epoch milliseconds.
      parsed = new Date(n < 1e12 ? n * 1000 : n);
    } else {
      parsed = new Date(value);
    }

    if (Number.isNaN(parsed.getTime())) return null;
    return parsed.toISOString().split('T')[0];
  };

  const uniqueByApplyUrl = new Map();
  rows.forEach((row) => {
    const key = `${row?.apply_url || ''}`.trim().toLowerCase();
    if (!key) return;
    if (!uniqueByApplyUrl.has(key)) {
      uniqueByApplyUrl.set(key, row);
    }
  });

  const sanitizedRows = Array.from(uniqueByApplyUrl.values()).map((row) => {
    const copy = { ...row };
    delete copy.id;
    delete copy.dedupe_hash;
    copy.posted_date = toDateOnly(copy.posted_date);
    return copy;
  });

  const { data, error } = await supabase
    .from('job_listings')
    .upsert(sanitizedRows, { onConflict: 'apply_url', ignoreDuplicates: true })
    .select('id, apply_url, dedupe_hash');
  return { data, error };
};

export const saveJobForUser = async (userId, jobId) => {
  const { data, error } = await supabase
    .from('user_saved_jobs')
    .upsert({ user_id: userId, job_id: jobId }, { onConflict: 'user_id,job_id', ignoreDuplicates: true })
    .select();
  return { data, error };
};

export const getUserSavedJobs = async (userId) => {
  const { data, error } = await supabase
    .from('user_saved_jobs')
    .select('*')
    .eq('user_id', userId);
  return { data, error };
};

export const upsertJobApplicationStatus = async ({ userId, jobId, status, appliedAt = null, notes = null }) => {
  const { data, error } = await supabase
    .from('user_job_applications')
    .upsert(
      {
        user_id: userId,
        job_id: jobId,
        status,
        applied_at: appliedAt,
        notes,
      },
      { onConflict: 'user_id,job_id' },
    )
    .select();
  return { data, error };
};

export const getUserJobApplications = async (userId) => {
  const { data, error } = await supabase
    .from('user_job_applications')
    .select('*')
    .eq('user_id', userId);
  return { data, error };
};

export const createJobFetchRun = async ({ sourceId, status, startedAt }) => {
  const { data, error } = await supabase
    .from('job_fetch_runs')
    .insert({
      source_id: sourceId,
      status,
      started_at: startedAt,
    })
    .select('id')
    .maybeSingle();
  return { data, error };
};

export const updateJobFetchRun = async (runId, updates) => {
  const { error } = await supabase
    .from('job_fetch_runs')
    .update(updates)
    .eq('id', runId);
  return { data: null, error };
};

export const touchJobSourceAfterFetch = async ({
  sourceId,
  success,
  intervalMinutes = 360,
  errorMessage = null,
}) => {
  const now = new Date();
  const next = new Date(now.getTime() + intervalMinutes * 60 * 1000);

  const updates = {
    last_fetched_at: now.toISOString(),
    next_fetch_at: next.toISOString(),
    last_error: success ? null : errorMessage,
  };

  return updateJobSource(sourceId, updates);
};
