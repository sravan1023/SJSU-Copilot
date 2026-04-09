from pydantic import BaseModel
from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from services.llm import stream_chat, generate_title
from services.conversation_state import analyze_conversation_state, adapt_behavior

router = APIRouter(tags=["chat"])


class Message(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[Message]
    model: str = "8b"
    behavior: dict | None = None
    memory_prompt: str | None = None


class TitleRequest(BaseModel):
    message: str


@router.post("/chat")
async def chat(req: ChatRequest):
    messages = [m.model_dump() for m in req.messages]

    # Adapt behavior based on conversation state
    behavior = req.behavior
    if behavior:
        state = analyze_conversation_state(messages)
        behavior = adapt_behavior(behavior, state)

    return StreamingResponse(
        stream_chat(
            messages=messages,
            model=req.model,
            behavior=behavior,
            memory_prompt=req.memory_prompt,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/generate-title")
async def title(req: TitleRequest):
    result = await generate_title(req.message)
    return {"title": result}
