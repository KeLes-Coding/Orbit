from app.services.conversations.base import ConversationBaseService, ConversationStreamEvent
from app.services.conversations.branch import ConversationBranchService
from app.services.conversations.crud import ConversationCrudService
from app.services.conversations.runs import ConversationRunService


# ConversationService 是对外 facade：调用方不用关心内部按 CRUD/branch/run/stream 拆分。
class ConversationService(
    ConversationRunService,
    ConversationCrudService,
    ConversationBranchService,
    ConversationBaseService,
):
    pass


__all__ = ["ConversationService", "ConversationStreamEvent"]
