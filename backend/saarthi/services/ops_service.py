"""Tickets and merchant communication."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.enums import (
    Actor,
    MessageChannel,
    MessageDirection,
    MessageStatus,
    TicketPriority,
    TicketStatus,
)
from ..database.ids import next_id
from ..database.models import Message, Ticket
from .errors import invalid_state, not_found


async def get_ticket(session: AsyncSession, ticket_id: str) -> Ticket:
    ticket = await session.get(Ticket, ticket_id)
    if ticket is None:
        raise not_found("Ticket", ticket_id)
    return ticket


async def find_ticket_by_idempotency_key(session: AsyncSession, key: str) -> Ticket | None:
    return await session.scalar(select(Ticket).where(Ticket.idempotency_key == key))


async def create_ticket(
    session: AsyncSession,
    *,
    case_id: str | None,
    subject: str,
    description: str,
    priority: TicketPriority = TicketPriority.NORMAL,
    idempotency_key: str | None = None,
) -> Ticket:
    if idempotency_key:
        existing = await find_ticket_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return existing
    ticket = Ticket(
        id=await next_id(session, "ticket"),
        case_id=case_id,
        status=TicketStatus.OPEN,
        priority=priority,
        subject=subject,
        description=description,
        idempotency_key=idempotency_key,
    )
    session.add(ticket)
    await session.flush()
    return ticket


async def update_ticket(session: AsyncSession, ticket_id: str, *, status: TicketStatus) -> Ticket:
    ticket = await get_ticket(session, ticket_id)
    if ticket.status == TicketStatus.CLOSED:
        raise invalid_state(f"Ticket {ticket_id} is closed")
    ticket.status = status
    await session.flush()
    return ticket


async def draft_message(
    session: AsyncSession,
    *,
    case_id: str,
    content: str,
    channel: MessageChannel = MessageChannel.CHAT,
    meta: dict | None = None,
) -> Message:
    message = Message(
        id=await next_id(session, "message"),
        case_id=case_id,
        direction=MessageDirection.OUTBOUND,
        channel=channel,
        sender=Actor.SAARTHI,
        content=content,
        status=MessageStatus.DRAFT,
        meta=meta,
    )
    session.add(message)
    await session.flush()
    return message


async def send_message(session: AsyncSession, message_id: str) -> Message:
    message = await session.get(Message, message_id)
    if message is None:
        raise not_found("Message", message_id)
    if message.status == MessageStatus.SENT:
        return message
    message.status = MessageStatus.SENT
    await session.flush()
    return message


async def get_message(session: AsyncSession, message_id: str) -> Message:
    message = await session.get(Message, message_id)
    if message is None:
        raise not_found("Message", message_id)
    return message


async def record_inbound_message(
    session: AsyncSession,
    *,
    case_id: str,
    content: str,
    channel: MessageChannel = MessageChannel.CHAT,
    sender: Actor = Actor.MERCHANT,
) -> Message:
    message = Message(
        id=await next_id(session, "message"),
        case_id=case_id,
        direction=MessageDirection.INBOUND,
        channel=channel,
        sender=sender,
        content=content,
        status=MessageStatus.SENT,
    )
    session.add(message)
    await session.flush()
    return message


async def list_messages(session: AsyncSession, case_id: str) -> list[Message]:
    result = await session.scalars(
        select(Message).where(Message.case_id == case_id).order_by(Message.created_at, Message.id)
    )
    return list(result)
