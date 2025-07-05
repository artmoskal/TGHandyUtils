"""Telegram message and callback factories for testing handlers."""

import factory
from datetime import datetime
from .base import BaseFactory, TestDataMixin, SimpleObject


class TelegramUserFactory(BaseFactory, TestDataMixin):
    """Factory for Telegram user objects."""
    
    class Meta:
        model = SimpleObject
    
    id = factory.LazyFunction(lambda: TestDataMixin.random_user_id())
    is_bot = False
    first_name = factory.Faker('first_name')
    last_name = factory.Faker('last_name')
    username = factory.Faker('user_name')
    language_code = 'en'


class TelegramChatFactory(BaseFactory, TestDataMixin):
    """Factory for Telegram chat objects."""
    
    class Meta:
        model = SimpleObject
    
    id = factory.LazyFunction(lambda: TestDataMixin.random_user_id())
    type = 'private'
    first_name = factory.Faker('first_name')
    username = factory.Faker('user_name')


class TelegramMessageFactory(BaseFactory, TestDataMixin):
    """Factory for Telegram message objects."""
    
    class Meta:
        model = SimpleObject
    
    message_id = factory.Sequence(lambda n: n + 10000)
    from_user = factory.SubFactory(TelegramUserFactory)
    chat = factory.SubFactory(TelegramChatFactory)
    date = factory.LazyFunction(lambda: int(datetime.now().timestamp()))
    text = factory.Faker('sentence')


class CallbackQueryFactory(BaseFactory, TestDataMixin):
    """Factory for Telegram callback query objects."""
    
    class Meta:
        model = SimpleObject
    
    id = factory.Faker('uuid4')
    from_user = factory.SubFactory(TelegramUserFactory)
    message = factory.SubFactory(TelegramMessageFactory)
    data = factory.Iterator([
        'recipient_edit_1', 
        'recipient_edit_2', 
        'add_shared_task_1',
        'add_shared_recipient',
        'platform_type_todoist',
        'platform_type_trello',
        'trello_board_12345',
        'trello_list_67890'
    ])


class FSMStateDataFactory(BaseFactory, TestDataMixin):
    """Factory for FSM state data objects."""
    
    class Meta:
        model = SimpleObject
    
    # Common state data patterns
    mode = factory.Iterator(['user_platform', 'shared_recipient'])
    platform_type = factory.Iterator(['todoist', 'trello'])
    recipient_name = factory.Faker('company')
    credentials = factory.LazyAttribute(
        lambda obj: TestDataMixin.random_platform_credentials(obj.platform_type)
    )


class SharedRecipientStateFactory(FSMStateDataFactory):
    """Factory for shared recipient state data."""
    
    mode = 'shared_recipient'
    recipient_name = factory.LazyAttribute(
        lambda obj: f"{factory.Faker('company').generate()} (Shared)"
    )


class PersonalRecipientStateFactory(FSMStateDataFactory):
    """Factory for personal recipient state data."""
    
    mode = 'user_platform'
    recipient_name = factory.LazyAttribute(
        lambda obj: f"My {obj.platform_type.title()}"
    )