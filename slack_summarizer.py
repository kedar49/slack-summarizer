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
        
    def get_channels(self):
        """Get list of all channels the user is a member of"""
        try:
            response = self.client.conversations_list(
                types="public_channel,private_channel",
                exclude_archived=True
            )
            return response['channels']
        except SlackApiError as e:
            print(f"Error fetching channels: {e}")
            return []
    
    def get_user_name(self, user_id):
        """Get user's display name"""
        try:
            response = self.client.users_info(user=user_id)
            user = response['user']
            return user['profile'].get('display_name') or user['profile'].get('real_name') or user['name']
        except SlackApiError:
            return user_id
    
    def fetch_messages(self, channel_id, days_back=7):
        """Fetch messages from the last N days"""
        oldest = (datetime.now() - timedelta(days=days_back)).timestamp()
        messages = []
        
        try:
            response = self.client.conversations_history(
                channel=channel_id,
                oldest=str(oldest),
                limit=1000
            )
            messages = response['messages']
            
            while response.get('has_more'):
                response = self.client.conversations_history(
                    channel=channel_id,
                    oldest=str(oldest),
                    cursor=response['response_metadata']['next_cursor'],
                    limit=1000
                )
                messages.extend(response['messages'])
                
        except SlackApiError as e:
            print(f"Error fetching messages: {e}")
        
        return messages
    
    def format_messages(self, messages, channel_name):
        """Format messages into readable text with user names"""
        if not messages:
            return None
            
        formatted = f"# Slack Channel: {channel_name}\n\n"
        user_cache = {}
        
        messages.sort(key=lambda x: float(x['ts']))
        
        for msg in messages:
            if msg.get('subtype') in ['channel_join', 'channel_leave']:
                continue
                
            user_id = msg.get('user', 'Unknown')
            if user_id not in user_cache:
                user_cache[user_id] = self.get_user_name(user_id)
            
            username = user_cache[user_id]
            text = msg.get('text', '')
            timestamp = datetime.fromtimestamp(float(msg['ts'])).strftime('%Y-%m-%d %H:%M')
            
            formatted += f"[{timestamp}] {username}: {text}\n"
            
            if msg.get('thread_ts') and msg.get('reply_count', 0) > 0:
                formatted += "  (Thread discussion)\n"
        
        return formatted
    
    def summarize_with_gemini(self, text, channel_name):
        """Summarize text using Gemini API with retry logic"""
        prompt = f"""Summarize the following Slack channel discussion concisely. 
Focus on:
- Key topics discussed
- Important decisions or action items
- Notable announcements
- Main participants and their contributions

Channel: {channel_name}

Discussion:
{text}

Provide a structured summary with sections for Topics, Decisions, Action Items, and Key Highlights."""

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.model.generate_content(prompt)
                return response.text
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"Retry {attempt + 1}/{max_retries} after {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    return f"Error generating summary: {str(e)}"
    
    def run(self, days_back=7, output_file='summaries/weekly_summary.md'):
        """Main execution function"""
        print(f"Starting Slack summarization for last {days_back} days...")
        
        channels = self.get_channels()
        print(f"Found {len(channels)} channels")
        
        all_summaries = []
        date_range = f"{(datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')} to {datetime.now().strftime('%Y-%m-%d')}"
        
        all_summaries.append(f"# Slack Weekly Summary\n")
        all_summaries.append(f"**Period:** {date_range}\n")
        all_summaries.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        all_summaries.append("---\n\n")
        
        for channel in channels:
            channel_name = channel['name']
            channel_id = channel['id']
            
            print(f"Processing #{channel_name}...")
            
            messages = self.fetch_messages(channel_id, days_back)
            
            if not messages:
                print(f"  No messages in #{channel_name}")
                continue
            
            print(f"  Found {len(messages)} messages")
            
            formatted_text = self.format_messages(messages, channel_name)
            
            if len(formatted_text) < 100:
                print(f"  Skipping #{channel_name} - minimal activity")
                continue
            
            if len(formatted_text) > 30000:
                formatted_text = formatted_text[:30000] + "\n... (truncated)"
            
            summary = self.summarize_with_gemini(formatted_text, channel_name)
            
            all_summaries.append(f"## #{channel_name}\n\n")
            all_summaries.append(f"**Messages:** {len(messages)}\n\n")
            all_summaries.append(f"{summary}\n\n")
            all_summaries.append("---\n\n")
            
            time.sleep(2)
        
        os.makedirs(os.path.dirname(output_file), exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.writelines(all_summaries)
        
        print(f"\nSummary saved to {output_file}")
        return output_file

if __name__ == "__main__":
    summarizer = SlackSummarizer()
    summarizer.run(days_back=7)
