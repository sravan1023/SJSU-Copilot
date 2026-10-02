import { supabase } from './supabaseClient';

// Conversations & messages live in services/chatService.js.

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
