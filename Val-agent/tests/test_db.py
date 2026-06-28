import unittest

from val_agent.db import connect, create_conversation, get_recent_chat_context, init_db, save_chat_message


class DbTests(unittest.TestCase):
    def test_recent_chat_context_is_bounded_and_ordered(self):
        conn = connect(":memory:")
        init_db(conn)
        conversation_id = create_conversation(conn, "user", "AP_REVIEWER")
        for index in range(5):
            save_chat_message(conn, conversation_id, "USER", f"message {index}")

        context = get_recent_chat_context(conn, conversation_id, limit=3)

        self.assertEqual([item["message"] for item in context], ["message 2", "message 3", "message 4"])


if __name__ == "__main__":
    unittest.main()
