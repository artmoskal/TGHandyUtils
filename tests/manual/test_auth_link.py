"""Manual test for auth link generation."""

from utils.auth_link_utils import encode_auth_request_data, decode_auth_request_data


def test_auth_link_encoding():
    """Test encoding and decoding of auth request data."""
    
    # Test data
    test_data = {
        'requester_user_id': 123456,
        'platform_type': 'todoist',
        'recipient_name': 'Work Tasks',
        'requester_name': 'Alice'
    }
    
    # Encode
    encoded = encode_auth_request_data(**test_data)
    print(f"Encoded: {encoded}")
    print(f"Length: {len(encoded)} chars")
    
    # Decode
    decoded = decode_auth_request_data(encoded)
    print(f"\nDecoded: {decoded}")
    
    # Verify
    assert decoded is not None
    assert decoded['requester_user_id'] == test_data['requester_user_id']
    assert decoded['platform_type'] == test_data['platform_type']
    assert decoded['recipient_name'] == test_data['recipient_name']
    assert decoded['requester_name'] == test_data['requester_name']
    
    print("\n✅ Encoding/decoding works correctly!")
    
    # Generate sample bot link
    bot_username = "delmebot_bot"
    bot_link = f"https://t.me/{bot_username}?start=auth_{encoded}"
    print(f"\nSample bot link:\n{bot_link}")
    print(f"Link length: {len(bot_link)} chars")


if __name__ == "__main__":
    test_auth_link_encoding()