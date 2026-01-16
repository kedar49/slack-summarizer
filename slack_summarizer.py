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
        self.user_cache = {}
        self.debug_log = []
        
        # Use gemini-2.0-flash-lite (fast and efficient)
        self.model = genai.GenerativeModel('gemini-2.0-flash-lite')
        self.log(f"✅ Using model: gemini-2.0-flash-lite")
        self.user_cache = {}
        self.debug_log = []
        
    def log(self, message):
        """Log messages for debugging"""
        print(message)
        self.debug_log.append(message)
        
    def get_channels(self):
        """Get list of all channels the user is a member of"""
        all_channels = []
        try:
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
                    
            self.log(f"Total channels found: {len(all_channels)}")
            member_channels = [c for c in all_channels if c.get('is_member', False)]
            self.log(f"Channels you're a member of: {len(member_channels)}")
            
            if member_channels:
                self.log("\nYour channels:")
                for ch in member_channels:
                    self.log(f"  - #{ch['name']} (ID: {ch['id']})")
            
            return member_channels
            
        except SlackApiError as e:
            self.log(f"Error fetching channels: {e}")
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
            page = 0
            while True:
                page += 1
                response = self.client.conversations_history(
                    channel=channel_id,
                    oldest=str(oldest),
                    limit=200,
                    cursor=cursor
                )
                batch = response['messages']
                messages.extend(batch)
                self.log(f"    Page {page}: fetched {len(batch)} messages")
                
                cursor = response.get('response_metadata', {}).get('next_cursor')
                if not cursor or len(messages) > 2000:
                    break
                    
        except SlackApiError as e:
            self.log(f"  Error fetching messages: {e}")
        
        return messages
    
    def fetch_thread_replies(self, channel_id, thread_ts):
        """Fetch replies in a thread"""
        try:
            response = self.client.conversations_replies(
                channel=channel_id,
                ts=thread_ts,
                limit=100
            )
            return response['messages'][1:] if len(response['messages']) > 1 else []
        except SlackApiError as e:
            self.log(f"  Error fetching thread: {e}")
            return []
    
    def format_file_info(self, file_data):
        """Format file/attachment information"""
        name = file_data.get('name', 'Unnamed file')
        title = file_data.get('title', '')
        filetype = file_data.get('filetype', 'file')
        size = file_data.get('size', 0)
        
        if size > 1024*1024:
            size_str = f"{size/(1024*1024):.1f}MB"
        elif size > 1024:
            size_str = f"{size/1024:.1f}KB"
        else:
            size_str = f"{size}B"
        
        info = f"📎 {title or name} ({filetype}, {size_str})"
        
        if file_data.get('preview'):
            preview = file_data['preview'][:200]
            info += f"\n   Preview: {preview}..."
            
        return info
    
    def format_messages(self, messages, channel_name, channel_id):
        """Format messages into readable text with all context"""
        if not messages:
            return None
            
        formatted = f"# Channel: {channel_name}\n\n"
        
        messages.sort(key=lambda x: float(x.get('ts', 0)))
        
        for msg in messages:
            if msg.get('subtype') in ['channel_join', 'channel_leave', 'channel_archive']:
                continue
            
            user_id = msg.get('user', msg.get('bot_id', 'Unknown'))
            username = self.get_user_name(user_id)
            text = msg.get('text', '')
            timestamp = datetime.fromtimestamp(float(msg.get('ts', 0))).strftime('%Y-%m-%d %H:%M')
            
            formatted += f"\n[{timestamp}] **{username}**: {text}\n"
            
            if msg.get('files'):
                for file_data in msg['files']:
                    formatted += f"  {self.format_file_info(file_data)}\n"
            
            if msg.get('attachments'):
                for att in msg['attachments']:
                    if att.get('title'):
                        formatted += f"  🔗 {att['title']}\n"
                    if att.get('text'):
                        formatted += f"     {att['text'][:150]}...\n"
            
            if msg.get('reactions'):
                reactions = ', '.join([f"{r['name']}({r['count']})" for r in msg['reactions']])
                formatted += f"  Reactions: {reactions}\n"
            
            if msg.get('reply_count', 0) > 0 and not msg.get('thread_ts'):
                thread_replies = self.fetch_thread_replies(channel_id, msg['ts'])
                if thread_replies:
                    formatted += f"  💬 Thread ({len(thread_replies)} replies):\n"
                    for reply in thread_replies[:5]:
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
                    self.log(f"  Retry {attempt + 1}/{max_retries} after {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    return f"⚠️ Error generating summary: {str(e)}\n\nRaw message count: {message_count}"
    
    def run(self, days_back=7, output_file='summaries/weekly_summary.md'):
        """Main execution function"""
        self.log(f"\n{'='*60}")
        self.log(f"🚀 Starting Slack Summarization")
        self.log(f"📅 Period: Last {days_back} days")
        self.log(f"{'='*60}\n")
        
        # ALWAYS create the output file
        all_summaries = []
        date_range = f"{(datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')} to {datetime.now().strftime('%Y-%m-%d')}"
        
        # Header
        all_summaries.append(f"# 📊 Slack Weekly Summary Report\n\n")
        all_summaries.append(f"**📅 Period:** {date_range}\n")
        all_summaries.append(f"**🕐 Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        channels = self.get_channels()
        
        all_summaries.append(f"**📢 Total Channels Found:** {len(channels)}\n\n")
        all_summaries.append("---\n\n")
        
        if not channels:
            self.log("❌ No channels found!")
            all_summaries.append("## ⚠️ No Channels Found\n\n")
            all_summaries.append("**Possible reasons:**\n")
            all_summaries.append("- You're not a member of any channels\n")
            all_summaries.append("- Slack token doesn't have correct permissions\n")
            all_summaries.append("- Token might be a Bot token instead of User token\n\n")
            all_summaries.append("**Debug Log:**\n```\n")
            all_summaries.append('\n'.join(self.debug_log))
            all_summaries.append("\n```\n")
            
            # Still save the file!
            output_dir = os.path.dirname(output_file)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)
            with open(output_file, 'w', encoding='utf-8') as f:
                f.writelines(all_summaries)
            self.log(f"✅ Debug file saved to: {output_file}")
            return output_file
        
        channels_processed = 0
        total_messages = 0
        
        for idx, channel in enumerate(channels, 1):
            channel_name = channel['name']
            channel_id = channel['id']
            
            self.log(f"\n[{idx}/{len(channels)}] Processing #{channel_name}...")
            
            messages = self.fetch_messages(channel_id, days_back)
            
            if not messages:
                self.log(f"  ⚠️  No messages found in last {days_back} days")
                all_summaries.append(f"## #{channel_name}\n\n")
                all_summaries.append(f"⚠️ No messages in the last {days_back} days\n\n")
                all_summaries.append("---\n\n")
                continue
            
            self.log(f"  ✅ Found {len(messages)} total messages")
            
            formatted_text = self.format_messages(messages, channel_name, channel_id)
            
            if not formatted_text or len(formatted_text) < 50:
                self.log(f"  ⏭️  Skipping - no meaningful content after filtering")
                all_summaries.append(f"## #{channel_name}\n\n")
                all_summaries.append(f"**Messages found:** {len(messages)} (all were system messages)\n\n")
                all_summaries.append("---\n\n")
                continue
            
            self.log(f"  📝 Formatted content length: {len(formatted_text)} characters")
            
            if len(formatted_text) > 25000:
                self.log(f"  ⚠️  Truncating content (too long for API)")
                formatted_text = formatted_text[:25000] + "\n\n... (content truncated for API limits)"
            
            self.log(f"  🤖 Generating AI summary...")
            summary = self.summarize_with_gemini(formatted_text, channel_name, len(messages))
            
            all_summaries.append(f"## #{channel_name}\n\n")
            all_summaries.append(f"**Message Count:** {len(messages)}\n\n")
            all_summaries.append(f"{summary}\n\n")
            all_summaries.append("---\n\n")
            
            channels_processed += 1
            total_messages += len(messages)
            
            time.sleep(3)
        
        # Summary statistics
        all_summaries.append(f"\n## 📈 Summary Statistics\n\n")
        all_summaries.append(f"- **Total Channels Found:** {len(channels)}\n")
        all_summaries.append(f"- **Channels Processed:** {channels_processed}\n")
        all_summaries.append(f"- **Total Messages Analyzed:** {total_messages}\n")
        all_summaries.append(f"- **Average Messages per Channel:** {total_messages/channels_processed if channels_processed > 0 else 0:.1f}\n\n")
        
        # Add debug log at the end
        all_summaries.append(f"## 🔍 Debug Log\n\n```\n")
        all_summaries.append('\n'.join(self.debug_log[-50:]))  # Last 50 log entries
        all_summaries.append("\n```\n")
        
        # FORCE file creation
        output_dir = os.path.dirname(output_file)
        self.log(f"\n📝 Saving summary to: {output_file}")
        self.log(f"   Output directory: {output_dir or 'current directory'}")
        
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            self.log(f"   ✅ Directory created/verified")
        
        self.log(f"   Writing {len(all_summaries)} lines to file...")
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(all_summaries)
        
        # Verify
        if os.path.exists(output_file):
            file_size = os.path.getsize(output_file)
            self.log(f"   ✅ File created! Size: {file_size} bytes")
            
            # Print first few lines
            with open(output_file, 'r') as f:
                preview = f.read(500)
            self.log(f"\n📄 File preview:\n{preview}...")
        else:
            self.log(f"   ❌ ERROR: File was NOT created!")
            self.log(f"   Current working directory: {os.getcwd()}")
            self.log(f"   Files in current directory: {os.listdir('.')}")
        
        self.log(f"\n{'='*60}")
        self.log(f"✅ Process completed!")
        self.log(f"📊 Processed {channels_processed} channels with {total_messages} total messages")
        self.log(f"{'='*60}\n")
        
        return output_file

if __name__ == "__main__":
    try:
        summarizer = SlackSummarizer()
        result = summarizer.run(days_back=30)  # Changed to 30 days for better chance of finding messages
        print(f"\n✅ SUCCESS! Summary file: {result}")
    except Exception as e:
        print(f"\n❌ FATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        
        # Create error file
        os.makedirs('summaries', exist_ok=True)
        with open('summaries/weekly_summary.md', 'w') as f:
            f.write(f"# ❌ Error Running Summarizer\n\n")
            f.write(f"**Error:** {str(e)}\n\n")
            f.write(f"**Traceback:**\n```\n{traceback.format_exc()}\n```\n")
        print("Error details saved to summaries/weekly_summary.md")
