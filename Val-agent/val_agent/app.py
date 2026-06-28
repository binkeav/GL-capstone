from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable

from val_agent.db import connect, create_conversation, get_recent_chat_context, init_db, save_chat_message
from val_agent.graph import run_validation_graph


EXIT_WORDS = {"bye", "done", "exit", "quit", "q", "stop", "goodbye", "see you"}


def is_exit_message(message: str) -> bool:
    return message.strip().lower() in EXIT_WORDS


def main() -> None:
    parser = argparse.ArgumentParser(description="Local chat-based invoice validation agent")
    parser.add_argument("--db", default="data/val_agent.sqlite3", help="SQLite database path")
    parser.add_argument("--message", help="Chat message. Include invoice JSON to validate.")
    parser.add_argument("--file", help="Read invoice JSON from file and validate it.")
    parser.add_argument("--user-id", default="demo_user")
    parser.add_argument("--role", default="AP_REVIEWER")
    args = parser.parse_args()

    conn = connect(args.db)
    init_db(conn)

    conversation_id = create_conversation(conn, args.user_id, args.role)

    if args.file:
        _run_turn(conn, conversation_id, args.user_id, args.role, Path(args.file).read_text())
        return
    if args.message:
        _run_turn(conn, conversation_id, args.user_id, args.role, args.message)
        return

    print("Val Agent: Hi, I can help validate invoices. Paste invoice JSON, ask about a case, or type 'bye' to exit.")
    while True:
        try:
            message = _read_interactive_message()
        except (EOFError, KeyboardInterrupt):
            print("\nVal Agent: Bye. See you when the next invoice needs a second pair of eyes.")
            break
        if is_exit_message(message):
            print("Val Agent: Done. Closing this chat.")
            break
        if not message.strip():
            continue
        _run_turn(conn, conversation_id, args.user_id, args.role, message)


def _read_interactive_message(input_func: Callable[[str], str] = input) -> str:
    first_line = input_func("You: ")
    if not _looks_like_json_start(first_line):
        return first_line

    lines = [first_line]
    while not _is_complete_json("\n".join(lines)):
        next_line = input_func("... ")
        lines.append(next_line)
    return "\n".join(lines)


def _looks_like_json_start(message: str) -> bool:
    stripped = message.lstrip()
    return stripped.startswith("{") or stripped.startswith("[")


def _is_complete_json(message: str) -> bool:
    try:
        json.loads(message)
    except json.JSONDecodeError:
        return False
    return True


def _run_turn(
    conn,
    conversation_id: str,
    user_id: str,
    user_role: str,
    message: str,
) -> None:
    save_chat_message(conn, conversation_id, "USER", message)
    chat_context = get_recent_chat_context(conn, conversation_id)

    state = run_validation_graph(
        conn,
        {
            "conversation_id": conversation_id,
            "user_id": user_id,
            "user_role": user_role,
            "user_message": message,
            "chat_context": chat_context,
            "final_state": "PENDING",
            "rule_results": [],
            "errors": [],
        },
        progress=_print_progress,
    )
    save_chat_message(
        conn,
        conversation_id,
        "ASSISTANT",
        state["assistant_response"],
        invoice_id=state.get("invoice_id"),
        intent=state.get("intent"),
    )
    print(f"Val Agent: {state['assistant_response']}")


def _print_progress(message: str) -> None:
    print(f"Val Agent: Working: {message}", flush=True)


if __name__ == "__main__":
    main()
