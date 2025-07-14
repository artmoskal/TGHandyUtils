"""Check existing users in database."""

from database.connection import DatabaseManager

db = DatabaseManager('data/db/users.db')

with db.get_connection() as conn:
    # Check users from recipients
    cursor = conn.execute('''
        SELECT DISTINCT user_id 
        FROM recipients 
        WHERE user_id > 0
        ORDER BY user_id
    ''')
    users = cursor.fetchall()
    
    print(f"Found {len(users)} unique users in recipients table")
    print("User IDs:", [u[0] for u in users])
    
    # Check if we store any usernames
    cursor = conn.execute('''
        SELECT user_id, name, platform_config 
        FROM recipients 
        WHERE platform_config IS NOT NULL 
        LIMIT 5
    ''')
    
    print("\nSample recipient data:")
    for row in cursor.fetchall():
        print(f"User {row[0]}: {row[1]} - Config: {row[2][:50]}...")