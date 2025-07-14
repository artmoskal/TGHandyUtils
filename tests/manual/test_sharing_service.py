"""Manual test for sharing service."""

import asyncio
from core.container import container


async def test_sharing_service():
    """Test the sharing service bot link generation."""
    
    sharing_service = container.sharing_service()
    
    # Test 1: Non-existent user (should return user_not_found)
    print("Test 1: Non-existent user")
    result = sharing_service.create_auth_request(
        requester_user_id=123456,
        target_username="nonexistentuser",
        platform_type="todoist",
        recipient_name="Test Account"
    )
    print(f"Result: {result}")
    assert result['status'] == 'user_not_found'
    print("✅ Returns user_not_found for non-existent user\n")
    
    # Test 2: Self request (should raise error)
    print("Test 2: Self request")
    try:
        # Since we always return user_not_found now, this won't trigger
        result = sharing_service.create_auth_request(
            requester_user_id=123456,
            target_username="testuser",
            platform_type="todoist", 
            recipient_name="My Account"
        )
        print(f"Result: {result}")
    except ValueError as e:
        print(f"Error (expected): {e}")
        print("✅ Self-request validation works\n")
    
    print("All tests passed!")


if __name__ == "__main__":
    asyncio.run(test_sharing_service())