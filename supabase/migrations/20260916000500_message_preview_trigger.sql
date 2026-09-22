-- Maintain conversations.last_message_preview in the database.
--
-- UI/src/services/chatService.js insertMessage does two serial round trips: it
-- inserts the message, then updates the parent conversation's preview and
-- updated_at. It is called twice per exchange, so a single turn costs four
-- round trips, two of them on the critical path before the model is even
-- asked.
--
-- A trigger does the same write inside the insert, halving that.
--
-- NOTE: the client still performs its own preview update until this migration
-- is confirmed applied. The two writes are identical, so running both is
-- harmless -- but do not remove the client-side update in chatService.js until
-- this is deployed, or previews will silently go stale.

create or replace function public.set_conversation_preview()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
  update public.conversations
     set last_message_preview = case
           when length(new.content) > 80 then left(new.content, 80) || '...'
           else new.content
         end,
         updated_at = now()
   where id = new.conversation_id;
  return new;
end;
$$;

drop trigger if exists trg_set_conversation_preview on public.messages;
create trigger trg_set_conversation_preview
after insert on public.messages
for each row
execute function public.set_conversation_preview();
