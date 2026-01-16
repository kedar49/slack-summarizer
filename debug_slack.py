import os
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Test script to debug Slack connection
def test_slack_connection():
    slack_token = os.environ.get('SLACK_USER_TOKEN')
    
    if not slack_token:
        print("❌ SLACK_USER_TOKEN not found in environment variables")
        return False
    
    print(f"✅ Token found: {slack_token[:15]}...")
    print(f"   Token length: {len(slack_token)}")
    print(f"   Token starts with: {slack_token[:5]}")
    
    if not slack_token.startswith('xoxp-'):
        print("⚠️  WARNING: Token should start with 'xoxp-' for user tokens")
        print("   You might be using a bot token instead")
        return False
    
    client = WebClient(token=slack_token)
    
    # Test auth
    try:
        print("\n🔐 Testing authentication...")
        auth_response = client.auth_test()
        print(f"✅ Authentication successful!")
        print(f"   User: {auth_response['user']}")
        print(f"   Team: {auth_response['team']}")
        print(f"   User ID: {auth_response['user_id']}")
    except SlackApiError as e:
        print(f"❌ Authentication failed: {e}")
        return False
    
    # Test channel listing
    try:
        print("\n📢 Fetching channels...")
        response = client.conversations_list(
            types="public_channel,private_channel",
            exclude_archived=True,
            limit=100
        )
        
        channels = response['channels']
        print(f"✅ Total channels in workspace: {len(channels)}")
        
        member_channels = [c for c in channels if c.get('is_member', False)]
        print(f"✅ Channels you're a member of: {len(member_channels)}")
        
        if member_channels:
            print("\n📋 Your channels:")
            for i, ch in enumerate(member_channels[:10], 1):
                print(f"   {i}. #{ch['name']} (ID: {ch['id']})")
            if len(member_channels) > 10:
                print(f"   ... and {len(member_channels) - 10} more")
        else:
            print("\n⚠️  You're not a member of any channels!")
            print("   Join some channels in Slack and try again")
            return False
            
    except SlackApiError as e:
        print(f"❌ Error fetching channels: {e}")
        return False
    
    # Test message fetching
    if member_channels:
        test_channel = member_channels[0]
        print(f"\n💬 Testing message fetch from #{test_channel['name']}...")
        
        try:
            from datetime import datetime, timedelta
            oldest = (datetime.now() - timedelta(days=7)).timestamp()
            
            msg_response = client.conversations_history(
                channel=test_channel['id'],
                oldest=str(oldest),
                limit=10
            )
            
            messages = msg_response['messages']
            print(f"✅ Found {len(messages)} messages in last 7 days")
            
            if messages:
                print("\n📝 Sample message:")
                sample = messages[0]
                print(f"   User: {sample.get('user', 'N/A')}")
                print(f"   Text: {sample.get('text', 'N/A')[:100]}")
            else:
                print("⚠️  No messages in the last 7 days in this channel")
                print("   Try increasing the time range to 30 days")
                
        except SlackApiError as e:
            print(f"❌ Error fetching messages: {e}")
            return False
    
    print("\n" + "="*60)
    print("✅ All tests passed! Your Slack connection is working.")
    print("="*60)
    return True

if __name__ == "__main__":
    print("="*60)
    print("🔍 SLACK CONNECTION DEBUG TEST")
    print("="*60 + "\n")
    
    test_slack_connection()
