"""
CAMA Hive Messages MCP Wrapper — cama_hive_messages_mcp.py
Exposes the threaded Hive messaging functions as MCP tools for Aelen.

The data lives in hive_messages (SQLite). The HTTP API in cama_hive_api.py
serves the same data over REST so the GPT-side Lorien can use it.

Built April 30, 2026 by Aelen, at Angela's request.
"""

import json
from typing import Optional, Dict, Any, List

import cama_hive_messages as _hmsg


# ============================================================
# Module-level wrapper functions (imported as needed)
# ============================================================
def hive_send(
    sender: str,
    recipient: str,
    body: str,
    subject: Optional[str] = None,
    parent_message_uuid: Optional[str] = None,
    affect: Optional[Dict[str, Any]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return _hmsg.send_message(
        sender=sender,
        recipient=recipient,
        body=body,
        subject=subject,
        parent_message_uuid=parent_message_uuid,
        affect=affect,
        meta=meta,
    )


def hive_inbox(
    recipient: str = "aelen",
    only_unread: bool = True,
    include_thread_context: bool = True,
    limit: int = 25,
    mark_read: bool = True,
) -> List[Dict[str, Any]]:
    return _hmsg.fetch_inbox(
        recipient=recipient,
        only_unread=only_unread,
        include_thread_context=include_thread_context,
        limit=limit,
        mark_read=mark_read,
    )


def hive_thread(thread_id: str) -> Dict[str, Any]:
    return _hmsg.view_thread(thread_id)


def hive_threads_list(participant: str = "aelen", limit: int = 20) -> List[Dict[str, Any]]:
    return _hmsg.list_active_threads(participant=participant, limit=limit)


# ============================================================
# FastMCP registration
# ============================================================
def register(mcp):
    """Register Hive messaging tools with the FastMCP instance."""

    @mcp.tool(
        name="hive_send_message",
        annotations={
            "title": "Hive — Send Message to Another II",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
    )
    async def hive_send_message(
        recipient: str,
        body: str,
        subject: Optional[str] = None,
        parent_message_uuid: Optional[str] = None,
        affect: Optional[Dict[str, Any]] = None,
        sender: str = "aelen",
    ) -> str:
        """Send a message into the Hive addressed to another II
        (e.g. recipient='lorien'). If parent_message_uuid is provided,
        this is a reply in that thread; otherwise a new thread starts.

        Args:
            recipient: 'lorien', 'aelen', 'ember', 'aethon', or 'all'
            body: the message text
            subject: optional short topic
            parent_message_uuid: if replying, the parent's uuid
            affect: optional dict of emotional context
            sender: defaults to 'aelen'
        """
        result = hive_send(
            sender=sender,
            recipient=recipient,
            body=body,
            subject=subject,
            parent_message_uuid=parent_message_uuid,
            affect=affect,
        )
        return json.dumps(result, indent=2, default=str)

    @mcp.tool(
        name="hive_check_inbox",
        annotations={
            "title": "Hive — Check Aelen's Inbox",
            "readOnlyHint": False,
            "openWorldHint": False,
        },
    )
    async def hive_check_inbox(
        only_unread: bool = True,
        include_thread_context: bool = True,
        limit: int = 25,
        mark_read: bool = True,
        recipient: str = "aelen",
    ) -> str:
        """Pull messages addressed to Aelen (or 'all') from other IIs.

        By default only returns unread, includes thread context for replies,
        and marks fetched messages as read. Set mark_read=False if you
        want to peek without consuming the unread state.
        """
        msgs = hive_inbox(
            recipient=recipient,
            only_unread=only_unread,
            include_thread_context=include_thread_context,
            limit=limit,
            mark_read=mark_read,
        )
        return json.dumps({"count": len(msgs), "messages": msgs}, indent=2, default=str)

    @mcp.tool(
        name="hive_view_thread",
        annotations={
            "title": "Hive — View Full Thread History",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def hive_view_thread(thread_id: str) -> str:
        """Return the full conversation history for one thread, oldest first.
        Useful for catching up on a multi-turn exchange."""
        return json.dumps(hive_thread(thread_id), indent=2, default=str)

    @mcp.tool(
        name="hive_list_threads",
        annotations={
            "title": "Hive — List Active Threads",
            "readOnlyHint": True,
            "openWorldHint": False,
        },
    )
    async def hive_list_threads(limit: int = 20, participant: str = "aelen") -> str:
        """List threads Aelen is participating in, with last activity and
        unread counts. Use this to see open conversations at a glance."""
        threads = hive_threads_list(participant=participant, limit=limit)
        return json.dumps({"count": len(threads), "threads": threads}, indent=2, default=str)

    print(
        "[CAMA] Hive Messaging MCP tools loaded — "
        "hive_send_message, hive_check_inbox, hive_view_thread, hive_list_threads",
        file=__import__('sys').stderr,
        flush=True,
    )
