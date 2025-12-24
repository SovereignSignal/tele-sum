"""
Manually find actual URLs for messages without links.
"""
import sys
sys.path.insert(0, '~/Documents/GitHub/tele-sum')

from sqlalchemy import text
from src.models import database
from src.models.models import Message

def get_messages_without_urls():
    """Get messages that have no extracted URLs"""
    database.get_engine()
    db = database.SessionLocal()

    try:
        # Use raw SQL for JSON comparison
        result = db.execute(text("""
            SELECT id, raw_content
            FROM messages
            WHERE extracted_urls IS NULL
               OR extracted_urls::text = '[]'
               OR extracted_urls::text = 'null'
            ORDER BY id
            LIMIT 30
        """))

        rows = result.fetchall()
        print(f"Found messages without URLs:\n")

        for row in rows:
            msg_id, content = row
            print(f"ID: {msg_id}")
            print(f"Content: {content[:250]}...")
            print("-" * 60)
            print()

    finally:
        db.close()

if __name__ == "__main__":
    get_messages_without_urls()
