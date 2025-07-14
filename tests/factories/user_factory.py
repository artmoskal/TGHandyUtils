"""Factory for creating test User instances."""

import factory
from datetime import datetime
from models.user import User


class UserFactory(factory.Factory):
    """Factory for creating User instances."""
    
    class Meta:
        model = User
    
    user_id = factory.Sequence(lambda n: 100000 + n)
    username = factory.Sequence(lambda n: f"testuser{n}")
    first_name = factory.Faker('first_name')
    last_name = factory.Faker('last_name')
    last_seen = factory.LazyFunction(datetime.now)
    created_at = factory.LazyFunction(datetime.now)