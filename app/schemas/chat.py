from app.schemas.common import DianaBaseModel
from app.schemas.messages import MessageCreate, MessageRead


class ChatRequest(MessageCreate):
    pass


class ChatResponse(DianaBaseModel):
    user_message: MessageRead
    diana_message: MessageRead
