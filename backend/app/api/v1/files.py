from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_allow_query
from app.db.session import get_db_session
from app.models.user import User
from app.schemas.file import FileRead
from app.services.conversation import ConversationService
from app.services.file_service import FileService

router = APIRouter(tags=["files"])


@router.post("/files/pending", response_model=FileRead, status_code=status.HTTP_201_CREATED)
async def upload_pending_file(
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FileRead:
    # 新聊天预上传：文件写入临时区，conversation_id=NULL，24h 后过期。
    return await FileService(session).upload_pending_file(user_id=current_user.id, file=file)


@router.post(
    "/conversations/{conversation_id}/files",
    response_model=FileRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_conversation_file(
    conversation_id: UUID,
    file: Annotated[UploadFile, File(...)],
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FileRead:
    # 已有会话上传必须先校验 conversation 归属，避免用户把文件绑定到他人会话。
    await ConversationService(session).get_conversation(
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    return await FileService(session).upload_to_conversation(
        user_id=current_user.id, conversation_id=conversation_id, file=file
    )


@router.get("/files/{file_id}", response_model=FileRead)
async def get_file_metadata(
    file_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> FileRead:
    # 返回文件元数据，校验 user_id 归属，防止跨用户枚举。
    file_record = await FileService(session).repo.get_by_id(
        user_id=current_user.id, file_id=file_id
    )
    if file_record is None:
        from fastapi import HTTPException, status

        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return FileRead.model_validate(file_record)


@router.get("/files/{file_id}/content")
async def get_file_content(
    file_id: UUID,
    current_user: Annotated[User, Depends(get_current_user_allow_query)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> Response:
    # 下载或预览文件内容，校验 user_id 归属后以原始 MIME 类型返回。
    conv_file, content = await FileService(session).get_file_content(
        user_id=current_user.id, file_id=file_id
    )
    from urllib.parse import quote

    return Response(
        content=content,
        media_type=conv_file.file_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(conv_file.original_name)}",
        },
    )


@router.get("/conversations/{conversation_id}/files", response_model=list[FileRead])
async def list_conversation_files(
    conversation_id: UUID,
    current_user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[FileRead]:
    # 返回当前会话已绑定的文件列表，先校验会话归属再查询文件。
    await ConversationService(session).get_conversation(
        user_id=current_user.id, conversation_id=conversation_id
    )
    files = await FileService(session).repo.list_by_conversation(conversation_id=conversation_id)
    return [FileRead.model_validate(f) for f in files]
