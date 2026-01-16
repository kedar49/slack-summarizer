import os
import json
from datetime import datetime, timedelta
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import google.generativeai as genai
import time

class SlackSummarizer:
    def __init__(self):
        self.slack_token = os.environ.get('SLACK_USER_TOKEN')
        self.gemini_key = os.environ.get('GEMINI_API_KEY')
        
        if not self.slack_token or not self.gemini_key:
            raise ValueError("Missing required environment variables")
        
        self.client = WebClient(token=self.slack_token)
        genai.configure(api_key=self.gemini_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')
        self.user_cache = {}
        
    def get_channels(self):
        """Get list of all channels the user is a member of"""
        all_channels = []
        try:
            # Get public channels
            cursor = None
            while True:
                response = self.client.conversations_list(
                    types="public_channel,private_channel",
                    exclude_archived=True,
                    limit=200,
                    cursor=cursor
                )
                all_channels.extend(response['channels'])
                
                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor:
                    break
                    
            print(f"Total channels found: {len(all_channels)}")
            # Filter to only channels the user is a member of
            member_channels = [c for c in all_channels if c.get('is_member', False)]
            print(f"Channels you're a member of: {len(member_channels)}")
            return member_channels
            
        except SlackApiError as e:
            print(f"Error fetching channels: {e}")
            return []
    
    def get_user_name(self, user_id):
        """Get user's display name with caching"""
        if user_id in self.user_cache:
            return self.user_cache[user_id]
            
        try:
            response = self.client.users_info(user=user_id)
            user = response['user']
            name = user['profile'].get('display_name') or user['profile'].get('real_name') or user['name']
            self.user_cache[user_id] = name
            return name
        except SlackApiError:
            self.user_cache[user_id] = user_id
            return user_id
    
    def fetch_messages(self, channel_id, days_back=7):
        """Fetch messages from the last N days with all metadata"""
        oldest = (datetime.now() - timedelta(days=days_back)).timestamp()
        messages = []
        
        try:
            cursor = None
            while True:
                response = self.client.conversations_history(
                    channel=channel_id,
                    oldest=str(oldest),
                    limit=200,
                    cursor=cursor
                )
                messages.extend(response['messages'])
                
                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor or len(messages) > 2000:  # Safety limit
                    break
                    
        except SlackApiError as e:
            print(f"  Error fetching messages: {e}")
        
        return messages
    
    def fetch_thread_replies(self, channel_id, thread_ts):
        """Fetch replies in a thread"""
        try:
            response = self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=100
            )
            # Skip the first message (it's the parent)
            return response['messages'][1:] if len(response['messages']) > 1 else []
        except SlackApiError as e:
            print(f"  Error fetching thread: {e}")
            return []
    
    def format_file_info(self, file_data):
        """Format file/attachment information"""
        name = file_data.get('name', 'Unnamed file')
        title = file_data.get('title', '')
        filetype = file_data.get('filetype', 'file')
        size = file_data.get('size', 0)
        
        # Convert bytes to readable format
        if size > 1024*1024:
            size_str = f"{size/(1024*1024):.1f}MB"
        elif size > 1024:
            size_str = f"{size/1024:.1f}KB"
        else:
            size_str = f"{size}B"
        
        info = f"📎 {title or name} ({filetype}, {size_str})"
        
        # Add preview if available
        if file_data.get('preview'):
            preview = file_data['preview'][:200]
            info += f"\n   Preview: {preview}..."
            
        return info
    
    def format_messages(self, messages, channel_name, channel_id):
        """Format messages into readable text with all context"""
        if not messages:
            return None
            
        formatted = f"# Channel: {channel_name}\n\n"
        
        # Sort by timestamp
        messages.sort(key=lambda x: float(x.get('ts', 0)))
        
        for msg in messages:
            # Skip system messages
            if msg.get('subtype') in ['channel_join', 'channel_leave', 'channel_archive']:
                continue
            
            user_id = msg.get('user', msg.get('bot_id', 'Unknown'))
            username = self.get_user_name(user_id)
            text = msg.get('text', '')
            timestamp = datetime.fromtimestamp(float(msg.get('ts', 0))).strftime('%Y-%m-%d %H:%M')
            
            # Main message
            formatted += f"\n[{timestamp}] **{username}**: {text}\n"
            
            # Add file attachments
            if msg.get('files'):
                for file_data in msg['files']:
                    formatted += f"  {self.format_file_info(file_data)}\n"
            
            # Add other attachments (links, etc)
            if msg.get('attachments'):
                for att in msg['attachments']:
                    if att.get('title'):
                        formatted += f"  🔗 {att['title']}\n"
                    if att.get('text'):
                        formatted += f"     {att['text'][:150]}...\n"
            
            # Add reactions
            if msg.get('reactions'):
                reactions = ', '.join([f"{r['name']}({r['count']})" for r in msg['reactions']])
                formatted += f"  Reactions: {reactions}\n"
            
            # Handle threaded replies
            if msg.get('reply_count', 0) > 0 and not msg.get('thread_ts'):
                thread_replies = self.fetch_thread_replies(channel_id, msg['ts'])
                if thread_replies:
                    formatted += f"  💬 Thread ({len(thread_replies)} replies):\n"
                    for reply in thread_replies[:5]:  # Limit to first 5 replies
                        reply_user = self.get_user_name(reply.get('user', 'Unknown'))
                        reply_text = reply.get('text', '')[:100]
                        formatted += f"    ↳ {reply_user}: {reply_text}\n"
                    if len(thread_replies) > 5:
                        formatted += f"    ↳ ... and {len(thread_replies)-5} more replies\n"
        
        return formatted
    
    def summarize_with_gemini(self, text, channel_name, message_count):
        """Summarize text using Gemini API with comprehensive prompt"""
        prompt = f"""You are analyzing a Slack channel's activity. Provide a comprehensive, detailed summary.

Channel: #{channel_name}
Total Messages: {message_count}
Time Period: Last 7 days

Discussion Content:
{text}

Please provide a detailed summary with these sections:

## 📊 Overview
- Brief description of the channel's main purpose and activity level

## 💬 Key Discussion Topics
- List all major topics discussed (be specific)
- Include context and details for each topic

## 👥 Active Participants
- Who were the main contributors?
- What were their key contributions?

## 📁 Shared Files & Resources
- List all documents, files, or links shared
- Summarize their purpose/content

## ✅ Decisions & Action Items
- Any decisions made?
- Action items or tasks assigned?
- Deadlines mentioned?

## 🔔 Important Announcements
- Any significant announcements or updates?

## 💡 Key Insights
- Important takeaways from the discussions
- Emerging patterns or trends

Be detailed and specific. Include names, dates, and context where available."""

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 3
                    print(f"  Retry {attempt + 1}/{max_retries} after {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    return f"⚠️ Error generating summary: {str(e)}\n\nRaw message count: {message_count}"
    
    def run(self, days_back=7, output_file='summaries/weekly_summary.md'):
        """Main execution function"""
        print(f"\n{'='*60}")
        print(f"🚀 Starting Slack Summarization")
        print(f"📅 Period: Last {days_back} days")
        print(f"{'='*60}\n")
        
        channels = self.get_channels()
        
        if not channels:
            print("❌ No channels found. Please check:")
            print("   1. Your Slack token is correct")
            print("   2. You're a member of some channels")
            print("   3. The app has the right permissions")
            return
        
        all_summaries = []
        date_range = f"{(datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')} to {datetime.now().strftime('%Y-%m-%d')}"
        
        # Header
        all_summaries.append(f"# 📊 Slack Weekly Summary Report\n\n")
        all_summaries.append(f"**📅 Period:** {date_range}\n")
        all_summaries.append(f"**🕐 Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        all_summaries.append(f"**📢 Channels Analyzed:** {len(channels)}\n\n")
        all_summaries.append("---\n\n")
        
        channels_processed = 0
        total_messages = 0
        
        for idx, channel in enumerate(channels, 1):
            channel_name = channel['name']
            channel_id = channel['id']
            
            print(f"[{idx}/{len(channels)}] Processing #{channel_name}...")
            
            messages = self.fetch_messages(channel_id, days_back)
            
            if not messages:
                print(f"  ⚠️  No messages found")
                continue
            
            print(f"  ✅ Found {len(messages)} messages")
            
            formatted_text = self.format_messages(messages, channel_name, channel_id)
            
            # Only skip if truly empty
            if not formatted_text or len(formatted_text) < 50:
                print(f"  ⏭️  Skipping - no meaningful content")
                continue
            
            # Truncate if needed for API limits
            if len(formatted_text) > 25000:
                print(f"  ⚠️  Truncating content (too long)")
                formatted_text = formatted_text[:25000] + "\n\n... (content truncated for API limits)"
            
            print(f"  🤖 Generating AI summary...")
            summary = self.summarize_with_gemini(formatted_text, channel_name, len(messages))
            
            # Add to final output
            all_summaries.append(f"# 📢 #{channel_name}\n\n")
            all_summaries.append(f"**Message Count:** {len(messages)}\n\n")
            all_summaries.append(f"{summary}\n\n")
            all_summaries.append("---\n\n")
            
            channels_processed += 1
            total_messages += len(messages)
            
            # Rate limiting
            time.sleep(3)
        
        # Add summary footer
        all_summaries.append(f"\n## 📈 Summary Statistics\n\n")
        all_summaries.append(f"- **Total Channels Processed:** {channels_processed}\n")
        all_summaries.append(f"- **Total Messages Analyzed:** {total_messages}\n")
        all_summaries.append(f"- **Average Messages per Channel:** {total_messages/channels_processed if channels_processed > 0 else 0:.1f}\n")
        
        # Save to file
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(all_summaries)
        
        print(f"\n{'='*60}")
        print(f"✅ Summary saved to: {output_file}")
        print(f"📊 Processed {channels_processed} channels with {total_messages} total messages")
        print(f"{'='*60}\n")
        
        return output_file

if __name__ == "__main__":
    summarizer = SlackSummarizer()
    summarizer.run(days_back=7)
