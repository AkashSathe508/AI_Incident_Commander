import pytest
from unittest.mock import patch, MagicMock

from app.reasoning.integrations.jira_tool import create_jira_issue
from app.reasoning.integrations.slack_tool import post_slack_message

def test_jira_tool_mock_mode():
    """Test that Jira tool falls back to mock mode if credentials are missing."""
    # Ensure no env/settings credentials
    with patch("app.reasoning.integrations.jira_tool.settings") as mock_settings, \
         patch("app.reasoning.integrations.jira_tool.os.environ.get", return_value=""):
        mock_settings.jira_domain = ""
        mock_settings.jira_user_email = ""
        mock_settings.jira_api_token = ""
        
        result = create_jira_issue(
            summary="Test mock issue", 
            priority="high",
            project_key="TEST"
        )
        
        assert result["mock"] is True
        assert result["status"] == "created"
        assert result["issue_key"] == "TEST-MOCK"
        assert "demo.atlassian.net" in result["url"]

@patch("app.reasoning.integrations.jira_tool.httpx.Client.post")
def test_jira_tool_api_call(mock_post):
    """Test that Jira tool makes correct API call when credentials are provided via kwargs."""
    # Setup mock response
    mock_resp = MagicMock()
    mock_resp.status_code = 201
    mock_resp.json.return_value = {"id": "1000", "key": "INC-123"}
    mock_post.return_value = mock_resp
    
    result = create_jira_issue(
        summary="Database is down",
        description="Detailed description",
        priority="urgent",
        issue_type="Bug",
        domain="test.atlassian.net",
        email="test@example.com",
        token="fake-token",
        project_key="INC"
    )
    
    assert result["mock"] is False
    assert result["status"] == "created"
    assert result["issue_key"] == "INC-123"
    
    # Verify API request
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://test.atlassian.net/rest/api/3/issue"
    
    # Check basic auth header
    assert "Authorization" in kwargs["headers"]
    
    # Check payload
    payload = kwargs["json"]
    assert payload["fields"]["project"]["key"] == "INC"
    assert payload["fields"]["summary"] == "Database is down"
    assert payload["fields"]["issuetype"]["name"] == "Bug"
    assert payload["fields"]["priority"]["name"] == "Highest"

def test_slack_tool_mock_mode():
    """Test that Slack tool falls back to mock mode if credentials missing."""
    with patch("app.reasoning.integrations.slack_tool.settings") as mock_settings, \
         patch("app.reasoning.integrations.slack_tool.os.environ.get", return_value=""):
        mock_settings.slack_webhook_url = ""
        mock_settings.slack_bot_token = ""
        
        result = post_slack_message(
            message="Test mock message",
            channel="#test-channel"
        )
        
        assert result["mock"] is True
        assert result["status"] == "posted"
        assert result["channel"] == "#test-channel"

@patch("app.reasoning.integrations.slack_tool.httpx.Client.post")
def test_slack_tool_webhook_call(mock_post):
    """Test Slack tool via webhook URL."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_post.return_value = mock_resp
    
    result = post_slack_message(
        message="Critical alert",
        channel="#alerts",
        webhook_url="https://hooks.slack.com/services/T000/B000/XXXX"
    )
    
    assert result["mock"] is False
    assert result["status"] == "posted"
    
    # Verify API request
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "https://hooks.slack.com/services/T000/B000/XXXX"
    assert kwargs["json"]["text"] == "Critical alert"
    assert kwargs["json"]["channel"] == "#alerts"
