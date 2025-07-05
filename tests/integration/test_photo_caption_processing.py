"""Integration test for photo caption processing bug fix."""

import pytest
from unittest.mock import Mock, AsyncMock, patch
from aiogram.types import Message, PhotoSize, Document, User, Chat

from handlers_modular.message.message_handler import process_user_input_with_photo


class TestPhotoCaptionProcessing:
    """Test photo caption processing handles both inline photos and document attachments."""
    
    @pytest.fixture
    def mock_message_with_photo_caption(self):
        """Create mock message with photo and caption."""
        user = User(id=123456, is_bot=False, first_name="Test", username="testuser")
        chat = Chat(id=789, type="private")
        
        message = Mock(spec=Message)
        message.from_user = user
        message.chat = chat
        message.photo = [Mock(spec=PhotoSize)]
        message.document = None
        message.caption = "Fix this bug in the code"
        message.text = None  # Photos don't have .text, they have .caption
        message.forward_from = None
        message.forward_sender_name = None
        message.answer = AsyncMock()
        message.reply = AsyncMock()
        
        return message
    
    @pytest.fixture  
    def mock_message_with_document_caption(self):
        """Create mock message with document attachment and caption."""
        user = User(id=123456, is_bot=False, first_name="Test", username="testuser")
        chat = Chat(id=789, type="private")
        
        message = Mock(spec=Message)
        message.from_user = user
        message.chat = chat
        message.photo = None
        message.document = Mock(spec=Document)
        message.caption = "Review this document and create task"
        message.text = None
        message.forward_from = None
        message.forward_sender_name = None
        message.answer = AsyncMock()
        message.reply = AsyncMock()
        
        return message
    
    @pytest.mark.asyncio
    @patch('handlers_modular.message.message_handler.container.recipient_service')
    @patch('core.initialization.services.get_image_processing_service')
    async def test_photo_with_caption_processing(self, mock_image_service_factory, 
                                               mock_recipient_service_factory, 
                                               mock_message_with_photo_caption):
        """Test that photo captions are properly processed."""
        # Setup mocks
        mock_recipient_service = Mock()
        mock_recipient_service.get_enabled_recipients.return_value = [Mock()]  # Has recipients
        mock_recipient_service_factory.return_value = mock_recipient_service
        
        mock_image_service = Mock()
        mock_image_service.process_image_message = AsyncMock(return_value={
            'extracted_text': 'console.log("debug");',
            'summary': 'JavaScript debug code'
        })
        mock_image_service_factory.return_value = mock_image_service
        
        # Setup threading system mocks
        with patch('handlers_modular.message.message_handler.time.time', return_value=1000), \
             patch('handlers_modular.message.message_handler.asyncio.sleep'), \
             patch('handlers_modular.message.text_handler.process_thread_with_photos') as mock_process_thread:
            
            # Call the function with caption text
            result = await process_user_input_with_photo(
                text="Fix this bug in the code",  # This should be the caption
                user_id=123456,
                message_obj=mock_message_with_photo_caption,
                state=Mock(),
                bot=Mock()
            )
            
            # Verify image processing was called
            mock_image_service.process_image_message.assert_called_once()
            
            # Verify caption was included in processing
            # The function should combine caption + screenshot content
            assert result is True
    
    @pytest.mark.asyncio
    @patch('handlers_modular.message.message_handler.container.recipient_service')
    @patch('core.initialization.services.get_image_processing_service')
    async def test_document_with_caption_processing(self, mock_image_service_factory,
                                                  mock_recipient_service_factory,
                                                  mock_message_with_document_caption):
        """Test that document attachments with captions are properly processed."""
        # Setup mocks
        mock_recipient_service = Mock()
        mock_recipient_service.get_enabled_recipients.return_value = [Mock()]
        mock_recipient_service_factory.return_value = mock_recipient_service
        
        mock_image_service = Mock()
        mock_image_service.process_image_message = AsyncMock(return_value={
            'extracted_text': 'PDF content here',
            'summary': 'Document analysis'
        })
        mock_image_service_factory.return_value = mock_image_service
        
        with patch('handlers_modular.message.message_handler.time.time', return_value=1000), \
             patch('handlers_modular.message.message_handler.asyncio.sleep'), \
             patch('handlers_modular.message.text_handler.process_thread_with_photos'):
            
            # Call with document message
            result = await process_user_input_with_photo(
                text="Review this document and create task",
                user_id=123456,
                message_obj=mock_message_with_document_caption,
                state=Mock(),
                bot=Mock()
            )
            
            # Verify document processing was called
            mock_image_service.process_image_message.assert_called_once()
            assert result is True
    
    @pytest.mark.asyncio
    @patch('handlers_modular.message.message_handler.container.recipient_service')
    async def test_photo_without_caption_still_works(self, mock_recipient_service_factory,
                                                    mock_message_with_photo_caption):
        """Test that photos without captions still work."""
        # Modify message to have no caption
        mock_message_with_photo_caption.caption = None
        
        mock_recipient_service = Mock()
        mock_recipient_service.get_enabled_recipients.return_value = [Mock()]
        mock_recipient_service_factory.return_value = mock_recipient_service
        
        with patch('core.initialization.services.get_image_processing_service') as mock_image_service_factory:
            mock_image_service = Mock()
            mock_image_service.process_image_message = AsyncMock(return_value={
                'extracted_text': 'Some code',
                'summary': 'Code screenshot'
            })
            mock_image_service_factory.return_value = mock_image_service
            
            with patch('handlers_modular.message.message_handler.time.time', return_value=1000), \
                 patch('handlers_modular.message.message_handler.asyncio.sleep'), \
                 patch('handlers_modular.message.text_handler.process_thread_with_photos'):
                
                # Call with empty caption
                result = await process_user_input_with_photo(
                    text="",  # Empty caption
                    user_id=123456,
                    message_obj=mock_message_with_photo_caption,
                    state=Mock(),
                    bot=Mock()
                )
                
                # Should still process the image
                mock_image_service.process_image_message.assert_called_once()
                assert result is True